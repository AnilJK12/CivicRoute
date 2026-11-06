"""Mockable retrieval adapter for approved official sources; results stay unverified."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import re
import socket
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .url_validation import is_absolute_https_url

BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_RESULT_COUNT = 8
_SENSITIVE_SEARCH_INPUT = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"(?<!\d)(?:\d[\s-]?){9,}\d(?!\d)", re.IGNORECASE
)


class OfficialSourceCandidate(BaseModel):
    """A discovery with provenance; it is never a trusted catalogue record."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    retrieved_at: datetime
    jurisdiction: str | None
    excerpt: str = Field(min_length=1)
    verification_status: Literal["unverified"] = "unverified"
    claim_key: str | None = None

    @field_validator("title", "excerpt")
    @classmethod
    def text_provenance_must_be_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("candidate provenance text must not be blank")
        return value

    @field_validator("jurisdiction", "claim_key")
    @classmethod
    def optional_provenance_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("optional provenance must be null or nonblank")
        return value

    @field_validator("url")
    @classmethod
    def source_url_must_be_https(cls, value: str) -> str:
        if not is_absolute_https_url(value):
            raise ValueError("source URL must be absolute HTTPS")
        return value

    @field_validator("retrieved_at")
    @classmethod
    def retrieval_timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieval timestamp must include a timezone")
        return value.astimezone(timezone.utc)


class SourceConflict(BaseModel):
    """Indices of differing excerpts a provider associates with the same claim key."""

    claim_key: str
    candidate_indices: list[int] = Field(min_length=2)


class OfficialSourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "no_results", "unavailable", "invalid_results"]
    candidates: list[OfficialSourceCandidate] = Field(default_factory=list)
    conflicts: list[SourceConflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MalformedOfficialSourceResponse(ValueError):
    """Provider response is not the documented machine-readable result shape."""


class BraveSearchProvider:
    """Small standard-library client for Brave Web Search; output remains unverified."""

    def __init__(
        self,
        api_key: str,
        *,
        approved_hosts: Sequence[str],
        result_count: int = DEFAULT_RESULT_COUNT,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        opener=urlopen,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Brave Search API key is required")
        if not 1 <= result_count <= 20:
            raise ValueError("Search result count must be between 1 and 20")
        if max_response_bytes <= 0:
            raise ValueError("Maximum provider response size must be positive")
        self._api_key = api_key
        self.approved_hosts = tuple(host.casefold().rstrip(".") for host in approved_hosts if host.strip())
        self.result_count = result_count
        self.max_response_bytes = max_response_bytes
        self._opener = opener

    def _allowed(self, url: str) -> bool:
        if not is_absolute_https_url(url):
            return False
        host = (urlsplit(url).hostname or "").casefold().rstrip(".")
        return any(host == allowed or host.endswith("." + allowed) for allowed in self.approved_hosts)

    @staticmethod
    def _query(query: str, jurisdiction: str | None) -> str:
        # Brave documents a 600-character/75-word query limit.
        text = " ".join(part.strip() for part in (query, jurisdiction, "Karnataka India") if part and part.strip())
        if _SENSITIVE_SEARCH_INPUT.search(text):
            raise ValueError("Sensitive input is not sent to web search")
        bounded = " ".join(text.split()[:75])
        if len(bounded) > 600:
            bounded = bounded[:600].rsplit(" ", 1)[0]
        if not bounded:
            raise ValueError("Search query is empty")
        return bounded

    def search(
        self, query: str, jurisdiction: str | None, *, timeout_seconds: float
    ) -> Sequence[Mapping[str, object]]:
        """Return only result fields consumed by the existing provenance validator."""
        params = urlencode({
            "q": self._query(query, jurisdiction),
            "country": "IN",
            "search_lang": "en",
            "count": self.result_count,
            "result_filter": "web",
            "text_decorations": "false",
        })
        request = Request(
            f"{BRAVE_WEB_SEARCH_URL}?{params}",
            headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
            method="GET",
        )
        try:
            with self._opener(request, timeout=timeout_seconds) as response:
                body = response.read(self.max_response_bytes + 1)
        except (TimeoutError, socket.timeout):
            raise TimeoutError("Official-source provider timed out") from None
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError("Official-source provider timed out") from None
            raise RuntimeError("Official-source provider request failed") from None
        except HTTPError:
            raise RuntimeError("Official-source provider request failed") from None
        except Exception:
            raise RuntimeError("Official-source provider request failed") from None

        if not isinstance(body, bytes) or len(body) > self.max_response_bytes:
            raise MalformedOfficialSourceResponse("Provider response exceeded the size limit")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MalformedOfficialSourceResponse("Provider response was not valid JSON") from None
        if not isinstance(payload, dict):
            raise MalformedOfficialSourceResponse("Provider response had an invalid shape")
        web_results = payload.get("web", {}).get("results") if isinstance(payload.get("web"), dict) else None
        if not isinstance(web_results, list):
            raise MalformedOfficialSourceResponse("Provider response had an invalid shape")

        candidates: list[Mapping[str, object]] = []
        retrieved_at = datetime.now(timezone.utc)
        for item in web_results[:self.result_count]:
            if not isinstance(item, dict):
                raise MalformedOfficialSourceResponse("Provider result had an invalid shape")
            url = item.get("url")
            title = item.get("title")
            excerpt = item.get("description")
            if not isinstance(url, str) or not self._allowed(url):
                continue
            if not isinstance(title, str) or not isinstance(excerpt, str) or not excerpt.strip():
                raise MalformedOfficialSourceResponse("Provider result omitted required provenance")
            candidates.append({
                "title": title,
                "url": url,
                "retrieved_at": retrieved_at,
                "jurisdiction": jurisdiction,
                "excerpt": excerpt,
                "verification_status": "unverified",
            })
        return candidates


class ApprovedOfficialSourceProvider(Protocol):
    """Adapter for a team-configured provider restricted to explicitly approved sources."""

    def search(
        self, query: str, jurisdiction: str | None, *, timeout_seconds: float
    ) -> Sequence[Mapping[str, object]]: ...


class OfficialSourceRetriever:
    """Validate provider output and retain discoveries without promoting them as facts."""

    def __init__(
        self,
        provider: ApprovedOfficialSourceProvider,
        *,
        approved_hosts: Sequence[str],
        timeout_seconds: float = 5.0,
    ) -> None:
        hosts = {host.strip().lower().rstrip(".") for host in approved_hosts if host.strip()}
        if not hosts or any("/" in host or ":" in host for host in hosts):
            raise ValueError("Configure one or more approved source hostnames")
        if timeout_seconds <= 0:
            raise ValueError("Source retrieval timeout must be positive")
        self.provider = provider
        self.approved_hosts = hosts
        self.timeout_seconds = timeout_seconds

    def _is_approved_url(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        return any(host == approved or host.endswith("." + approved) for approved in self.approved_hosts)

    @staticmethod
    def _find_conflicts(candidates: list[OfficialSourceCandidate]) -> list[SourceConflict]:
        groups: dict[str, list[int]] = defaultdict(list)
        for index, candidate in enumerate(candidates):
            if candidate.claim_key:
                groups[candidate.claim_key].append(index)
        conflicts = []
        for claim_key, indices in groups.items():
            excerpts = {" ".join(candidates[index].excerpt.casefold().split()) for index in indices}
            if len(indices) > 1 and len(excerpts) > 1:
                conflicts.append(SourceConflict(claim_key=claim_key, candidate_indices=indices))
        return conflicts

    def retrieve(
        self, query: str, *, jurisdiction: str | None = None
    ) -> OfficialSourceResult:
        """Make one provider attempt with a required timeout; failures stay non-factual."""
        try:
            raw_results = self.provider.search(
                query, jurisdiction, timeout_seconds=self.timeout_seconds
            )
        except MalformedOfficialSourceResponse:
            return OfficialSourceResult(
                status="invalid_results", warnings=["Official-source provider returned malformed results."]
            )
        except TimeoutError:
            return OfficialSourceResult(
                status="unavailable", warnings=["Approved official-source retrieval timed out."]
            )
        except Exception:
            return OfficialSourceResult(
                status="unavailable", warnings=["Approved official-source retrieval is unavailable."]
            )

        if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes)):
            return OfficialSourceResult(
                status="invalid_results", warnings=["Official-source provider returned malformed results."]
            )

        candidates: list[OfficialSourceCandidate] = []
        try:
            for raw in raw_results:
                candidate = OfficialSourceCandidate.model_validate(raw)
                if not self._is_approved_url(candidate.url):
                    raise ValueError("source host is not approved")
                candidates.append(candidate)
        except (ValidationError, ValueError, TypeError):
            return OfficialSourceResult(
                status="invalid_results", warnings=["Official-source provider returned invalid provenance."]
            )

        if not candidates:
            return OfficialSourceResult(status="no_results")
        return OfficialSourceResult(
            status="ok",
            candidates=candidates,
            conflicts=self._find_conflicts(candidates),
            warnings=["Retrieved source candidates are unverified discoveries; review before use."],
        )


def build_configured_official_source_retriever(settings) -> OfficialSourceRetriever | None:
    """Construct the supported live adapter only when every setting is explicitly present."""
    if (
        settings.official_source_provider != "brave"
        or not settings.brave_search_api_key
        or not settings.official_source_approved_hosts
    ):
        return None
    try:
        provider = BraveSearchProvider(
            settings.brave_search_api_key,
            approved_hosts=settings.official_source_approved_hosts,
        )
        return OfficialSourceRetriever(
            provider,
            approved_hosts=settings.official_source_approved_hosts,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        )
    except (TypeError, ValueError):
        return None
