"""Internal discovery review queue tests use synthetic source candidates only."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.discovery_store import DiscoveryStore, DiscoveryStoreError
from backend.knowledge_base import load_services
from backend.official_sources import OfficialSourceCandidate, OfficialSourceResult
from backend import pipeline
from backend.official_sources import OfficialSourceRetriever


def discovery(**changes):
    value = {
        "title": "Synthetic source result",
        "url": "https://official.example.test/record",
        "retrieved_at": "2026-10-30T12:00:00Z",
        "jurisdiction": "Synthetic test jurisdiction",
        "excerpt": "Synthetic excerpt only; no public facts.",
        "verification_status": "unverified",
        "claim_key": "synthetic-field",
    }
    value.update(changes)
    return value


def test_store_and_retrieve_preserves_candidate_provenance(tmp_path):
    path = tmp_path / "discoveries.json"
    store = DiscoveryStore(path)
    added = store.store_candidates([discovery()])[0]
    loaded = DiscoveryStore(path).get(added.discovery_id)

    assert path.exists()
    assert loaded.title == "Synthetic source result"
    assert loaded.url == "https://official.example.test/record"
    assert loaded.retrieved_at == datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    assert loaded.jurisdiction == "Synthetic test jurisdiction"
    assert loaded.excerpt == "Synthetic excerpt only; no public facts."
    assert loaded.verification_status == "unverified"
    assert loaded.review_state == "pending_review"
    assert loaded.review_notes is None


def test_ingest_only_stores_successful_unverified_retrieval_results(tmp_path):
    store = DiscoveryStore(tmp_path / "discoveries.json")
    candidate = OfficialSourceCandidate.model_validate(discovery())
    stored = store.ingest_result(OfficialSourceResult(status="ok", candidates=[candidate]))
    assert len(stored) == 1
    assert store.ingest_result(OfficialSourceResult(status="no_results")) == []
    assert len(store.list()) == 1


def test_retrieval_pipeline_can_persist_discoveries_separately(monkeypatch, tmp_path):
    class MockProvider:
        def search(self, query, jurisdiction, *, timeout_seconds):
            assert timeout_seconds > 0
            return [discovery()]

    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {})
    store = DiscoveryStore(tmp_path / "discoveries.json")
    retriever = OfficialSourceRetriever(
        MockProvider(), approved_hosts=["official.example.test"]
    )
    outcome = pipeline.retrieve_with_official_fallback(
        "sample_service",
        "synthetic request",
        official_source_retriever=retriever,
        discovery_store=store,
    )
    assert outcome.trusted_entry is None
    assert outcome.discoveries.status == "ok"
    assert len(outcome.stored_discoveries) == 1
    assert outcome.stored_discoveries[0].review_state == "pending_review"
    assert store.get(outcome.stored_discoveries[0].discovery_id).verification_status == "unverified"
    assert not (tmp_path / "services.json").exists()


def test_human_review_can_verify_candidate_without_promoting_it(tmp_path):
    store = DiscoveryStore(tmp_path / "discoveries.json")
    stored = store.store_candidates([discovery()])[0]
    reviewed = store.review(stored.discovery_id, "verified", notes="Synthetic review note.")

    assert reviewed.review_state == "verified"
    assert reviewed.verification_status == "verified"
    assert reviewed.review_notes == "Synthetic review note."
    assert reviewed.reviewed_at is not None
    assert store.list("verified") == [reviewed]
    trusted_path = tmp_path / "services.json"
    trusted_path.write_text(json.dumps({
        "_metadata": {"kind": "verified_catalog"},
        "services": [],
        "discoveries": [],
    }), encoding="utf-8")
    assert load_services(trusted_path) == {}


def test_rejected_candidate_is_retained_with_review_notes(tmp_path):
    store = DiscoveryStore(tmp_path / "discoveries.json")
    stored = store.store_candidates([discovery()])[0]
    rejected = store.review(stored.discovery_id, "rejected", notes="Synthetic rejection reason.")
    assert rejected.review_state == "rejected"
    assert rejected.verification_status == "rejected"
    assert rejected.review_notes == "Synthetic rejection reason."
    assert store.list("pending_review") == []


def test_duplicate_candidate_deduplicates_and_preserves_review_state(tmp_path):
    store = DiscoveryStore(tmp_path / "discoveries.json")
    first = store.store_candidates([discovery()])[0]
    verified = store.review(first.discovery_id, "verified", notes="Synthetic review.")
    duplicate = store.store_candidates([discovery(retrieved_at="2026-10-31T12:00:00Z")])[0]

    assert duplicate.discovery_id == first.discovery_id
    assert duplicate.review_state == "verified"
    assert duplicate.verification_status == "verified"
    assert duplicate.retrieved_at > verified.retrieved_at
    assert len(store.list()) == 1


def test_conflicting_excerpts_are_linked_for_review_without_selecting_a_winner(tmp_path):
    store = DiscoveryStore(tmp_path / "discoveries.json")
    first, second = store.store_candidates([
        discovery(url="https://official.example.test/one", excerpt="Synthetic value is 10."),
        discovery(url="https://official.example.test/two", excerpt="Synthetic value is 20."),
    ])
    assert second.discovery_id in first.conflicts_with
    assert first.discovery_id in second.conflicts_with
    assert first.review_state == second.review_state == "pending_review"


def test_records_before_explicit_cutoff_become_stale(tmp_path):
    store = DiscoveryStore(tmp_path / "discoveries.json")
    pending = store.store_candidates([discovery()])[0]
    rejected = store.store_candidates([discovery(url="https://official.example.test/rejected")])[0]
    store.review(rejected.discovery_id, "rejected", notes="Synthetic reason.")

    stale = store.mark_stale_before(datetime(2026, 10, 31, tzinfo=timezone.utc), note="Synthetic freshness policy.")
    assert [item.discovery_id for item in stale] == [pending.discovery_id]
    assert store.get(pending.discovery_id).review_state == "stale"
    assert store.get(pending.discovery_id).verification_status == "stale"
    assert store.get(rejected.discovery_id).review_state == "rejected"


@pytest.mark.parametrize("missing", ["title", "url", "retrieved_at", "jurisdiction", "excerpt"])
def test_missing_provenance_is_rejected_without_storage(tmp_path, missing):
    value = discovery()
    del value[missing]
    store = DiscoveryStore(tmp_path / "discoveries.json")
    with pytest.raises(DiscoveryStoreError, match="missing or invalid provenance"):
        store.store_candidates([value])
    assert store.list() == []


@pytest.mark.parametrize("url", ["http://official.example.test/x", "/relative", "javascript:alert(1)"])
def test_invalid_candidate_urls_are_rejected(tmp_path, url):
    store = DiscoveryStore(tmp_path / "discoveries.json")
    with pytest.raises(DiscoveryStoreError):
        store.store_candidates([discovery(url=url)])


def test_review_requires_note_and_unknown_ids_fail_safely(tmp_path):
    store = DiscoveryStore(tmp_path / "discoveries.json")
    stored = store.store_candidates([discovery()])[0]
    with pytest.raises(DiscoveryStoreError, match="require a review note"):
        store.review(stored.discovery_id, "verified")
    with pytest.raises(DiscoveryStoreError, match="not found"):
        store.review("missing-id", "rejected", notes="Synthetic note")


def test_malformed_review_file_fails_with_sanitized_error(tmp_path):
    path = tmp_path / "discoveries.json"
    path.write_text(json.dumps({"schema_version": 1, "discoveries": [{"url": "bad"}]}), encoding="utf-8")
    with pytest.raises(DiscoveryStoreError, match="malformed"):
        DiscoveryStore(path).list()
