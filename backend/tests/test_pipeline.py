"""Pipeline checks use synthetic test data only; no government facts are asserted."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.main import ask
from backend.pipeline import build_response, classify, load_catalog
from backend.schemas import AskRequest


class NoOpLLM:
    def rephrase_steps(self, steps):
        return steps


@pytest.fixture
def synthetic_service():
    return {
        "id": "civic_complaint",
        "match_phrases": {"en": ["fixture civic service"], "kn": []},
        "steps": {"en": ["Synthetic test instruction."], "kn": []},
        "documents": [], "fees": [], "official_links": [], "sources": [],
        "last_verified": "test-fixture-only",
    }


def test_catalog_is_explicit_empty_development_fixture():
    assert load_catalog(Path(__file__).parents[1] / "data" / "services.json") == []


def test_classifies_known_phrase(synthetic_service):
    assert classify("Please help with fixture civic service", [synthetic_service]) == synthetic_service


def test_tied_phrase_match_is_unknown(synthetic_service):
    other = {**synthetic_service, "id": "income_certificate"}
    assert classify("fixture civic service", [synthetic_service, other]) is None


def test_request_rejects_extra_fields():
    with pytest.raises(ValidationError):
        AskRequest(question="hello", extra="not allowed")


def test_ask_returns_unsupported_fixture():
    result = ask(AskRequest(question="unmatched", language="en"))
    assert result.service_id == "unknown"
    assert result.status == "unsupported"


def test_pipeline_validates_supported_response(synthetic_service):
    result = build_response(AskRequest(question="fixture civic service"), [synthetic_service], NoOpLLM())
    assert result.supported is True
    assert result.service_id == "civic_complaint"