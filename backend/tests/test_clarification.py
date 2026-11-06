"""Service and task clarification tests use synthetic data and mocked Gemini calls."""

from backend import pipeline
from backend.main import ask
from backend.knowledge_base import ServiceRecord
from backend.llm import StepsComposition
from backend.schemas import AskRequest, ClarificationResponse, SupportedResponse, UnsupportedResponse
import pytest


@pytest.fixture(autouse=True)
def disable_live_gemini(monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(
        pipeline,
        "settings",
        replace(pipeline.settings, gemini_api_key=None, gemini_model=None),
    )


def _entry():
    return ServiceRecord.model_validate({
        "id": "income_certificate",
        "name": "Synthetic certificate fixture",
        "aliases": ["synthetic certificate request"],
        "steps": {"en": ["Synthetic verified step."]},
        "documents": [],
        "fee": {"amount": None, "currency": "INR", "status": "not_verified", "note": None},
        "online_option": {"available": None, "url": None, "status": "not_verified"},
        "office_type": None,
        "grievance_route": None,
        "sources": [{"title": "Synthetic source", "url": "https://example.test/source"}],
        "last_verified": None,
    })


def _stub_verified_entry(monkeypatch):
    entry = _entry()
    monkeypatch.setattr(pipeline, "retrieve_service", lambda service_id: entry)
    monkeypatch.setattr(
        pipeline, "compose_steps",
        lambda record, language: StepsComposition(steps=record.steps["en"]),
    )
    return entry


def test_clear_service_and_application_task_do_not_trigger_clarification(monkeypatch):
    _stub_verified_entry(monkeypatch)
    result = pipeline.answer_request(
        AskRequest(question="How do I apply for an income certificate?", language="en")
    )
    assert isinstance(result, SupportedResponse)
    assert result.service_id == "income_certificate"


def test_service_without_exact_task_asks_a_concise_clarification(monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve_service", lambda service_id: (_ for _ in ()).throw(AssertionError()))
    result = pipeline.answer_request(AskRequest(question="I need an income certificate", language="en"))
    assert isinstance(result, ClarificationResponse)
    assert result.missing_fields == ["task_type"]
    assert "document checklist" in result.clarification_question
    assert result.question == result.clarification_question


def test_api_clarification_response_uses_fields_already_supported_by_frontend():
    response = ask(AskRequest(question="I need an income certificate", language="en"))
    body = response.model_dump()
    assert body["status"] == "clarification_needed"
    assert body["missing_fields"] == ["task_type"]
    assert body["question"] == body["clarification_question"]
    assert set(body) == {
        "status", "service_id", "service_name", "language", "question",
        "clarification_question", "missing_fields",
    }


def test_ambiguous_service_request_asks_which_service():
    result = pipeline.answer_request(
        AskRequest(question="Aadhaar update or income certificate application?", language="en")
    )
    assert isinstance(result, ClarificationResponse)
    assert "service_id" in result.missing_fields


def test_office_location_requires_locality(monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve_service", lambda service_id: (_ for _ in ()).throw(AssertionError()))
    result = pipeline.answer_request(
        AskRequest(question="Where is the income certificate office?", language="en")
    )
    assert isinstance(result, ClarificationResponse)
    assert result.missing_fields == ["locality"]
    assert "district or taluk" in result.clarification_question


def test_state_name_is_not_mistaken_for_locality(monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve_service", lambda service_id: (_ for _ in ()).throw(AssertionError()))
    result = pipeline.answer_request(
        AskRequest(question="Where is the income certificate office in Karnataka?", language="en")
    )
    assert isinstance(result, ClarificationResponse)
    assert "locality" in result.missing_fields


def test_explicit_locality_satisfies_location_clarification_and_warns_about_unverified_centres(monkeypatch):
    _stub_verified_entry(monkeypatch)
    result = pipeline.answer_request(
        AskRequest(question="Where is the income certificate office in Mysuru?", language="en")
    )
    assert isinstance(result, SupportedResponse)
    assert any("authorization are not confirmed" in warning for warning in result.warnings)


@pytest.mark.parametrize("question", [
    "Where can I get an income certificate in Mysuru?",
    "Where do I obtain an income certificate in Mysuru?",
])
def test_where_to_get_service_uses_location_task_flow(monkeypatch, question):
    _stub_verified_entry(monkeypatch)
    result = pipeline.answer_request(AskRequest(question=question, language="en"))
    assert isinstance(result, SupportedResponse)
    assert any("authorization are not confirmed" in warning for warning in result.warnings)


def test_unrelated_request_remains_unsupported():
    result = pipeline.answer_request(AskRequest(question="What is the weather today?", language="en"))
    assert isinstance(result, UnsupportedResponse)
    assert result.service_id == "unknown"


def test_task_classifier_covers_each_supported_task_type_without_gemini():
    examples = {
        "How do I apply for this service?": "application_procedure",
        "Which documents do I need?": "document_checklist",
        "Am I eligible?": "eligibility",
        "What is the fee?": "fee",
        "How do I file a complaint?": "complaint_filing",
        "What is the grievance route?": "grievance_route",
        "Where is the nearest office?": "office_location",
    }
    for question, expected in examples.items():
        assert pipeline.classify_task_type(question, "en", api_key="") == expected


def test_multiple_task_types_are_ambiguous():
    assert pipeline.classify_task_type("I need the fee and document checklist", "en", api_key="") is None


def test_gemini_task_classification_is_structured_and_application_validated():
    class FakeTaskClient:
        calls = 0

        def classify_task(self, question, language):
            self.calls += 1
            return {"task_type": "application_procedure"}

    client = FakeTaskClient()
    result = pipeline.classify_task_type(
        "Could you walk me through getting an income certificate?",
        "en",
        api_key="test-key",
        model="test-model",
        gemini_client=client,
    )
    assert result == "application_procedure"
    assert client.calls == 1


def test_gemini_task_wrapper_requests_task_enum_json(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    from backend.llm import GeminiClient

    captured = {}

    class FakeInteractions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text='{"task_type":"fee"}')

    class FakeClient:
        def __init__(self, **kwargs):
            self.interactions = FakeInteractions()

    fake_genai = ModuleType("google.genai")
    fake_genai.Client = FakeClient
    fake_google = ModuleType("google")
    fake_google.__path__ = []
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    assert GeminiClient("test-key", "test-model").classify_task("What is the fee?", "en") == {
        "task_type": "fee"
    }
    assert "fee" in captured["response_format"]["schema"]["properties"]["task_type"]["enum"]
    assert "unknown" in captured["response_format"]["schema"]["properties"]["task_type"]["enum"]


def test_gemini_unavailable_falls_back_to_deterministic_task_or_clarification():
    class FailingTaskClient:
        calls = 0

        def classify_task(self, question, language):
            self.calls += 1
            raise TimeoutError("synthetic timeout")

    client = FailingTaskClient()
    result = pipeline.classify_task_type(
        "Could you walk me through getting an income certificate?",
        "en",
        api_key="test-key",
        model="test-model",
        gemini_client=client,
    )
    assert result is None
    assert client.calls == 1

    deterministic = pipeline.classify_task_type("Please explain the application procedure", "en", api_key="")
    assert deterministic == "application_procedure"


def test_missing_gemini_configuration_skips_task_model_call():
    class SpyTaskClient:
        calls = 0

        def classify_task(self, question, language):
            self.calls += 1
            return {"task_type": "fee"}

    client = SpyTaskClient()
    result = pipeline.classify_task_type(
        "Could you walk me through this?",
        "en",
        api_key="",
        model=None,
        gemini_client=client,
    )
    assert result is None
    assert client.calls == 0
