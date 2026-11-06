"""Strict knowledge-base loader tests use synthetic, non-government data."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from backend.knowledge_base import KnowledgeBaseError, get_service, load_services


@pytest.fixture
def valid_record():
    return {
        "id": "civic_complaint",
        "name": "Synthetic test service",
        "steps": {"en": ["Synthetic test instruction."], "kn": ["Synthetic test step."]},
        "documents": [{"name": "Synthetic record", "status": "commonly_requested", "note": None}],
        "fee": {"amount": 125.0, "currency": "INR", "status": "verified", "note": "Synthetic test value."},
        "online_option": {"available": True, "url": "https://example.test/apply", "status": "synthetic"},
        "office_type": None,
        "grievance_route": None,
        "sources": [{"title": "Synthetic source", "url": "https://example.test/source"}],
        "last_verified": "2026-10-01",
    }


def write_catalog(path, records):
    path.write_text(json.dumps({
        "_metadata": {"kind": "verified_catalog"},
        "services": records,
    }), encoding="utf-8")


def test_loads_valid_record_and_preserves_document_label(tmp_path, valid_record):
    catalog_path = tmp_path / "services.json"
    write_catalog(catalog_path, [valid_record])
    loaded = load_services(catalog_path)
    assert loaded["civic_complaint"].documents[0].status == "commonly_requested"
    assert loaded["civic_complaint"].last_verified.isoformat() == "2026-10-01"
    assert get_service("civic_complaint", loaded).name == "Synthetic test service"


def test_detects_missing_required_fields(tmp_path, valid_record):
    incomplete = deepcopy(valid_record)
    del incomplete["grievance_route"]
    catalog_path = tmp_path / "services.json"
    write_catalog(catalog_path, [incomplete])
    with pytest.raises(KnowledgeBaseError, match=r"grievance_route \(missing\)"):
        load_services(catalog_path)


def test_rejects_malformed_service_id(tmp_path, valid_record):
    invalid = deepcopy(valid_record)
    invalid["id"] = "Invalid Service ID"
    catalog_path = tmp_path / "services.json"
    write_catalog(catalog_path, [invalid])
    with pytest.raises(KnowledgeBaseError, match=r"id \(invalid\)"):
        load_services(catalog_path)


def test_rejects_empty_source_list(tmp_path, valid_record):
    invalid = deepcopy(valid_record)
    invalid["sources"] = []
    catalog_path = tmp_path / "services.json"
    write_catalog(catalog_path, [invalid])
    with pytest.raises(KnowledgeBaseError, match=r"sources \(invalid\)"):
        load_services(catalog_path)


@pytest.mark.parametrize("url", [
    "http://example.test/source", "/relative", "javascript:alert(1)",
    "https://example.test:invalid/source", "https://user@example.test/source",
])
def test_rejects_non_absolute_https_source_urls(tmp_path, valid_record, url):
    invalid = deepcopy(valid_record)
    invalid["sources"][0]["url"] = url
    catalog_path = tmp_path / "services.json"
    write_catalog(catalog_path, [invalid])
    with pytest.raises(KnowledgeBaseError, match=r"sources.0.url \(invalid\)"):
        load_services(catalog_path)


def test_unverified_fee_stays_null_and_never_defaults_to_zero(tmp_path, valid_record):
    record = deepcopy(valid_record)
    record["fee"] = {"amount": None, "currency": "INR", "status": "not_verified", "note": "No verified fee."}
    catalog_path = tmp_path / "services.json"
    write_catalog(catalog_path, [record])
    assert load_services(catalog_path)["civic_complaint"].fee.amount is None


def test_rejects_zero_for_unverified_fee(tmp_path, valid_record):
    record = deepcopy(valid_record)
    record["fee"] = {"amount": 0, "currency": "INR", "status": "not_verified", "note": None}
    catalog_path = tmp_path / "services.json"
    write_catalog(catalog_path, [record])
    with pytest.raises(KnowledgeBaseError, match="fee"):
        load_services(catalog_path)


def test_missing_requested_service_fails_safely(tmp_path):
    catalog_path = tmp_path / "services.json"
    write_catalog(catalog_path, [])
    with pytest.raises(KnowledgeBaseError, match="civic_complaint is missing"):
        get_service("civic_complaint", load_services(catalog_path))


def test_loads_repository_development_catalog_without_fabricating_entries():
    catalog_path = Path(__file__).parents[1] / "data" / "services.json"
    assert load_services(catalog_path) == {}


def test_catalog_accepts_dynamic_ids_and_service_metadata(tmp_path, valid_record):
    record = deepcopy(valid_record)
    record.update({
        "id": "sample_access",
        "name": "Synthetic test record",
        "aliases": ["synthetic sample request"],
        "departments": ["Synthetic unit"],
        "categories": ["sample_category"],
        "service_channels": ["online", "office"],
        "task_types": ["sample_task"],
        "jurisdiction": ["test-only jurisdiction"],
        "provider_types": ["test-only provider"],
        "official_links": [{"title": "Synthetic link", "url": "https://example.test/link"}],
    })
    catalog_path = tmp_path / "services.json"
    write_catalog(catalog_path, [record])
    loaded = load_services(catalog_path)
    assert loaded["sample_access"].aliases == ["synthetic sample request"]
    assert loaded["sample_access"].departments == ["Synthetic unit"]
    assert loaded["sample_access"].official_links[0].url == "https://example.test/link"


def test_unreviewed_discovery_is_not_loaded_as_trusted_service(tmp_path):
    catalog_path = tmp_path / "services.json"
    catalog_path.write_text(json.dumps({
        "_metadata": {"kind": "development_fixture", "verified": False},
        "services": [],
        "discoveries": [{"id": "unreviewed_sample", "name": "Unreviewed sample", "url": "not trusted"}],
    }), encoding="utf-8")
    assert load_services(catalog_path) == {}


def test_rejects_missing_source_provenance(tmp_path, valid_record):
    record = deepcopy(valid_record)
    record["sources"] = [{"title": "Source without URL"}]
    catalog_path = tmp_path / "services.json"
    write_catalog(catalog_path, [record])
    with pytest.raises(KnowledgeBaseError, match=r"sources.0.url \(missing\)"):
        load_services(catalog_path)


def test_rejects_invalid_official_link_url(tmp_path, valid_record):
    record = deepcopy(valid_record)
    record["official_links"] = [{"title": "Bad link", "url": "http://example.test"}]
    catalog_path = tmp_path / "services.json"
    write_catalog(catalog_path, [record])
    with pytest.raises(KnowledgeBaseError, match=r"official_links.0.url \(invalid\)"):
        load_services(catalog_path)