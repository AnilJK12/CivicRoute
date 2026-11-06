"""Centre lookup tests use synthetic records in temporary catalogues only."""

import json
from datetime import date

import pytest
from fastapi import HTTPException

from backend import centres
from backend.config import settings
from backend.main import centres as centres_endpoint


def service_record(service_id="sample_service"):
    return {
        "id": service_id, "name": "Synthetic service",
        "steps": {"en": ["Synthetic step."]}, "documents": [],
        "fee": {"amount": None, "currency": "INR", "status": "not_verified", "note": None},
        "online_option": {"available": None, "url": None, "status": "not_verified"},
        "office_type": None, "grievance_route": None,
        "sources": [{"title": "Synthetic source", "url": "https://example.test/service"}],
        "last_verified": date.today().isoformat(),
    }


def centre_record(identifier, *, status="verified", capability="verified", service_id="sample_service",
                  district="Mysuru", taluk="Nanjangud", latitude=12.0, longitude=76.0,
                  last_verified=None):
    return {
        "id": identifier, "name": f"Synthetic centre {identifier}",
        "provider_type": "Synthetic provider", "address": "Synthetic address",
        "district": district, "taluk": taluk, "latitude": latitude, "longitude": longitude,
        "service_capabilities": [{"service_id": service_id, "authorization_status": capability,
                                  "source_urls": ["https://example.test/authorization"]}],
        "jurisdiction": {"state": "Synthetic State", "district": district, "taluk": taluk},
        "sources": [{"title": "Synthetic centre source", "url": "https://example.test/centre",
                     "retrieved_at": "2026-10-20T10:00:00Z", "publisher": "Synthetic publisher"}],
        "verification_status": status, "last_verified": last_verified or date.today().isoformat(),
    }


def write_catalog(path, centres):
    path.write_text(json.dumps({"_metadata": {"kind": "verified_catalog"},
                                "services": [service_record()], "centres": centres}), encoding="utf-8")


def test_only_approved_centres_with_verified_service_capability_returned(tmp_path):
    path = tmp_path / "services.json"
    write_catalog(path, [
        centre_record("approved"),
        centre_record("pending", status="pending_review"),
        centre_record("unauthorized", capability="unverified"),
        centre_record("other-service", service_id="other_service"),
        dict(centre_record("no-provenance"), sources=[]),
    ])
    result = centres.find_verified_centres("sample_service", catalog_path=path)
    assert [item.id for item in result.centres] == ["approved"]
    assert result.centres[0].sources[0]["title"] == "Synthetic centre source"


def test_district_and_taluk_filters_are_exact_case_insensitive(tmp_path):
    path = tmp_path / "services.json"
    write_catalog(path, [centre_record("match"), centre_record("wrong-district", district="Other"),
                         centre_record("wrong-taluk", taluk="Other")])
    result = centres.find_verified_centres("sample_service", district="mysuru", taluk="NANJANGUD", catalog_path=path)
    assert [item.id for item in result.centres] == ["match"]


def test_distance_orders_results_and_returns_haversine_distance(tmp_path):
    path = tmp_path / "services.json"
    write_catalog(path, [centre_record("far", latitude=13, longitude=77),
                         centre_record("near", latitude=12.01, longitude=76.01)])
    result = centres.find_verified_centres("sample_service", latitude=12, longitude=76, catalog_path=path)
    assert [item.id for item in result.centres] == ["near", "far"]
    assert result.centres[0].distance_km == pytest.approx(1.55, abs=0.1)


def test_missing_coordinates_are_returned_without_origin_but_excluded_for_distance(tmp_path):
    path = tmp_path / "services.json"
    write_catalog(path, [centre_record("no-coordinates", latitude=None, longitude=None)])
    no_origin = centres.find_verified_centres("sample_service", catalog_path=path)
    with_origin = centres.find_verified_centres("sample_service", latitude=12, longitude=76, catalog_path=path)
    assert len(no_origin.centres) == 1 and no_origin.centres[0].distance_km is None
    assert with_origin.centres == []


def test_empty_results_and_invalid_service_are_controlled(tmp_path):
    path = tmp_path / "services.json"
    write_catalog(path, [])
    assert centres.find_verified_centres("sample_service", catalog_path=path).centres == []
    with pytest.raises(KeyError):
        centres.find_verified_centres("unknown_service", catalog_path=path)
    with pytest.raises(ValueError):
        centres.find_verified_centres("Bad ID", catalog_path=path)


def test_malformed_centre_catalogue_fails_safely(tmp_path):
    path = tmp_path / "services.json"
    write_catalog(path, [{"name": "malformed", "verification_status": "approved-ish"}])
    with pytest.raises(centres.KnowledgeBaseError, match="unavailable or invalid"):
        centres.find_verified_centres("sample_service", catalog_path=path)


def test_centres_api_filters_from_configured_catalogue(tmp_path, monkeypatch):
    path = tmp_path / "services.json"
    write_catalog(path, [centre_record("api-match")])
    monkeypatch.setattr(centres, "settings", settings.__class__(catalog_path=path))
    response = centres_endpoint(
        service_id="sample_service", district="Mysuru", taluk=None, area=None,
        latitude=None, longitude=None, max_results=20,
    )
    assert response.centres[0].id == "api-match"
    with pytest.raises(HTTPException) as missing:
        centres_endpoint(
            service_id="unlisted_service", district=None, taluk=None, area=None,
            latitude=None, longitude=None, max_results=20,
        )
    assert missing.value.status_code == 404
