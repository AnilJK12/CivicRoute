"""Adapter contract for maps-owned provider candidates and verified service capability."""

from collections.abc import Mapping, Sequence
from math import inf
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .url_validation import is_absolute_https_url


class ProviderSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def source_must_be_https(cls, value: str) -> str:
        if not is_absolute_https_url(value):
            raise ValueError("provider authorization source must be absolute HTTPS")
        return value


class ServiceCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    status: Literal["verified", "unverified"]
    sources: list[ProviderSource] = Field(min_length=1)


class ProviderCandidate(BaseModel):
    """Normalized adapter result; nearby location alone is not service authorization."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1)
    provider_name: str = Field(min_length=1)
    distance_km: float | None = Field(default=None, ge=0)
    service_capabilities: list[ServiceCapability]


class ProviderCandidateSource(Protocol):
    """Maps/integration adapter contract; no endpoint or provider data is owned here."""

    def find_nearby(self, location: str) -> Sequence[ProviderCandidate | Mapping[str, object]]: ...


def verified_capable_providers(
    candidates: Sequence[ProviderCandidate | Mapping[str, object]],
    service_id: str,
) -> list[ProviderCandidate]:
    """Filter by cited, verified service authorization before any distance ranking."""
    authorized: list[ProviderCandidate] = []
    for candidate in candidates:
        try:
            record = candidate if isinstance(candidate, ProviderCandidate) else ProviderCandidate.model_validate(candidate)
        except ValidationError:
            continue
        if any(
            capability.service_id == service_id and capability.status == "verified"
            for capability in record.service_capabilities
        ):
            authorized.append(record)
    return sorted(authorized, key=lambda item: item.distance_km if item.distance_km is not None else inf)
