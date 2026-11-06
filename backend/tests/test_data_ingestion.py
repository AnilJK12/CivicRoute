"""Bundle ingestion tests use synthetic records and never write trusted data."""

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backend.data_ingestion import ingest_bundle, ingest_bundle_file, main, write_pending_stage


def complete_service(**changes):
    url = "https://source.example.test/service"
    data = {
        "id": "sample_service",
        "name": "Synthetic Sample Service",
        "aliases": ["Sample Service Alternative"],
        "department": "Synthetic Department",
        "responsible_authority": "Synthetic Authority",
        "category": "Synthetic Category",
        "description": {
            "status": "verified", "value": "Synthetic description.", "source_urls": [url],
        },
        "application_procedure": {
            "status": "verified",
            "value": [{"order": 1, "text": "Synthetic step one."}, {"order": 2, "text": "Synthetic step two."}],
            "source_urls": [url],
        },
        "documents": {
            "status": "verified", "value": [{"name": "Synthetic document", "status": "conditional"}],
            "source_urls": [url],
        },
        "eligibility": {
            "status": "verified", "value": ["Synthetic criteria."], "source_urls": [url],
        },
        "fees": {
            "status": "verified",
            "value": {
                "amount": 12.5, "currency": "INR",
                "other_charges": [{"name": "Synthetic additional charge", "amount": 2, "status": "verified"}],
            },
            "source_urls": [url],
        },
        "online_application_links": {
            "status": "verified",
            "value": [{"title": "Synthetic portal", "url": "https://portal.example.test/apply"}],
            "source_urls": [url],
        },
        "offline_application_locations": {
            "status": "verified", "value": [{"name": "Synthetic centre", "district": "Test district"}],
            "source_urls": [url],
        },
        "service_channels": ["online", "offline"],
        "authorised_service_centres": ["synthetic_centre"],
        "jurisdiction": {"state": "Karnataka", "district": "Test district", "taluk": "Test taluk"},
        "sources": [{
            "title": "Synthetic source title", "url": url,
            "retrieved_at": "2026-10-20T10:00:00Z", "publisher": "Synthetic publisher",
        }],
        "verification_status": "verified",
        "last_verified": "2026-10-20",
    }
    data.update(changes)
    return data


def bundle(services=None, centres=None):
    return {"schema_version": 1, "services": services or [], "centres": centres or []}


def test_valid_bundle_stages_rich_service_as_pending_and_preserves_source_data():
    result = ingest_bundle(bundle([complete_service()]), today=date(2026, 10, 30))
    assert len(result.services) == 1
    staged = result.services[0]
    assert staged.import_state == "pending_review"
    assert staged.record.id == "sample_service"
    assert staged.record.department == "Synthetic Department"
    assert staged.record.category == "Synthetic Category"
    assert [step.order for step in staged.record.application_procedure.value] == [1, 2]
    assert staged.record.documents.value[0].status == "conditional"
    assert staged.record.fees.value.amount == 12.5
    assert staged.record.fees.value.other_charges[0].amount == 2
    assert staged.record.sources[0].title == "Synthetic source title"
    assert staged.record.sources[0].retrieved_at == datetime(2026, 10, 20, 10, tzinfo=timezone.utc)
    assert result.report.status == "valid"


def test_optional_fields_can_be_absent_and_are_reported_for_review_not_rejected():
    result = ingest_bundle(bundle([{"id": "dynamic_service", "name": "Synthetic Dynamic Service"}]))
    assert len(result.services) == 1
    assert result.services[0].record.fees is None
    assert result.services[0].record.documents is None
    record_report = result.report.records[0]
    assert record_report.disposition == "pending_review"
    assert any(issue.code == "missing_source_provenance" for issue in record_report.issues)
    assert result.report.rejected_count == 0


def test_verified_unavailable_fee_is_distinct_from_missing_and_not_zero():
    service = complete_service(fees={
        "status": "verified_unavailable", "value": None,
        "note": "Synthetic source explicitly states no fee was published.",
        "source_urls": ["https://source.example.test/service"],
    })
    result = ingest_bundle(bundle([service]), today=date(2026, 10, 30))
    fee = result.services[0].record.fees
    assert fee.status == "verified_unavailable"
    assert fee.value is None
    assert fee.note


def test_verified_fee_without_an_explicit_amount_is_rejected():
    service = complete_service(fees={
        "status": "verified", "value": {"amount": None, "currency": "INR"},
        "source_urls": ["https://source.example.test/service"],
    })
    result = ingest_bundle(bundle([service]))
    assert result.report.rejected_count == 1
    assert any("fees" in (issue.field or "") for issue in result.report.records[0].issues)


def test_duplicate_service_ids_and_conflicting_fields_are_reported():
    first = complete_service()
    second = complete_service(name="Synthetic Sample Service", department="Other Synthetic Department")
    result = ingest_bundle(bundle([first, second]), today=date(2026, 10, 30))
    for row in result.report.records:
        assert any(issue.code == "duplicate_service_id" for issue in row.issues)
        assert any("department" in issue.message for issue in row.issues if issue.code == "duplicate_service_id")
    assert all(staged.import_state == "pending_review" for staged in result.services)


