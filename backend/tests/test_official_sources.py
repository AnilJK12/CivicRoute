"""Official-source discovery adapter tests use mocked providers only."""

from datetime import datetime, timezone

import pytest

import backend.pipeline as pipeline
from backend.knowledge_base import ServiceRecord
from backend.official_sources import OfficialSourceRetriever


STAMP = "2026-10-30T10:15:00Z"


def candidate(**changes):
    data = {
        "title": "Synthetic official-source result",
        "url": "https://official.example.test/guide",
        "retrieved_at": STAMP,
        "jurisdiction": "Synthetic test jurisdiction",
        "excerpt": "Synthetic excerpt for adapter testing.",
        "claim_key": "sample_claim",
    }
    data.update(changes)
    return data


class MockProvider:
    def __init__(self, results=None, error=None):
        self.results = results if results is not None else []
        self.error = error
        self.calls = 0

    def search(self, query, jurisdiction, *, timeout_seconds):
        self.calls += 1
        assert timeout_seconds > 0
        if self.error:
            raise self.error
        return self.results


def retriever(provider, timeout=0.05):
    return OfficialSourceRetriever(
        provider, approved_hosts=["official.example.test"], timeout_seconds=timeout
    )


def test_successful_retrieval_preserves_provenance_and_marks_discovery_unverified():
    provider = MockProvider([candidate()])
    result = retriever(provider).retrieve("synthetic request", jurisdiction="Test")
    assert result.status == "ok"
    item = result.candidates[0]
    assert item.title == "Synthetic official-source result"
    assert item.url == "https://official.example.test/guide"
    assert item.retrieved_at == datetime(2026, 10, 30, 10, 15, tzinfo=timezone.utc)
    assert item.jurisdiction == "Synthetic test jurisdiction"
    assert item.excerpt == "Synthetic excerpt for adapter testing."
    assert item.verification_status == "unverified"
    assert provider.calls == 1


def test_no_results_is_a_controlled_empty_result():
    result = retriever(MockProvider([])).retrieve("synthetic request")
    assert result.status == "no_results"
    assert result.candidates == []


@pytest.mark.parametrize("results", [
    {"unexpected": "mapping"},
    [candidate(url="http://official.example.test/insecure")],
    [candidate(url="https://not-approved.example.test/page")],
    [candidate(verification_status="verified")],
])
def test_malformed_or_unapproved_results_are_rejected_without_details(results):
    result = retriever(MockProvider(results)).retrieve("synthetic request")
    assert result.status == "invalid_results"
    assert result.candidates == []
    assert "Synthetic official-source result" not in " ".join(result.warnings)


def test_conflicting_excerpts_are_flagged_without_selecting_a_winner():
    provider = MockProvider([
        candidate(url="https://official.example.test/one", excerpt="Synthetic amount is 5."),
        candidate(url="https://official.example.test/two", excerpt="Synthetic amount is 8."),
    ])
    result = retriever(provider).retrieve("synthetic request")
    assert result.status == "ok"
    assert len(result.conflicts) == 1
    assert result.conflicts[0].candidate_indices == [0, 1]
    assert all(item.verification_status == "unverified" for item in result.candidates)


@pytest.mark.parametrize("missing", ["title", "url", "retrieved_at", "jurisdiction", "excerpt"])
def test_missing_provenance_prevents_candidate_return(missing):
    data = candidate()
    del data[missing]
    result = retriever(MockProvider([data])).retrieve("synthetic request")
    assert result.status == "invalid_results"
    assert result.candidates == []


@pytest.mark.parametrize("provider", [
    MockProvider(error=TimeoutError()),
    MockProvider(error=RuntimeError("synthetic provider failure")),
])
def test_provider_timeout_or_failure_is_sanitized(provider):
    result = retriever(provider).retrieve("synthetic request")
    assert result.status == "unavailable"
    assert result.candidates == []
    assert "synthetic provider failure" not in " ".join(result.warnings)


def _trusted_entry():
    return ServiceRecord.model_validate({
        "id": "sample_access",
        "name": "Synthetic trusted entry",
        "aliases": ["synthetic request"],
        "steps": {"en": ["Synthetic verified step."]},
        "documents": [],
        "fee": {"amount": None, "currency": "INR", "status": "not_verified", "note": None},
        "online_option": {"available": None, "url": None, "status": "not_verified"},
        "office_type": None,
        "grievance_route": None,
        "sources": [{"title": "Synthetic trusted source", "url": "https://trusted.example.test/source"}],
        "last_verified": None,
    })


def test_trusted_bundled_entry_skips_official_source_provider(monkeypatch):
    entry = _trusted_entry()
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {entry.id: entry})
    provider = MockProvider([candidate()])
    outcome = pipeline.retrieve_with_official_fallback(
        entry.id,
        "synthetic request",
        official_source_retriever=retriever(provider),
    )
    assert outcome.trusted_entry is entry
    assert outcome.discoveries is None
    assert outcome.unsupported is None
    assert provider.calls == 0


def test_missing_trusted_entry_uses_discovery_without_promotion(monkeypatch):
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {})
    provider = MockProvider([candidate()])
    outcome = pipeline.retrieve_with_official_fallback(
        "sample_access",
        "synthetic request",
        official_source_retriever=retriever(provider),
    )
    assert outcome.trusted_entry is None
    assert outcome.discoveries.status == "ok"
    assert outcome.discoveries.candidates[0].verification_status == "unverified"
    assert outcome.unsupported.service_id == "unknown"


def test_incomplete_trusted_entry_can_trigger_discovery_without_replacement(monkeypatch):
    entry = _trusted_entry()
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {entry.id: entry})
    provider = MockProvider([candidate()])
    outcome = pipeline.retrieve_with_official_fallback(
        entry.id,
        "synthetic missing detail",
        information_complete=False,
        official_source_retriever=retriever(provider),
    )
    assert outcome.trusted_entry is entry
    assert outcome.discoveries.status == "ok"
    assert outcome.discoveries.candidates[0].verification_status == "unverified"
    assert outcome.unsupported is None
