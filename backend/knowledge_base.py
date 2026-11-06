"""Strict, deterministic loading of teammate-supplied service knowledge."""

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import settings
from .url_validation import is_absolute_https_url

DocumentStatus = Literal["required", "conditional", "commonly_requested"]


class KnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    publisher: str | None = None
    accessed_on: date | None = None

    @field_validator("url")
    @classmethod
    def source_url_must_be_absolute_https(cls, value: str) -> str:
        if not is_absolute_https_url(value):
            raise ValueError("must be an absolute HTTPS URL")
        return value


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    status: DocumentStatus
    note: str | None = None


class KnowledgeOfficialLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def official_link_must_be_absolute_https(cls, value: str) -> str:
        if not is_absolute_https_url(value):
            raise ValueError("must be an absolute HTTPS URL")
        return value


class KnowledgeFee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float | None
    currency: Literal["INR"]
    status: Literal["verified", "not_verified"]
    note: str | None

    @model_validator(mode="after")
    def amount_must_match_verification_status(self) -> "KnowledgeFee":
        if self.status == "not_verified" and self.amount is not None:
            raise ValueError("unverified fee amount must be null")
        if self.status == "verified" and self.amount is None:
            raise ValueError("verified fee must include an amount")
        return self


class KnowledgeOnlineOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool | None
    url: str | None
    status: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def online_url_must_be_absolute_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not is_absolute_https_url(value):
            raise ValueError("must be an absolute HTTPS URL")
        return value


class ServiceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    service_channels: list[str] = Field(default_factory=list)
    task_types: list[str] = Field(default_factory=list)
    jurisdiction: list[str] = Field(default_factory=list)
    provider_types: list[str] = Field(default_factory=list)
    steps: dict[Literal["en", "kn"], list[str]] = Field(min_length=1)
    documents: list[KnowledgeDocument]
    fee: KnowledgeFee
    online_option: KnowledgeOnlineOption
    official_links: list[KnowledgeOfficialLink] = Field(default_factory=list)
    office_type: str | None
    grievance_route: str | None
    sources: list[KnowledgeSource] = Field(min_length=1)
    last_verified: date | None
    # Preserve richer approved bundle fields without exposing them in the `/ask` response.
    bundle_data: dict[str, Any] | None = None
    promotion_approval: dict[str, str] | None = None

    @field_validator("steps")
    @classmethod
    def require_nonempty_step_text(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if not any(step.strip() for steps in value.values() for step in steps):
            raise ValueError("at least one nonblank step is required")
        return value

    @field_validator("aliases", "departments", "categories", "service_channels", "task_types", "jurisdiction", "provider_types")
    @classmethod
    def list_values_must_be_nonblank(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("catalogue labels must not be blank")
        return value


def _error_fields(error: ValidationError) -> str:
    """Expose field paths and error categories, never rejected input values."""
    locations = []
    for item in error.errors():
        path = ".".join(str(part) for part in item.get("loc", ())) or "record"
        category = "missing" if item.get("type") == "missing" else "invalid"
        entry = f"{path} ({category})"
        if entry not in locations:
            locations.append(entry)
    return ", ".join(locations)


class KnowledgeBaseError(ValueError):
    """Sanitized catalog loading error suitable for application logs and API handling."""


class ServiceRecordProvider(Protocol):
    """Lookup interface for trusted bundled, reviewed-cache, or official-source adapters."""

    def get(self, service_id: str) -> ServiceRecord | dict | None: ...


class ReviewedCacheProvider(ServiceRecordProvider, Protocol):
    """Future adapter for records already reviewed and validated by the team."""


class OfficialOnlineProvider(ServiceRecordProvider, Protocol):
    """Future adapter for official sources; no web retrieval implementation exists yet."""


class BundledCatalogProvider:
    """Read only records validated from the trusted bundled `services` section."""

    def __init__(self, records: dict[str, ServiceRecord]):
        self.records = records

    def get(self, service_id: str) -> ServiceRecord | None:
        return self.records.get(service_id)


def retrieve_from_providers(
    service_id: str,
    providers: Sequence[ServiceRecordProvider],
) -> ServiceRecord | None:
    """Return first matching validated record; caller orders bundled, cache, then online."""
    for provider in providers:
        candidate = provider.get(service_id)
        if candidate is None:
            continue
        try:
            record = candidate if isinstance(candidate, ServiceRecord) else ServiceRecord.model_validate(candidate)
        except ValidationError as exc:
            raise KnowledgeBaseError("A retrieval source returned an invalid service record") from exc
        if record.id != service_id:
            raise KnowledgeBaseError("A retrieval source returned a mismatched service ID")
        return record
    return None


def load_services(path: Path | None = None) -> dict[str, ServiceRecord]:
    """Load and validate every record; accepts current object and legacy list envelopes."""
    catalog_path = path or settings.catalog_path
    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KnowledgeBaseError("Service knowledge file is unavailable or invalid JSON") from exc

    if isinstance(raw, list):
        records = raw
    elif isinstance(raw, dict) and isinstance(raw.get("services"), list):
        metadata = raw.get("_metadata", {})
        kind = metadata.get("kind") if isinstance(metadata, dict) else None
        if kind == "development_fixture" and metadata.get("verified") is not False:
            raise KnowledgeBaseError("Development catalog metadata must declare verified=false")
        if kind not in {"development_fixture", "verified_catalog"}:
            raise KnowledgeBaseError("Service knowledge file has an unsupported catalog kind")
        discoveries = raw.get("discoveries", [])
        if not isinstance(discoveries, list):
            raise KnowledgeBaseError("Unreviewed discoveries must be a separate list")
        records = raw["services"]
    else:
        raise KnowledgeBaseError("Service knowledge file must contain a services list")

    result: dict[str, ServiceRecord] = {}
    for index, record in enumerate(records):
        try:
            service = ServiceRecord.model_validate(record)
        except ValidationError as exc:
            label = record.get("id") if isinstance(record, dict) else None
            label = label if isinstance(label, str) else f"record {index}"
            raise KnowledgeBaseError(
                f"Service {label} is invalid: {_error_fields(exc)}"
            ) from exc
        if service.id in result:
            raise KnowledgeBaseError(f"Service {service.id} is duplicated")
        result[service.id] = service
    return result


def get_service(service_id: str, knowledge_base: dict[str, ServiceRecord]) -> ServiceRecord:
    """Retrieve only allowlisted IDs and fail clearly if a teammate entry is absent."""
    if not isinstance(service_id, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", service_id):
        raise KnowledgeBaseError("Requested service ID is not supported")
    try:
        return knowledge_base[service_id]
    except KeyError as exc:
        raise KnowledgeBaseError(f"Service {service_id} is missing from the knowledge file") from exc
