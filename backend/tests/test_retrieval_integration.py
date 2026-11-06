"""Pipeline integration for trusted data, official-source candidates, and review storage."""

from datetime import datetime, timezone

import pytest

import backend.pipeline as pipeline
from backend.discovery_store import DiscoveryStore
from backend.knowledge_base import ServiceRecord
from backend.official_sources import OfficialSourceRetriever
from backend.schemas import AskRequest, SupportedResponse, UnsupportedResponse


def service_record(*, fee_status="verified"):
    amount = 0 if fee_status == "verified" else None
    return ServiceRecord.model_validate({
        "id": "sample_access",
        "name": "Synthetic sample service",
        "aliases": ["sample service"],
        "task_types": ["application_procedure", "fee"],
        "steps": {"en": ["Synthetic verified step." ]},
        "documents": [{"name": "Sample record", "status": "conditional", "note": None}],
        "fee": {"amount": amount, "currency": "INR", "status": fee_status, "note": None},
        "online_option": {"available": None, "url": None, "status": "not_verified"},
        "office_type": None,
        "grievance_route": None,
        "sources": [{"title": "Synthetic source", "url": "https://trusted.example.test/source"}],
        "last_verified": None,
    })


def candidate(url="https://official.example.test/source", excerpt="Synthetic source excerpt."):
    return {
        "title": "Synthetic official result",
        "url": url,
        "retrieved_at": datetime.now(timezone.utc),
        "jurisdiction": "Synthetic test jurisdiction",
        "excerpt": excerpt,
        "claim_key": "synthetic_claim",
    }


class MockProvider:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = 0
        self.queries = []

    def search(self, query, jurisdiction, *, timeout_seconds):
        self.calls += 1
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results


def source_retriever(provider):
    return OfficialSourceRetriever(provider, approved_hosts=["official.example.test"])


def prepare_request(monkeypatch, record, question, task):
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {record.id: record})
    monkeypatch.setattr(pipeline, "classify_intent", lambda *_: record.id)
    monkeypatch.setattr(pipeline, "classify_task_type", lambda *_: task)
    monkeypatch.setattr(pipeline, "compose_steps", lambda entry, language: entry.steps[language])
    return AskRequest(question=question, language="en")


def test_complete_bundled_data_skips_provider_and_returns_trusted_response(monkeypatch, tmp_path):
    record = service_record()
    request = prepare_request(monkeypatch, record, "how to apply for sample service", "application_procedure")
    provider = MockProvider([candidate()])
    result = pipeline.answer_request(
        request,
        official_source_retriever=source_retriever(provider),
        discovery_store=DiscoveryStore(tmp_path / "discoveries.json"),
    )
    assert isinstance(result, SupportedResponse)
    assert result.steps == record.steps["en"]
    assert provider.calls == 0
    assert not (tmp_path / "discoveries.json").exists()


def test_incomplete_trusted_data_retrieves_and_persists_unverified_candidate(monkeypatch, tmp_path):
    record = service_record(fee_status="not_verified")
    request = prepare_request(
        monkeypatch, record, "what is the fee for sample service, contact me at citizen@example.test", "fee"
    )
    provider = MockProvider([candidate()])
    store = DiscoveryStore(tmp_path / "discoveries.json")
    result = pipeline.answer_request(
        request, official_source_retriever=source_retriever(provider), discovery_store=store
    )
    assert isinstance(result, SupportedResponse)
    assert provider.calls == 1
    assert provider.queries == ["sample access fee"]
    assert "citizen@example.test" not in provider.queries[0]
    assert result.fee.amount is None and result.fee.status == "not_verified"
    assert any("unverified" in warning for warning in result.warnings)
    saved = store.list()
    assert len(saved) == 1
    assert saved[0].review_state == "pending_review"
    assert saved[0].verification_status == "unverified"
    assert saved[0].title == "Synthetic official result"
    assert saved[0].url == "https://official.example.test/source"
    assert saved[0].jurisdiction == "Synthetic test jurisdiction"
    assert saved[0].excerpt == "Synthetic source excerpt."


def test_conflicting_sources_are_persisted_but_not_used_as_facts(monkeypatch, tmp_path):
    record = service_record(fee_status="not_verified")
    request = prepare_request(monkeypatch, record, "what is the fee for sample service", "fee")
    provider = MockProvider([
        candidate("https://official.example.test/one", "Synthetic fee is 5."),
        candidate("https://official.example.test/two", "Synthetic fee is 8."),
    ])
    store = DiscoveryStore(tmp_path / "discoveries.json")
    result = pipeline.answer_request(
        request, official_source_retriever=source_retriever(provider), discovery_store=store
    )
    assert isinstance(result, SupportedResponse)
    assert result.fee.amount is None
    assert any("conflict" in warning for warning in result.warnings)
    stored = store.list()
    assert len(stored) == 2
    assert all(item.conflicts_with for item in stored)
    assert all(item.verification_status == "unverified" for item in stored)


def test_provider_failure_is_partial_and_sanitized(monkeypatch, tmp_path):
    record = service_record(fee_status="not_verified")
    request = prepare_request(monkeypatch, record, "what is the fee for sample service", "fee")
    provider = MockProvider(error=RuntimeError("sensitive provider detail"))
    result = pipeline.answer_request(
        request,
        official_source_retriever=source_retriever(provider),
        discovery_store=DiscoveryStore(tmp_path / "discoveries.json"),
    )
    assert isinstance(result, SupportedResponse)
    assert result.fee.amount is None
    assert any("unavailable" in warning for warning in result.warnings)
    assert "sensitive provider detail" not in " ".join(result.warnings)
    assert not (tmp_path / "discoveries.json").exists()


def test_no_provider_keeps_trusted_partial_response_and_does_not_persist(monkeypatch, tmp_path):
    record = service_record(fee_status="not_verified")
    request = prepare_request(monkeypatch, record, "what is the fee for sample service", "fee")
    result = pipeline.answer_request(request, discovery_store=DiscoveryStore(tmp_path / "discoveries.json"))
    assert isinstance(result, SupportedResponse)
    assert result.fee.amount is None
    assert not (tmp_path / "discoveries.json").exists()


def test_missing_trusted_record_with_source_candidate_remains_unsupported(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {})
    monkeypatch.setattr(pipeline, "classify_intent", lambda *_: "aadhaar_update")
    monkeypatch.setattr(pipeline, "classify_task_type", lambda *_: "application_procedure")
    provider = MockProvider([candidate()])
    store = DiscoveryStore(tmp_path / "discoveries.json")
    result = pipeline.answer_request(
        AskRequest(question="how to apply for aadhaar update", language="en"),
        official_source_retriever=source_retriever(provider),
        discovery_store=store,
    )
    assert isinstance(result, UnsupportedResponse)
    assert result.service_id == "unknown"
    assert any("unverified" in warning for warning in result.warnings)
    assert len(store.list()) == 1