def test_invalid_record_is_reported_with_field_reasons_not_silently_discarded():
    invalid = complete_service(online_application_links={
        "status": "verified", "value": [{"title": "Synthetic invalid link", "url": "http://example.test"}],
        "source_urls": ["https://source.example.test/service"],
    })
    result = ingest_bundle(bundle([invalid]))
    assert result.services == []
    assert result.report.rejected_count == 1
    row = result.report.records[0]
    assert row.disposition == "rejected"
    assert any(issue.field and "url" in issue.field for issue in row.issues)
    assert all("http://example.test" not in issue.message for issue in row.issues)


def test_missing_fact_source_reference_requires_review():
    service = complete_service(eligibility={
        "status": "verified", "value": ["Synthetic criteria."], "source_urls": [],
    })
    result = ingest_bundle(bundle([service]), today=date(2026, 10, 30))
    assert any(
        issue.code == "missing_fact_provenance" and issue.field == "eligibility"
        for issue in result.report.records[0].issues
    )


def test_possible_duplicate_services_with_distinct_ids_are_flagged():
    first = complete_service()
    second = complete_service(id="sample_service_alt", name="Another Name", aliases=[first["name"]])
    result = ingest_bundle(bundle([first, second]), today=date(2026, 10, 30))
    assert all(
        any(issue.code == "possible_duplicate_service" for issue in row.issues)
        for row in result.report.records
    )


def test_possible_duplicate_centres_and_unverified_authorization_are_reported():
    centre = {
        "id": "sample_centre",
        "name": "Synthetic Service Centre",
        "provider_type": "Synthetic provider",
        "district": "Test district",
        "taluk": "Test taluk",
        "service_capabilities": [{
            "service_id": "sample_service", "authorization_status": "unverified",
        }],
    }
    result = ingest_bundle(bundle(centres=[centre, dict(centre, id="other_centre")]))
    assert len(result.centres) == 2
    assert all(
        any(issue.code == "possible_duplicate_centre" for issue in row.issues)
        for row in result.report.records
    )
    assert all(staged.record.service_capabilities[0].authorization_status == "unverified" for staged in result.centres)


def test_stale_verification_date_is_reported():
    result = ingest_bundle(
        bundle([complete_service(last_verified="2024-01-01")]), today=date(2026, 10, 30)
    )
    assert any(issue.code == "stale_verification" for issue in result.report.records[0].issues)


def test_additional_departments_and_categories_are_data_driven():
    service = complete_service(
        id="new_department_service", name="Synthetic New Department Service",
        department="Synthetic New Department", category="Synthetic New Category",
    )
    result = ingest_bundle(bundle([service]), today=date(2026, 10, 30))
    assert result.services[0].record.department == "Synthetic New Department"
    assert result.services[0].record.category == "Synthetic New Category"


def test_existing_trusted_catalog_file_is_not_changed_by_ingestion():
    catalog_path = Path(__file__).parents[1] / "data" / "services.json"
    discovery_path = Path(__file__).parents[1] / "data" / "discoveries.json"
    before = catalog_path.read_bytes()
    discoveries_before = discovery_path.read_bytes() if discovery_path.exists() else None
    trusted = {"sample_service": {"id": "sample_service", "name": "Existing trusted record"}}
    result = ingest_bundle(bundle([complete_service()]), trusted_services=trusted, today=date(2026, 10, 30))
    assert catalog_path.read_bytes() == before
    assert (discovery_path.read_bytes() if discovery_path.exists() else None) == discoveries_before
    assert trusted["sample_service"]["name"] == "Existing trusted record"
    assert any(issue.code == "trusted_record_conflict" for issue in result.report.records[0].issues)
    assert result.services[0].import_state == "pending_review"


def test_json_file_validation_does_not_modify_input(tmp_path):
    path = tmp_path / "bundle.json"
    original = json.dumps(bundle([{"id": "json_service", "name": "Synthetic JSON Service"}]))
    path.write_text(original, encoding="utf-8")
    result = ingest_bundle_file(path, today=date(2026, 10, 30))
    assert result.services[0].record.id == "json_service"
    assert path.read_text(encoding="utf-8") == original


def test_pending_stage_is_separate_and_never_overwrites_existing_files(tmp_path):
    result = ingest_bundle(bundle([complete_service()]), today=date(2026, 10, 30))
    target = tmp_path / "pending.json"
    write_pending_stage(result, target)
    staged_data = json.loads(target.read_text(encoding="utf-8"))
    assert staged_data["bundle_state"] == "pending_review_not_trusted"
    assert staged_data["services"][0]["import_state"] == "pending_review"
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        write_pending_stage(result, target)
    assert target.read_bytes() == before


def test_documented_cli_writes_report_and_pending_stage_only_to_new_paths(tmp_path):
    bundle_path = tmp_path / "team-bundle.json"
    report_path = tmp_path / "quality-report.json"
    stage_path = tmp_path / "pending-stage.json"
    bundle_path.write_text(json.dumps(bundle([complete_service()])), encoding="utf-8")

    exit_code = main([
        str(bundle_path), "--report", str(report_path), "--staging-output", str(stage_path),
    ])

    assert exit_code == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "valid"
    assert json.loads(stage_path.read_text(encoding="utf-8"))["bundle_state"] == "pending_review_not_trusted"
