"""Focused backend security regression checks."""

import pytest
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app import ServiceFields
from backend.llm import GeminiClient
from backend.main import sanitized_validation_error
from backend.pipeline import classify_intent
from backend.schemas import AskRequest


@pytest.mark.parametrize("field", ["aadhaar_number", "otp", "password", "identity_document"])
def test_guidance_request_does_not_accept_sensitive_data_fields(field):
    with pytest.raises(ValidationError):
        AskRequest(question="general service question", **{field: "not-a-real-value"})


def test_validation_error_response_does_not_echo_rejected_input():
    sentinel = "synthetic-private-input-value"
    exc = RequestValidationError([{
        "loc": ("body", "question"),
        "msg": "invalid",
        "type": "string_too_long",
        "input": sentinel,
    }])
    response = sanitized_validation_error(None, exc)
    assert response.status_code == 422
    assert sentinel not in response.body.decode("utf-8")


@pytest.mark.parametrize("url", [
    "http://example.test/path", "/relative", "javascript:alert(1)",
    "https://example.test:invalid/path",
])
def test_legacy_response_rejects_non_https_official_urls(url):
    with pytest.raises(ValidationError):
        ServiceFields(
            documents=[], fees=[],
            official_links=[{"label": "synthetic", "url": url}],
            sources=[], last_verified="2026-01-01",
        )


def test_legacy_response_accepts_data_driven_https_urls():
    result = ServiceFields(
        documents=[], fees=[],
        official_links=[{"label": "synthetic", "url": "https://example.test/path"}],
        sources=[{"title": "synthetic", "url": "https://example.test/source"}],
        last_verified="2026-01-01",
    )
    assert result.official_links[0]["url"].startswith("https://")


@pytest.mark.parametrize("question", [
    "My UID number 1234 5678 9012 is wrong",
    "My UID record needs help and my OTP is 482193",
    "My identity record is associated with user@example.test",
    "My UID password is synthetic-password",
])
def test_sensitive_question_is_not_sent_to_intent_model(question):
    class ModelSpy:
        calls = 0

        def classify_service(self, question, language):
            self.calls += 1
            return {"service_id": "aadhaar_update"}

    model = ModelSpy()
    result = classify_intent(question, "en", api_key="test-key", gemini_client=model)
    assert result == "unknown"
    assert model.calls == 0


def test_unrelated_intent_does_not_send_question_to_model():
    class ModelSpy:
        calls = 0

        def classify_service(self, question, language):
            self.calls += 1
            return None

    model = ModelSpy()
    assert classify_intent("weather", "en", api_key="test-key", gemini_client=model) == "unknown"
    assert model.calls == 0

def test_env_examples_contain_variable_names_only():
    from pathlib import Path

    root = Path(__file__).parents[2]
    for sample in (root / ".env.example", root / "backend" / ".env.example"):
        for line in sample.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                assert line.split("=", 1)[1].strip() == ""


def test_question_length_limit_remains_enforced():
    with pytest.raises(ValidationError):
        AskRequest(question="x" * 2001)


def test_provider_exception_text_is_not_logged(caplog):
    marker = "synthetic-provider-secret-marker"

    class FailingModel:
        def classify_service(self, question, language):
            raise RuntimeError(marker)

    classify_intent(
        "My UID record needs help", "en",
        api_key="test-key", gemini_client=FailingModel(),
    )
    assert marker not in caplog.text
    assert "My UID record needs help" not in caplog.text
