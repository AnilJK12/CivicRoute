"""Intent classifier tests use explicit synthetic queries, never service facts."""

import pytest

from backend.pipeline import classify_intent


class FakeGemini:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = 0

    def classify_service(self, question, language, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.output


@pytest.mark.parametrize(("question", "expected"), [
    ("I need an Aadhaar update", "aadhaar_update"),
    ("Need an income certificate", "income_certificate"),
    ("Please report a pothole", "civic_complaint"),
])
def test_clear_english_intents_are_deterministic(question, expected):
    assert classify_intent(question, "en") == expected


@pytest.mark.parametrize("question", [
    "how do i apply income certificate",
    "I need an income certificate in Karnataka",
])
def test_income_certificate_natural_language_variations_are_deterministic(question):
    assert classify_intent(question, "en", api_key="") == "income_certificate"


@pytest.mark.parametrize(("question", "expected"), [
    ("I need an aadhar updte", "aadhaar_update"),
    ("Need an incom certificate", "income_certificate"),
])
def test_explicit_common_misspellings(question, expected):
    assert classify_intent(question, "en") == expected


def test_vague_phrasing_stays_unknown_even_when_model_is_configured():
    model = FakeGemini({"service_id": "civic_complaint"})
    assert classify_intent("Can you help me?", "en", api_key="test-key", gemini_client=model) == "unknown"
    assert model.calls == 0


def test_existing_kannada_fallback_text_is_not_a_service_intent():
    # No team-reviewed Kannada service phrases were found in the repository.
    assert classify_intent("ಈ ಪ್ರಶ್ನೆಯನ್ನು ಪರಿಶೀಲಿಸಿದ ಕರ್ನಾಟಕದ ಸಾರ್ವಜನಿಕ ಸೇವೆಯೊಂದಿಗೆ ಹೊಂದಿಸಲಾಗಲಿಲ್ಲ.", "kn") == "unknown"


def test_unrelated_question_is_not_sent_to_model():
    model = FakeGemini({"service_id": "income_certificate"})
    assert classify_intent("What is the weather forecast?", "en", api_key="test-key", gemini_client=model) == "unknown"
    assert model.calls == 0


def test_unrelated_question_remains_unknown_without_gemini():
    assert classify_intent("How do I bake sourdough bread?", "en", api_key="") == "unknown"


def _sample_catalog_record():
    from backend.knowledge_base import ServiceRecord

    return ServiceRecord.model_validate({
        "id": "sample_access",
        "name": "Synthetic test record",
        "aliases": ["synthetic sample request"],
        "departments": ["Synthetic unit"],
        "categories": ["sample_category"],
        "service_channels": ["online"],
        "task_types": ["sample_task"],
        "jurisdiction": ["test-only"],
        "provider_types": ["test-only"],
        "steps": {"en": ["Synthetic instruction."]},
        "documents": [],
        "fee": {"amount": None, "currency": "INR", "status": "not_verified", "note": None},
        "online_option": {"available": None, "url": None, "status": "not_verified"},
        "official_links": [],
        "office_type": None,
        "grievance_route": None,
        "sources": [{"title": "Synthetic source", "url": "https://example.test/source"}],
        "last_verified": None,
    })


def test_new_catalogue_alias_classifies_without_classifier_code_change(monkeypatch):
    import backend.pipeline as pipeline

    record = _sample_catalog_record()
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {record.id: record})
    assert classify_intent("I have a synthetic sample request", "en", api_key="") == "sample_access"


def test_catalogue_metadata_candidate_can_use_structured_classifier(monkeypatch):
    import backend.pipeline as pipeline

    record = _sample_catalog_record()
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {record.id: record})
    model = FakeGemini({"service_id": "sample_access"})
    result = classify_intent(
        "Please help with sample_task", "en", api_key="test-key", model="test-model", gemini_client=model
    )
    assert result == "sample_access"
    assert model.calls == 1


def test_configured_model_can_classify_related_paraphrase():
    model = FakeGemini({"service_id": "aadhaar_update"})
    result = classify_intent("My UID number record is wrong", "en", api_key="test-key", model="test-model", gemini_client=model)
    assert result == "aadhaar_update"
    assert model.calls == 1


