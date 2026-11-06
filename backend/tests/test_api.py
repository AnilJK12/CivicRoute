"""Route and schema checks use no network or invented public-service data."""


import pytest
from pydantic import ValidationError
from starlette.middleware.cors import CORSMiddleware
from starlette.datastructures import Headers

from backend.main import app, ask, health
from backend.schemas import AskRequest, UnsupportedResponse


def test_health_returns_simple_status():
    assert health() == {"status": "ok"}
    assert any(route.path == "/health" for route in app.routes)


def test_valid_request_returns_development_fixture_contract():
    request = AskRequest(question="How can I get help?", language="en")
    response = ask(request)
    assert response.status == "unsupported"
    assert response.service_id == "unknown"
    assert response.service_name is None
    assert response.suggested_services == ["aadhaar_update", "income_certificate", "civic_complaint"]
    assert "supported service" in response.message
    assert any(route.path == "/ask" and "POST" in route.methods for route in app.routes)


def test_frontend_ask_payload_contract_excludes_location_field():
    """The frontend's supported request fields are question and language only."""
    frontend_payload = {"question": "I need an income certificate", "language": "en"}
    parsed = AskRequest.model_validate(frontend_payload)
    assert parsed.model_dump() == frontend_payload

    # A previously sent optional location key was rejected by extra="forbid".
    with pytest.raises(ValidationError):
        AskRequest.model_validate({**frontend_payload, "location": "Mysuru"})


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_ask_options_preflight_succeeds_for_local_frontend(origin):
    middleware_config = next(
        middleware for middleware in app.user_middleware
        if middleware.cls is CORSMiddleware
    )
    cors = CORSMiddleware(app, **middleware_config.kwargs)
    request_headers = Headers(raw=[
            (b"origin", origin.encode()),
            (b"access-control-request-method", b"POST"),
            (b"access-control-request-headers", b"content-type"),
        ])
    response = cors.preflight_response(request_headers)
    headers = response.headers

    assert response.status_code == 200
    assert headers["access-control-allow-origin"] == origin
    assert "OPTIONS" in headers["access-control-allow-methods"]
    assert "POST" in headers["access-control-allow-methods"]
    assert "content-type" in headers["access-control-allow-headers"].lower()


def test_empty_question_is_rejected():
    with pytest.raises(ValidationError):
        AskRequest(question="   ", language="en")


def test_invalid_language_is_rejected():
    with pytest.raises(ValidationError):
        AskRequest(question="hello", language="fr")


def test_response_matches_unsupported_schema():
    body = ask(AskRequest(question="hello")).model_dump()
    parsed = UnsupportedResponse.model_validate(body)
    assert parsed.model_dump() == body
    assert app.docs_url == "/docs"
    assert "/ask" in app.openapi()["paths"]


def test_cors_requires_explicit_origins(monkeypatch):
    from backend.config import Settings

    monkeypatch.setenv("CORS_ORIGINS", "https://frontend.example, http://localhost:5173")
    assert Settings.from_environment().cors_origins == (
        "https://frontend.example", "http://localhost:5173"
    )
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(ValueError):
        Settings.from_environment()
    monkeypatch.setenv("CORS_ORIGINS", "https://frontend.example/path")
    with pytest.raises(ValueError):
        Settings.from_environment()
    monkeypatch.setenv("CORS_ORIGINS", "https://frontend.example:invalid")
    with pytest.raises(ValueError):
        Settings.from_environment()

def test_supported_response_schema_validates_structured_data():
    from backend.schemas import SupportedResponse

    response = SupportedResponse.model_validate({
        "status": "ok", "service_id": "civic_complaint", "service_name": "Test fixture",
        "language": "en", "steps": ["Synthetic test step."],
        "documents": [{"name": "Synthetic document", "status": "commonly_requested", "note": None}],
        "fee": {"amount": None, "currency": "INR", "status": "not_verified", "note": None},
        "online_option": {"available": None, "url": None, "status": "not_verified"},
        "office_type": None, "grievance": None, "sources": [],
        "last_verified": None, "warnings": ["synthetic schema test"],
    })
    assert response.service_id == "civic_complaint"
