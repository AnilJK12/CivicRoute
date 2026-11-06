"""Deterministic retrieval tests use validated synthetic service entries."""

from dataclasses import replace

import pytest

import backend.pipeline as pipeline
from backend.knowledge_base import ServiceRecord, retrieve_from_providers
from backend.schemas import UnsupportedResponse


@pytest.fixture
def service_entry():
    def create(service_id):
        return ServiceRecord.model_validate({
            "id": service_id,
            "name": f"Synthetic {service_id}",
            "steps": {"en": ["Synthetic instruction."], "kn": ["Synthetic step."]},
            "documents": [],
            "fee": {"amount": None, "currency": "INR", "status": "not_verified", "note": None},
            "online_option": {"available": None, "url": None, "status": "not_verified"},
            "office_type": None,
            "grievance_route": None,
            "sources": [{"title": "Synthetic source", "url": "https://example.test/source"}],
            "last_verified": None,
        })
    return create


@pytest.mark.parametrize("service_id", ["aadhaar_update", "income_certificate", "civic_complaint"])
def test_retrieval_returns_exact_validated_entry(monkeypatch, service_entry, service_id):
    record = service_entry(service_id)
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {service_id: record})
    assert pipeline.retrieve_service(service_id) is record


def test_unknown_id_returns_only_controlled_unsupported(monkeypatch):
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {})
    result = pipeline.retrieve_service("unknown")
    assert isinstance(result, UnsupportedResponse)
    assert result.service_id == "unknown"
    assert result.service_name is None
    assert result.official_portal_url is None
    assert result.suggested_services == ["aadhaar_update", "income_certificate", "civic_complaint"]


def test_missing_supported_entry_does_not_fabricate_details(monkeypatch):
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {})
    result = pipeline.retrieve_service("income_certificate")
    assert isinstance(result, UnsupportedResponse)
    assert result.service_id == "unknown"
    assert result.official_portal_url is None


def test_dynamic_service_id_retrieves_catalogued_record(monkeypatch, service_entry):
    record = service_entry("sample_access")
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {record.id: record})
    assert pipeline.retrieve_service("sample_access") is record


def test_retrieval_providers_are_checked_in_declared_priority_order(service_entry):
    bundled_record = service_entry("sample_access")
    cached_record = service_entry("sample_access")

    class Provider:
        def __init__(self, value):
            self.value = value
            self.calls = 0

        def get(self, service_id):
            self.calls += 1
            return self.value

    bundled = Provider(bundled_record)
    cache = Provider(cached_record)
    result = retrieve_from_providers("sample_access", [bundled, cache])
    assert result is bundled_record
    assert bundled.calls == 1
    assert cache.calls == 0


def test_general_portal_only_appears_when_explicitly_configured(monkeypatch):
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {})
    monkeypatch.setattr(
        pipeline, "settings",
        replace(pipeline.settings, verified_general_portal_url="https://example.test/verified-portal"),
    )
    result = pipeline.retrieve_service("unknown")
    assert isinstance(result, UnsupportedResponse)
    assert result.official_portal_url == "https://example.test/verified-portal"

def test_general_portal_requires_explicit_https_configuration(monkeypatch):
    from backend.config import Settings

    monkeypatch.setenv("CIVICROUTE_VERIFIED_GENERAL_PORTAL_URL", "https://example.test/portal")
    assert Settings.from_environment().verified_general_portal_url == "https://example.test/portal"
    monkeypatch.setenv("CIVICROUTE_VERIFIED_GENERAL_PORTAL_URL", "http://example.test/portal")
    with pytest.raises(ValueError):
        Settings.from_environment()
    monkeypatch.setenv("CIVICROUTE_VERIFIED_GENERAL_PORTAL_URL", "https://example.test:bad/portal")
    with pytest.raises(ValueError):
        Settings.from_environment()
