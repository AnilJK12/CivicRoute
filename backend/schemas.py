"""Validated request and response models for POST /ask."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    language: Literal["en", "kn"] = "en"

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class DocumentEntry(BaseModel):
    name: str
    status: Literal["required", "conditional", "commonly_requested"]
    note: str | None = None


class Fee(BaseModel):
    amount: float | None
    currency: Literal["INR"]
    status: Literal["verified", "not_verified"]
    note: str | None


class OnlineOption(BaseModel):
    available: bool | None
    url: str | None
    status: str


class Source(BaseModel):
    title: str
    url: str


class EligibilityInfo(BaseModel):
    """Explicit eligibility state; only verified criteria may be included."""

    status: Literal["verified", "not_verified", "verified_unavailable"] = "not_verified"
    criteria: list[str] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def status_matches_criteria(self) -> "EligibilityInfo":
        if self.status == "verified" and not self.criteria:
            raise ValueError("verified eligibility requires criteria")
        if self.status != "verified" and self.criteria:
            raise ValueError("unverified eligibility must not include criteria")
        if self.status == "verified_unavailable" and not self.note:
            raise ValueError("verified unavailable eligibility requires a note")
        return self


class ResearchFinding(BaseModel):
    """A search-snippet lead tied to its source; never an authoritative requirement."""

    text: str
    source_title: str
    source_url: str
    information_status: Literal["Probable"] = "Probable"


class ResearchSource(BaseModel):
    title: str
    url: str
    excerpt: str
    verification_status: Literal["unverified"] = "unverified"


class ServiceResearch(BaseModel):
    """Optional compatible extension for source-backed but unverified research leads."""

    department: str
    identified_service: str
    information_status: Literal["Officially verified", "Probable", "Not found"]
    probable_documents: list[ResearchFinding] = Field(default_factory=list)
    probable_steps: list[ResearchFinding] = Field(default_factory=list)
    online_option: OnlineOption
    offline_option: str | None = None
    sources: list[ResearchSource] = Field(default_factory=list)
    note: str | None = None


class SupportedResponse(BaseModel):
    status: Literal["ok"]
    service_id: str
    service_name: str
    language: Literal["en", "kn"]
    steps: list[str]
    documents: list[DocumentEntry]
    fee: Fee
    eligibility: EligibilityInfo = Field(default_factory=EligibilityInfo)
    online_option: OnlineOption
    office_type: str | None
    grievance: str | None
    sources: list[Source]
    last_verified: str | None
    warnings: list[str]
    research: ServiceResearch | None = None


class UnsupportedResponse(BaseModel):
    status: Literal["unsupported"]
    service_id: Literal["unknown"] = "unknown"
    service_name: None = None
    message: str
    official_portal_url: str | None
    suggested_services: list[str]
    warnings: list[str]
    research: ServiceResearch | None = None


class ClarificationResponse(BaseModel):
    """Frontend-compatible follow-up response for essential intent details."""

    status: Literal["clarification_needed"]
    service_id: str | None = None
    service_name: str | None = None
    language: Literal["en", "kn"]
    question: str
    clarification_question: str
    missing_fields: list[Literal["service_id", "task_type", "locality"]]


class ServiceFields(BaseModel):
    """Legacy root-app catalog shape kept for compatibility with the existing pipeline."""

    documents: list[str]
    fees: list[dict[str, Any]]
    official_links: list[dict[str, str]]
    sources: list[dict[str, str]]
    last_verified: str


class AskResponse(BaseModel):
    """Legacy response model retained for the existing root app and pipeline."""

    service_id: str
    supported: bool
    language: Literal["en", "kn"]
    answer: str
    steps: list[str]
    service: ServiceFields | None

    @classmethod
    def model_validate_response(cls, value: Any) -> "AskResponse":
        return cls.model_validate(value)
