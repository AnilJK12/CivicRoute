"""Promotion tests use temporary catalogues and synthetic non-government fixtures only."""

import json
from datetime import date
from pathlib import Path

import pytest

from backend.catalog_promotion import promote_bundle_file
from backend.knowledge_base import ServiceRecord


def bundle_service(**changes):
    source_url = "https://source.example.test/record"
    record = {
        "id": "sample_service",
        "name": "Synthetic Sample Service",
        "aliases": ["Synthetic Alias"],
        "department": "Synthetic Department",
        "responsible_authority": "Synthetic Authority",
        "category": "Synthetic Category",
        "application_procedure": {
            "status": "verified",
            "value": [{"order": 2, "text": "Synthetic second step."}, {"order": 1, "text": "Synthetic first step."}],
            "source_urls": [source_url],
        },
        "documents": {
            "status": "verified",
            "value": [{"name": "Synthetic conditional item", "status": "conditional"},
                      {"name": "Synthetic commonly requested item", "status": "commonly_requested"}],
            "source_urls": [source_url],
        },
        "fees": {
            "status": "verified", "value": {"amount": 0, "currency": "INR"},
            "source_urls": [source_url],
        },
        "online_application_links": {
            "status": "verified", "value": [{"title": "Synthetic link", "url": "https://portal.example.test/apply"}],
            "source_urls": [source_url],
        },
        "service_channels": ["online"],
        "jurisdiction": {"state": "Synthetic State", "district": "Synthetic District"},
        "sources": [{"title": "Synthetic source title", "url": source_url,
                     "retrieved_at": "2026-10-20T10:15:00Z", "publisher": "Synthetic publisher"}],
        "verification_status": "verified",
        "last_verified": "2026-10-20",
    }
    record.update(changes)
    return record


def write_bundle(path: Path, services=None, centres=None):
    path.write_text(json.dumps({"schema_version": 1, "services": services or [], "centres": centres or []}), encoding="utf-8")


def catalog_file(path: Path, services=None, discoveries=None):
    path.write_text(json.dumps({
        "_metadata": {"kind": "verified_catalog"},
        "services": services or [],
        "discoveries": discoveries or [],
    }, indent=2), encoding="utf-8")


def promote(bundle_path, catalog_path, ids=None, *, today=date(2026, 10, 30)):
    return promote_bundle_file(
        bundle_path, approved_ids=set(ids or []), reviewer="synthetic reviewer",
        approval_note="Synthetic review approval for a unit test.",
        catalog_path=catalog_path, today=today,
    )


def test_record_requires_explicit_approval_even_when_input_claims_verified(tmp_path):
    catalog = tmp_path / "services.json"
    bundle = tmp_path / "bundle.json"
    catalog_file(catalog)
    original = catalog.read_bytes()
    write_bundle(bundle, [bundle_service()])

    report = promote(bundle, catalog)

    assert report.skipped[0].reason == "Explicit record-level approval is missing."
    assert catalog.read_bytes() == original


def test_missing_fact_provenance_and_stale_data_require_review(tmp_path):
    catalog, bundle = tmp_path / "services.json", tmp_path / "bundle.json"
    catalog_file(catalog)
    record = bundle_service(
        fees={"status": "verified", "value": {"amount": 10, "currency": "INR"}, "source_urls": []},
        last_verified="2024-01-01",
    )
    write_bundle(bundle, [record])
    report = promote(bundle, catalog, {"sample_service"})
    assert len(report.review_required) == 1
    assert "source record" in report.review_required[0].reason
    assert catalog.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("services.json.backup-*"))


def test_duplicate_service_ids_are_not_promoted(tmp_path):
    catalog, bundle = tmp_path / "services.json", tmp_path / "bundle.json"
    catalog_file(catalog)
    write_bundle(bundle, [bundle_service(), bundle_service(name="Conflicting Synthetic Name")])
    report = promote(bundle, catalog, {"sample_service"})
    assert len(report.review_required) == 2
    assert "duplicate service ID" in report.review_required[0].reason
    assert json.loads(catalog.read_text(encoding="utf-8"))["services"] == []