@pytest.mark.parametrize("output", [
    "not-json",
    {"service_id": "weather"},
    {"service_id": "civic_complaint", "confidence": 0.9},
    {"service_id": ["aadhaar_update"]},
    ["aadhaar_update"],
])
def test_malformed_or_unexpected_model_output_returns_unknown(output):
    model = FakeGemini(output)
    assert classify_intent("My UID number record is wrong", "en", api_key="test-key", model="test-model", gemini_client=model) == "unknown"


def test_model_cannot_select_an_intent_inconsistent_with_query_signals():
    model = FakeGemini({"service_id": "civic_complaint"})
    assert classify_intent("My UID number record is wrong", "en", api_key="test-key", model="test-model", gemini_client=model) == "unknown"


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("rate limit")])
def test_model_failures_return_unknown_without_guessing(error):
    model = FakeGemini(error=error)
    assert classify_intent("My UID number record is wrong", "en", api_key="test-key", model="test-model", gemini_client=model) == "unknown"


def test_missing_key_uses_deterministic_fallback_only():
    model = FakeGemini({"service_id": "aadhaar_update"})
    assert classify_intent("My UID number record is wrong", "en", api_key="", gemini_client=model) == "unknown"
    assert model.calls == 0


def test_missing_model_skips_gemini_even_when_key_is_configured(monkeypatch):
    from dataclasses import replace
    import backend.pipeline as pipeline

    monkeypatch.setattr(pipeline, "settings", replace(pipeline.settings, gemini_api_key="test-key", gemini_model=None))
    model = FakeGemini({"service_id": "aadhaar_update"})
    result = classify_intent(
        "My UID number record is wrong", "en", gemini_client=model
    )
    assert result == "unknown"
    assert model.calls == 0


def test_configured_model_is_passed_to_gemini_client(monkeypatch):
    from dataclasses import replace
    import backend.pipeline as pipeline

    captured = {}
    fake_model = FakeGemini({"service_id": "aadhaar_update"})

    def make_client(api_key, model):
        captured["model"] = model
        return fake_model

    monkeypatch.setattr(pipeline, "settings", replace(pipeline.settings, gemini_api_key="test-key", gemini_model="configured-test-model"))
    monkeypatch.setattr(pipeline, "GeminiClient", make_client)

    result = classify_intent("My UID number record is wrong", "en")

    assert result == "aadhaar_update"
    assert captured["model"] == "configured-test-model"


def test_unknown_language_returns_unknown():
    assert classify_intent("Aadhaar update", "fr") == "unknown"
    assert classify_intent("Aadhaar update", []) == "unknown"

def test_gemini_wrapper_requests_enum_json_with_low_temperature(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    from backend.llm import GeminiClient

    captured = {}

    class FakeInteractions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text='{"service_id":"income_certificate"}')

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs
            self.interactions = FakeInteractions()

    fake_genai = ModuleType("google.genai")
    fake_genai.Client = FakeClient
    fake_google = ModuleType("google")
    fake_google.__path__ = []
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    output = GeminiClient("test-key", "test-model").classify_service("income proof", "en")
    assert output == {"service_id": "income_certificate"}
    assert captured["response_format"]["schema"]["properties"]["service_id"]["enum"] == [
        "aadhaar_update", "income_certificate", "civic_complaint", "unknown"
    ]
    assert captured["timeout"] == 8.0
    assert captured["store"] is False


def test_gemini_schema_accepts_catalogue_service_ids(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    from backend.llm import GeminiClient

    captured = {}

    class FakeInteractions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text='{"service_id":"sample_access"}')

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

    result = GeminiClient("test-key", "test-model").classify_service(
        "sample request", "en", allowed_service_ids=["sample_access"],
        service_descriptions=[{"id": "sample_access", "name": "Synthetic test record"}],
    )
    assert result == {"service_id": "sample_access"}
    assert captured["response_format"]["schema"]["properties"]["service_id"]["enum"] == [
        "sample_access", "unknown"
    ]
