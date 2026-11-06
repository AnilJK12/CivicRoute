"""Composer tests are mocked and use synthetic verified-step strings only."""

from dataclasses import replace
from types import ModuleType, SimpleNamespace
import sys

import pytest

import backend.llm as llm
from backend.llm import GeminiClient, compose_steps


class FakeComposer:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []

    def compose_verified_steps(self, source_steps, language):
        self.calls.append((source_steps, language))
        if self.error:
            raise self.error
        return self.output


@pytest.fixture
def entry():
    return {
        "id": "civic_complaint",
        "name": "Synthetic test entry",
        "steps": {"en": ["Synthetic source step one.", "Synthetic source step two."],
                  "kn": ["ಪರಿಶೀಲಿಸಿದ ಕೃತಕ ಹಂತ ಒಂದು.", "ಪರಿಶೀಲಿಸಿದ ಕೃತಕ ಹಂತ ಎರಡು."]},
        "fee": {"amount": None, "status": "not_verified"},
        "documents": ["Synthetic document"],
    }


def set_api_key(monkeypatch, key):
    monkeypatch.setattr(llm, "settings", replace(llm.settings, gemini_api_key=key, gemini_model="test-model"))


def test_missing_key_uses_stored_steps_with_warning(monkeypatch, entry):
    set_api_key(monkeypatch, None)
    model = FakeComposer('{"steps":["unexpected"]}')
    result = compose_steps(entry, "en", gemini_client=model)
    assert result.steps == entry["steps"]["en"]
    assert result.warnings
    assert model.calls == []


def test_missing_model_uses_stored_steps_without_calling_model(monkeypatch, entry):
    monkeypatch.setattr(llm, "settings", replace(llm.settings, gemini_api_key="test-key", gemini_model=None))
    model = FakeComposer('{"steps":["unexpected"]}')
    result = compose_steps(entry, "en", gemini_client=model)
    assert result.steps == entry["steps"]["en"]
    assert result.warnings
    assert model.calls == []


def test_reviewed_pre_written_kannada_is_preferred(monkeypatch, entry):
    set_api_key(monkeypatch, "test-key")
    model = FakeComposer('{"steps":["model output"]}')
    result = compose_steps(entry, "kn", gemini_client=model)
    assert result.steps == entry["steps"]["kn"]
    assert result.warnings == []
    assert model.calls == []


def test_model_receives_only_stored_steps_and_requested_language(monkeypatch, entry):
    set_api_key(monkeypatch, "test-key")
    model = FakeComposer('{"steps":["Simplified step one.","Simplified step two."]}')
    result = compose_steps(entry, "en", gemini_client=model)
    assert model.calls == [(entry["steps"]["en"], "en")]
    assert result.steps == ["Simplified step one.", "Simplified step two."]
    assert result.warnings == []


def test_translates_only_steps_when_requested_language_is_absent(monkeypatch, entry):
    set_api_key(monkeypatch, "test-key")
    entry["steps"].pop("kn")
    model = FakeComposer('{"steps":["ಕೃತಕ ಅನುವಾದಿತ ಹಂತ ಒಂದು.","ಕೃತಕ ಅನುವಾದಿತ ಹಂತ ಎರಡು."]}')
    result = compose_steps(entry, "kn", gemini_client=model)
    assert model.calls == [(entry["steps"]["en"], "kn")]
    assert len(result.steps) == 2


def test_model_cannot_add_or_remove_steps(monkeypatch, entry):
    set_api_key(monkeypatch, "test-key")
    model = FakeComposer('{"steps":["Only one rewritten step."]}')
    result = compose_steps(entry, "en", gemini_client=model)
    assert result.steps == entry["steps"]["en"]
    assert result.warnings


def test_extra_model_fields_are_rejected(monkeypatch, entry):
    set_api_key(monkeypatch, "test-key")
    model = FakeComposer('{"steps":["Rewrite one.","Rewrite two."],"fee":0}')
    result = compose_steps(entry, "en", gemini_client=model)
    assert result.steps == entry["steps"]["en"]
    assert result.warnings


def test_model_failure_uses_requested_stored_language(monkeypatch, entry):
    set_api_key(monkeypatch, "test-key")
    model = FakeComposer(error=TimeoutError())
    result = compose_steps(entry, "en", gemini_client=model)
    assert result.steps == entry["steps"]["en"]
    assert result.warnings


def test_translation_failure_uses_original_verified_language_with_warning(monkeypatch, entry):
    set_api_key(monkeypatch, "test-key")
    entry["steps"].pop("kn")
    model = FakeComposer(error=RuntimeError("provider unavailable"))
    result = compose_steps(entry, "kn", gemini_client=model)
    assert result.steps == entry["steps"]["en"]
    assert result.warnings


def test_missing_requested_language_returns_original_steps_with_warning(monkeypatch, entry):
    set_api_key(monkeypatch, None)
    entry["steps"].pop("kn")
    result = compose_steps(entry, "kn")
    assert result.steps == entry["steps"]["en"]
    assert result.warnings


def test_genai_interactions_call_uses_steps_only_schema_timeout_and_no_storage(monkeypatch):
    captured = {}

    class FakeInteractions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text='{"steps":["Synthetic composed step."]}')

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs
            self.interactions = FakeInteractions()

    fake_genai = ModuleType("google.genai")
    fake_genai.Client = FakeClient
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    output = GeminiClient("test-key", "test-model").compose_verified_steps(["Only this source step."], "en")
    assert output == '{"steps":["Synthetic composed step."]}'
    assert captured["model"] == "test-model"
    assert "Only this source step." in captured["input"]
    assert "Synthetic test entry" not in captured["input"]
    assert captured["response_format"]["schema"]["properties"].keys() == {"steps"}
    assert captured["timeout"] == 8.0
    assert captured["store"] is False


def test_composer_retry_is_bounded(monkeypatch):
    captured = {"calls": 0}

    class FailingInteractions:
        def create(self, **kwargs):
            captured["calls"] += 1
            raise TimeoutError()

    class FakeClient:
        def __init__(self, **kwargs):
            self.interactions = FailingInteractions()

    fake_genai = ModuleType("google.genai")
    fake_genai.Client = FakeClient
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    assert GeminiClient("test-key", "test-model").compose_verified_steps(["Source."], "en") is None
    assert captured["calls"] == 2
