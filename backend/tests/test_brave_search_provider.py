"""Brave provider parsing and configuration tests use only mocked HTTP responses."""

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.official_sources import (
    BRAVE_WEB_SEARCH_URL,
    BraveSearchProvider,
    OfficialSourceRetriever,
    build_configured_official_source_retriever,
)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        assert len(self.body) <= limit or limit > 0
        return self.body[:limit]


def configured(**changes):
    values = {
        "official_source_provider": "brave",
        "brave_search_api_key": "synthetic-test-token",
        "official_source_approved_hosts": ("gov.in", "uidai.gov.in"),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def result_payload(results):
    return json.dumps({"web": {"results": results}}).encode("utf-8")


def web_result(**changes):
    item = {
        "title": "Synthetic government page",
        "url": "https://service.karnataka.gov.in/guide",
        "description": "Synthetic result excerpt for parser testing.",
    }
    item.update(changes)
    return item


def test_brave_search_success_maps_only_allowlisted_results_and_sends_secure_request():
    captured = {}
    body = result_payload([
        web_result(),
        web_result(url="https://unapproved.example.test/page"),
    ])

    def opener(request, *, timeout):
        captured["url"] = request.full_url
        captured["token_header"] = request.get_header("X-subscription-token")
        captured["timeout"] = timeout
        return FakeResponse(body)

    provider = BraveSearchProvider(
        "synthetic-test-token", approved_hosts=["gov.in"], opener=opener
    )
    retriever = OfficialSourceRetriever(
        provider, approved_hosts=["gov.in"], timeout_seconds=1.25
    )
    result = retriever.retrieve("sample service guidance", jurisdiction="Mysuru")

    assert result.status == "ok"
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.title == "Synthetic government page"
    assert candidate.url == "https://service.karnataka.gov.in/guide"
    assert candidate.jurisdiction == "Mysuru"
    assert candidate.excerpt == "Synthetic result excerpt for parser testing."
    assert candidate.verification_status == "unverified"
    assert candidate.retrieved_at.tzinfo is not None
    assert captured["token_header"] == "synthetic-test-token"
    assert captured["timeout"] == 1.25
    parsed = urlsplit(captured["url"])
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == BRAVE_WEB_SEARCH_URL
    params = parse_qs(parsed.query)
    assert params["country"] == ["IN"]
    assert params["result_filter"] == ["web"]
    assert "Mysuru" in params["q"][0]
    assert "Karnataka" in params["q"][0]


def test_empty_brave_web_results_return_no_results():
    provider = BraveSearchProvider(
        "synthetic-test-token",
        approved_hosts=["gov.in"],
        opener=lambda *_args, **_kwargs: FakeResponse(result_payload([])),
    )
    result = OfficialSourceRetriever(provider, approved_hosts=["gov.in"]).retrieve("sample query")
    assert result.status == "no_results"
    assert result.candidates == []


@pytest.mark.parametrize("body", [
    b"not-json",
    json.dumps({"web": {"results": {"bad": "shape"}}}).encode(),
    result_payload([web_result(description="")]),
    b"x" * 1001,
])
def test_malformed_or_oversized_responses_are_rejected_without_payload_details(body):
    provider = BraveSearchProvider(
        "synthetic-test-token",
        approved_hosts=["gov.in"],
        max_response_bytes=1000,
        opener=lambda *_args, **_kwargs: FakeResponse(body),
    )
    result = OfficialSourceRetriever(provider, approved_hosts=["gov.in"]).retrieve("sample query")
    assert result.status == "invalid_results"
    assert result.candidates == []
    assert len(" ".join(result.warnings)) < 200


@pytest.mark.parametrize("error", [TimeoutError("secret timeout detail"), OSError("sensitive transport detail")])
def test_http_provider_timeout_and_failure_are_sanitized(error):
    provider = BraveSearchProvider(
        "synthetic-test-token",
        approved_hosts=["gov.in"],
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    result = OfficialSourceRetriever(provider, approved_hosts=["gov.in"]).retrieve("sample query")
    assert result.status == "unavailable"
    assert result.candidates == []
    assert "secret timeout detail" not in " ".join(result.warnings)
    assert "sensitive transport detail" not in " ".join(result.warnings)


@pytest.mark.parametrize("configuration", [
    configured(official_source_provider=None),
    configured(official_source_provider="unknown"),
    configured(brave_search_api_key=None),
    configured(official_source_approved_hosts=()),
])
def test_missing_or_unsupported_configuration_disables_live_provider(configuration):
    assert build_configured_official_source_retriever(configuration) is None


def test_complete_configuration_builds_adapter_without_making_a_request():
    adapter = build_configured_official_source_retriever(configured())
    assert isinstance(adapter, OfficialSourceRetriever)
    assert isinstance(adapter.provider, BraveSearchProvider)


def test_sensitive_query_is_not_sent_to_provider():
    calls = []
    provider = BraveSearchProvider(
        "synthetic-test-token",
        approved_hosts=["gov.in"],
        opener=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    result = OfficialSourceRetriever(provider, approved_hosts=["gov.in"]).retrieve(
        "Aadhaar update phone number 9876543210"
    )
    assert result.status == "unavailable"
    assert calls == []
