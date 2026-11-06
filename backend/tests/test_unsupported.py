"""Unsupported-request orchestration tests verify the composer is never guessed into."""

from dataclasses import replace

import backend.pipeline as pipeline
from backend.llm import StepsComposition
from backend.schemas import AskRequest, ClarificationResponse, UnsupportedResponse


class FailingClassifierModel:
    def classify_service(self, question, language):
        raise TimeoutError()


class ComposerSpy:
    def __init__(self):
        self.calls = 0

    def __call__(self, entry, language):
        self.calls += 1
        return StepsComposition(steps=["Synthetic step."])


def test_unrelated_request_gets_honest_fallback_without_composition(monkeypatch):
    composer = ComposerSpy()
    monkeypatch.setattr(pipeline, "compose_steps", composer)
    result = pipeline.answer_request(AskRequest(question="What is the weather?", language="en"))
    assert isinstance(result, UnsupportedResponse)
    assert result.service_id == "unknown"
    assert result.service_name is None
    assert result.suggested_services == ["aadhaar_update", "income_certificate", "civic_complaint"]
    assert "steps" not in result.model_dump()
    assert composer.calls == 0


def test_ambiguous_request_asks_which_service_without_composition(monkeypatch):
    composer = ComposerSpy()
    monkeypatch.setattr(pipeline, "compose_steps", composer)
    result = pipeline.answer_request(
        AskRequest(question="Aadhaar update and income certificate", language="en")
    )
    assert isinstance(result, ClarificationResponse)
    assert result.status == "clarification_needed"
    assert "service_id" in result.missing_fields
    assert composer.calls == 0


def test_empty_knowledge_base_does_not_compose_guessed_entry(monkeypatch):
    pipeline._in_memory_knowledge_base.cache_clear()
    composer = ComposerSpy()
    monkeypatch.setattr(pipeline, "classify_intent", lambda question, language: "civic_complaint")
    monkeypatch.setattr(pipeline, "classify_task_type", lambda question, language: "complaint_filing")
    monkeypatch.setattr(pipeline, "compose_steps", composer)
    result = pipeline.answer_request(AskRequest(question="civic request", language="en"))
    assert isinstance(result, UnsupportedResponse)
    assert result.service_id == "unknown"
    assert result.service_name is None
    assert composer.calls == 0
    pipeline._in_memory_knowledge_base.cache_clear()


def test_income_intent_with_missing_verified_record_stays_controlled_unsupported(monkeypatch):
    """Classification is correct, but an absent catalog record cannot produce guidance."""
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {})
    result = pipeline.answer_request(
        AskRequest(question="how do i apply income certificate", language="en")
    )
    assert isinstance(result, UnsupportedResponse)
    assert result.service_id == "unknown"
    assert result.service_name is None
    assert "steps" not in result.model_dump()


def test_model_failure_keeps_related_request_in_clarification_fallback(monkeypatch):
    composer = ComposerSpy()
    monkeypatch.setattr(
        pipeline,
        "classify_intent",
        lambda question, language: pipeline.classify_intent(
            question, language, api_key="test-key", model="test-model", gemini_client=FailingClassifierModel()
        ),
    )
    monkeypatch.setattr(pipeline, "compose_steps", composer)
    result = pipeline.answer_request(AskRequest(question="My UID number record is wrong", language="en"))
    assert isinstance(result, ClarificationResponse)
    assert result.service_id is None
    assert "service_id" in result.missing_fields
    assert composer.calls == 0


def test_unknown_classifier_id_is_rejected_before_retrieval_or_composition(monkeypatch):
    composer = ComposerSpy()
    monkeypatch.setattr(pipeline, "classify_intent", lambda question, language: "tax_help")
    monkeypatch.setattr(pipeline, "retrieve_service", lambda service_id: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(pipeline, "compose_steps", composer)
    result = pipeline.answer_request(AskRequest(question="unmatched", language="en"))
    assert isinstance(result, UnsupportedResponse)
    assert result.service_id == "unknown"
    assert composer.calls == 0


def test_general_portal_is_conditional_and_does_not_claim_full_coverage(monkeypatch):
    monkeypatch.setattr(
        pipeline, "settings",
        replace(pipeline.settings, verified_general_portal_url="https://example.test/general"),
    )
    result = pipeline.unsupported_request_response("en")
    assert result.official_portal_url == "https://example.test/general"
    assert "may not cover every service" in result.message
    assert any("may not cover every service" in warning for warning in result.warnings)