def test_existing_conflicting_trusted_id_is_rejected_not_overwritten(tmp_path):
    catalog, bundle = tmp_path / "services.json", tmp_path / "bundle.json"
    existing = ServiceRecord.model_validate({
        "id": "sample_service", "name": "Existing Synthetic Trusted Name",
        "steps": {"en": ["Existing trusted step."]}, "documents": [],
        "fee": {"amount": None, "currency": "INR", "status": "not_verified", "note": None},
        "online_option": {"available": None, "url": None, "status": "not_verified"},
        "office_type": None, "grievance_route": None,
        "sources": [{"title": "Existing source", "url": "https://existing.example.test/source"}],
        "last_verified": None,
    })
    catalog_file(catalog, [existing.model_dump(mode="json")])
    original = catalog.read_bytes()
    write_bundle(bundle, [bundle_service()])
    report = promote(bundle, catalog, {"sample_service"})
    assert len(report.rejected) == 1
    assert "different trusted facts" in report.rejected[0].reason
    assert catalog.read_bytes() == original


def test_promotion_is_atomic_preserves_existing_records_and_repeats_idempotently(tmp_path):
    catalog, bundle = tmp_path / "services.json", tmp_path / "bundle.json"
    existing = ServiceRecord.model_validate({
        "id": "existing_service", "name": "Existing Synthetic Service",
        "steps": {"en": ["Existing trusted step."]}, "documents": [],
        "fee": {"amount": None, "currency": "INR", "status": "not_verified", "note": None},
        "online_option": {"available": None, "url": None, "status": "not_verified"},
        "office_type": None, "grievance_route": None,
        "sources": [{"title": "Existing source", "url": "https://existing.example.test/source"}],
        "last_verified": None,
    })
    catalog_file(catalog, [existing.model_dump(mode="json")], discoveries=[{"keep": "pending discovery"}])
    write_bundle(bundle, [bundle_service()])

    first = promote(bundle, catalog, {"sample_service"})
    written = json.loads(catalog.read_text(encoding="utf-8"))
    backup = Path(first.backup_path)
    assert first.imported[0].record_id == "sample_service"
    assert backup.read_bytes() != b""
    assert json.loads(backup.read_text(encoding="utf-8"))["services"][0]["id"] == "existing_service"
    assert [item["id"] for item in written["services"]] == ["existing_service", "sample_service"]
    assert written["discoveries"] == [{"keep": "pending discovery"}]
    promoted = next(item for item in written["services"] if item["id"] == "sample_service")
    assert promoted["sources"][0]["title"] == "Synthetic source title"
    assert promoted["sources"][0]["accessed_on"] == "2026-10-20"
    assert promoted["bundle_data"]["sources"][0]["retrieved_at"] == "2026-10-20T10:15:00Z"
    assert promoted["promotion_approval"]["reviewer"] == "synthetic reviewer"
    assert promoted["documents"][0]["status"] == "conditional"
    assert promoted["documents"][1]["status"] == "commonly_requested"
    assert promoted["steps"]["en"] == ["Synthetic first step.", "Synthetic second step."]

    second = promote(bundle, catalog, {"sample_service"})
    assert second.skipped[0].reason.startswith("Identical trusted record already exists")
    assert second.backup_path is None
    assert len(json.loads(catalog.read_text(encoding="utf-8"))["services"]) == 2


