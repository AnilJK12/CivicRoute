"""Final deterministic QA coverage for the public API and safety contract."""

from pydantic import ValidationError
import pytest

import backend.main as main
import backend.pipeline as pipeline
from backend.knowledge_base import ServiceRecord
from backend.llm import compose_steps


def synthetic_entry(service_id):
    return ServiceRecord.model_validate({
        "id": service_id,
        "name": f"Synthetic {service_id} fixture",
        "steps": {"en": ["Synthetic verified step."], "kn": ["ಪರಿಶೀಲಿಸಿದ ಕೃತಕ ಹಂತ."]},
        "documents": [{"name": "Synthetic conditional record", "status": "conditional", "note": "Fixture only."}],
        "fee": {"amount": None, "currency": "INR", "status": "not_verified", "note": None},
        "online_option": {"available": True, "url": "https://example.test/official", "status": "verified"},
        "office_type": None,
        "grievance_route": None,
        "sources": [{"title": "Synthetic source", "url": "https://example.test/source"}],
        "last_verified": "2026-10-01",
    })


def test_health_route_is_simple():
    assert main.health() == {"status": "ok"}
    assert any(route.path == "/health" for route in main.app.routes)


def test_valid_post_ask_returns_current_unsupported_contract():
    body = main.ask(main.AskRequest(question="Could you help me?", language="en")).model_dump()
    assert {"status", "service_id", "service_name", "message", "official_portal_url", "suggested_services", "warnings"}.issubset(set(body))
    assert body["status"] == "unsupported"
    assert body["service_id"] == "unknown"
    assert body["service_name"] is None
    assert any(route.path == "/ask" and "POST" in route.methods for route in main.app.routes)


@pytest.mark.parametrize("payload", [
    {"question": "   ", "language": "en"},
    {"question": "x" * 2001, "language": "en"},
])
def test_blank_or_oversized_question_is_rejected_without_echo(payload):
    with pytest.raises(ValidationError):
        main.AskRequest.model_validate(payload)


def test_unsupported_language_is_rejected():
    with pytest.raises(ValidationError):
        main.AskRequest(question="Hello", language="fr")


@pytest.mark.parametrize("service_id", ["aadhaar_update", "income_certificate", "civic_complaint"])
def test_each_supported_service_returns_contract_from_entry(monkeypatch, service_id):
    entry = synthetic_entry(service_id)
    monkeypatch.setattr(pipeline, "classify_intent", lambda question, language: service_id)
    monkeypatch.setattr(pipeline, "classify_task_type", lambda question, language: "application_procedure")
    monkeypatch.setattr(pipeline, "retrieve_service", lambda requested: entry if requested == service_id else None)
    monkeypatch.setattr(pipeline, "compose_steps", lambda record, language: pipeline.StepsComposition(steps=record.steps[language]))

    body = main.ask(main.AskRequest(question="Synthetic supported test", language="en")).model_dump()
    assert {"status", "service_id", "service_name", "language", "steps", "documents", "fee", "eligibility", "online_option", "office_type", "grievance", "sources", "last_verified", "warnings"}.issubset(set(body))
    assert body["status"] == "ok"
    assert body["service_id"] == service_id
    assert body["fee"]["amount"] is None
    assert body["fee"]["status"] == "not_verified"
    assert body["documents"][0]["status"] == "conditional"
    assert body["sources"] == [{"title": "Synthetic source", "url": "https://example.test/source"}]
    assert body["last_verified"] == "2026-10-01"


def test_out_of_scope_request_is_unsupported_and_does_not_retrieve(monkeypatch):
    monkeypatch.setattr(pipeline, "classify_intent", lambda question, language: "unknown")
    monkeypatch.setattr(pipeline, "retrieve_service", lambda service_id: pytest.fail("must not retrieve unknown"))
    response = main.ask(main.AskRequest(question="What is the weather?", language="en"))
    assert response.status == "unsupported"


def test_malformed_retrieved_entry_fails_to_unsupported(monkeypatch):
    monkeypatch.setattr(pipeline, "classify_intent", lambda question, language: "income_certificate")
    monkeypatch.setattr(pipeline, "classify_task_type", lambda question, language: "application_procedure")
    monkeypatch.setattr(pipeline, "retrieve_service", lambda service_id: {"id": "income_certificate"})
    response = main.ask(main.AskRequest(question="Synthetic query", language="en"))
    assert response.status == "unsupported"


@pytest.mark.parametrize("error", [TimeoutError("timeout"), RuntimeError("429 rate limit")])
def test_composer_timeout_and_rate_limit_use_verified_stored_steps(monkeypatch, error):
    import backend.llm as llm
    from dataclasses import replace

    monkeypatch.setattr(llm, "settings", replace(llm.settings, gemini_api_key="test-key"))

    class BrokenModel:
        def compose_verified_steps(self, source_steps, language):
            raise error

    entry = {"steps": {"en": ["Verified synthetic fallback."]}}
    result = compose_steps(entry, "en", gemini_client=BrokenModel())
    assert result.steps == entry["steps"]["en"]
    assert result.warnings


@pytest.mark.parametrize("output", ["not-json", '{"steps":["one"],"url":"https://fake.test"}'])
def test_composer_malformed_output_uses_verified_steps(monkeypatch, output):
    import backend.llm as llm
    from dataclasses import replace

    monkeypatch.setattr(llm, "settings", replace(llm.settings, gemini_api_key="test-key"))

    class Model:
        def compose_verified_steps(self, source_steps, language):
            return output

    entry = {"steps": {"en": ["Verified synthetic fallback."]}}
    result = compose_steps(entry, "en", gemini_client=Model())
    assert result.steps == entry["steps"]["en"]
    assert result.warnings