def test_unknown_fees_stay_unverified_and_verified_unavailable_stays_distinct(tmp_path):
    catalog, bundle = tmp_path / "services.json", tmp_path / "bundle.json"
    catalog_file(catalog)
    record = bundle_service(fees={
        "status": "verified_unavailable", "value": None,
        "note": "Synthetic source explicitly has no fee value.",
        "source_urls": ["https://source.example.test/record"],
    })
    write_bundle(bundle, [record])
    report = promote(bundle, catalog, {"sample_service"})
    assert report.imported
    promoted = json.loads(catalog.read_text(encoding="utf-8"))["services"][0]
    assert promoted["fee"]["amount"] is None
    assert promoted["fee"]["status"] == "not_verified"
    assert promoted["bundle_data"]["fees"]["status"] == "verified_unavailable"


def test_centre_capability_without_trusted_service_is_not_promoted_as_authorized(tmp_path):
    catalog, bundle = tmp_path / "services.json", tmp_path / "bundle.json"
    catalog_file(catalog)
    centre = {
        "id": "synthetic_centre", "name": "Synthetic Centre",
        "service_capabilities": [{"service_id": "sample_service", "authorization_status": "verified",
                                   "source_urls": ["https://source.example.test/record"]}],
        "sources": [{"title": "Synthetic source", "url": "https://source.example.test/record",
                     "retrieved_at": "2026-10-20T10:00:00Z"}],
        "verification_status": "verified", "last_verified": "2026-10-20",
    }
    write_bundle(bundle, [bundle_service()], [centre])
    report = promote(bundle, catalog, {"sample_service", "synthetic_centre"})
    assert report.review_required[0].record_type == "centre"
    assert "service IDs absent" in report.review_required[0].reason
    assert "centres" not in json.loads(catalog.read_text(encoding="utf-8"))


def test_approved_centre_with_verified_capability_provenance_is_imported(tmp_path):
    catalog, bundle = tmp_path / "services.json", tmp_path / "bundle.json"
    trusted_service = ServiceRecord.model_validate({
        "id": "sample_service", "name": "Trusted Synthetic Service",
        "steps": {"en": ["Trusted step."]}, "documents": [],
        "fee": {"amount": None, "currency": "INR", "status": "not_verified", "note": None},
        "online_option": {"available": None, "url": None, "status": "not_verified"},
        "office_type": None, "grievance_route": None,
        "sources": [{"title": "Existing source", "url": "https://existing.example.test/source"}],
        "last_verified": None,
    })
    catalog_file(catalog, [trusted_service.model_dump(mode="json")])
    centre = {
        "id": "synthetic_centre", "name": "Synthetic Centre", "provider_type": "Synthetic provider",
        "district": "Synthetic District", "service_capabilities": [{
            "service_id": "sample_service", "authorization_status": "verified",
            "source_urls": ["https://source.example.test/record"],
        }],
        "sources": [{"title": "Synthetic source", "url": "https://source.example.test/record",
                     "retrieved_at": "2026-10-20T10:00:00Z"}],
        "verification_status": "verified", "last_verified": "2026-10-20",
    }
    write_bundle(bundle, centres=[centre])
    report = promote(bundle, catalog, {"synthetic_centre"})
    assert report.imported[0].record_type == "centre"
    imported = json.loads(catalog.read_text(encoding="utf-8"))["centres"][0]
    assert imported["service_capabilities"][0]["authorization_status"] == "verified"
    assert imported["service_capabilities"][0]["source_urls"] == ["https://source.example.test/record"]


def test_atomic_write_failure_leaves_catalogue_unchanged(tmp_path, monkeypatch):
    catalog, bundle = tmp_path / "services.json", tmp_path / "bundle.json"
    catalog_file(catalog)
    original = catalog.read_bytes()
    write_bundle(bundle, [bundle_service()])

    def fail_replace(*_args, **_kwargs):
        raise OSError("synthetic failure")

    monkeypatch.setattr("backend.catalog_promotion.os.replace", fail_replace)
    report = promote(bundle, catalog, {"sample_service"})
    assert report.status == "failed"
    assert not report.imported
    assert catalog.read_bytes() == original
    assert list(tmp_path.glob("services.json.backup-*"))
