"""Validate incoming service bundles without changing trusted runtime data."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .knowledge_base import KnowledgeBaseError, load_services
from .url_validation import is_absolute_https_url

T = TypeVar("T")
STALE_AFTER_DAYS = 365


class BundleFact(BaseModel, Generic[T]):
    """An explicit value state distinguishes absent knowledge from confirmed absence."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["verified", "unverified", "verified_unavailable"]
    value: T | None = None
    note: str | None = None
    source_urls: list[str] = Field(default_factory=list)

    @field_validator("source_urls")
    @classmethod
    def fact_sources_must_be_https(cls, values: list[str]) -> list[str]:
        if any(not is_absolute_https_url(value) for value in values):
            raise ValueError("fact source URLs must use absolute HTTPS")
        return values

    @model_validator(mode="after")
    def state_must_match_value(self) -> "BundleFact[T]":
        if self.status == "verified" and self.value is None:
            raise ValueError("verified fact must include a value")
        if self.status == "verified" and isinstance(self.value, str) and not self.value.strip():
            raise ValueError("verified text fact must not be blank")
        if self.status == "verified" and isinstance(self.value, list) and not self.value:
            raise ValueError("verified list fact must not be empty; use verified_unavailable")
        if self.status == "verified_unavailable" and self.value is not None:
            raise ValueError("verified_unavailable fact must not include a value")
        if self.status == "verified_unavailable" and not (self.note and self.note.strip()):
            raise ValueError("verified_unavailable fact requires a provenance note")
        return self


class BundleSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    url: str | None = None
    retrieved_at: datetime | None = None
    publisher: str | None = None

    @field_validator("url")
    @classmethod
    def source_url_must_be_https_when_present(cls, value: str | None) -> str | None:
        if value is not None and not is_absolute_https_url(value):
            raise ValueError("source URL must use absolute HTTPS")
        return value

    @field_validator("retrieved_at")
    @classmethod
    def source_timestamp_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source retrieval date must include a timezone")
        return value.astimezone(timezone.utc) if value is not None else None


class BundleStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    text: str = Field(min_length=1)


class BundleDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    status: Literal["required", "conditional", "commonly_requested"]
    note: str | None = None


class BundleCharge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    amount: float | None = Field(default=None, ge=0)
    currency: str = "INR"
    status: Literal["verified", "unverified", "verified_unavailable"] = "unverified"
    note: str | None = None

    @model_validator(mode="after")
    def charge_status_must_match_amount(self) -> "BundleCharge":
        if self.status == "verified" and self.amount is None:
            raise ValueError("verified charge must include an explicit amount")
        if self.status == "verified_unavailable" and self.amount is not None:
            raise ValueError("verified unavailable charge must not include an amount")
        if self.status == "verified_unavailable" and not (self.note and self.note.strip()):
            raise ValueError("verified unavailable charge requires a note")
        return self


class BundleFee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float | None = Field(default=None, ge=0)
    currency: str = "INR"
    other_charges: list[BundleCharge] = Field(default_factory=list)


class BundleLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    url: str

    @field_validator("url")
    @classmethod
    def link_must_be_https(cls, value: str) -> str:
        if not is_absolute_https_url(value):
            raise ValueError("application links must use absolute HTTPS")
        return value


class BundleLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    address: str | None = None
    centre_id: str | None = None
    district: str | None = None
    taluk: str | None = None
    pincode: str | None = None


class BundleJurisdiction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str | None = None
    district: str | None = None
    taluk: str | None = None


class BundleServiceRecord(BaseModel):
    """Flexible source record; it is not the runtime ServiceRecord or trusted by import."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    department: str | None = None
    responsible_authority: str | None = None
    category: str | None = None
    description: BundleFact[str] | None = None
    application_procedure: BundleFact[list[BundleStep]] | None = None
    documents: BundleFact[list[BundleDocument]] | None = None
    eligibility: BundleFact[list[str]] | None = None
    fees: BundleFact[BundleFee] | None = None
    online_application_links: BundleFact[list[BundleLink]] | None = None
    offline_application_locations: BundleFact[list[BundleLocation]] | None = None
    service_channels: list[str] = Field(default_factory=list)
    authorised_service_centres: list[str] = Field(default_factory=list)
    jurisdiction: BundleJurisdiction = Field(default_factory=BundleJurisdiction)
    sources: list[BundleSource] = Field(default_factory=list)
    verification_status: Literal["verified", "unverified", "pending_review", "stale"] = "pending_review"
    last_verified: date | None = None

    @field_validator("aliases", "service_channels", "authorised_service_centres")
    @classmethod
    def labels_must_not_be_blank(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("labels must not be blank")
        return values

    @field_validator("application_procedure")
    @classmethod
    def steps_must_have_unique_order(cls, fact: BundleFact[list[BundleStep]] | None):
        if fact and fact.value is not None:
            orders = [step.order for step in fact.value]
            if len(orders) != len(set(orders)):
                raise ValueError("application step order values must be unique")
        return fact

    @field_validator("fees")
    @classmethod
    def verified_fee_must_have_amount(cls, fact: BundleFact[BundleFee] | None):
        if fact and fact.status == "verified" and fact.value is not None and fact.value.amount is None:
            raise ValueError("verified fee must include an explicit amount; use verified_unavailable otherwise")
        return fact


class BundleCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    authorization_status: Literal["verified", "unverified", "not_authorized", "verified_unavailable"]
    source_urls: list[str] = Field(default_factory=list)

    @field_validator("source_urls")
    @classmethod
    def capability_sources_must_be_https(cls, values: list[str]) -> list[str]:
        if any(not is_absolute_https_url(value) for value in values):
            raise ValueError("capability source URLs must use absolute HTTPS")
        return values


class BundleCentreRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str = Field(min_length=1)
    provider_type: str | None = None
    department: str | None = None
    address: str | None = None
    district: str | None = None
    taluk: str | None = None
    pincode: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    service_capabilities: list[BundleCapability] = Field(default_factory=list)
    jurisdiction: BundleJurisdiction = Field(default_factory=BundleJurisdiction)
    sources: list[BundleSource] = Field(default_factory=list)
    verification_status: Literal["verified", "unverified", "pending_review", "stale"] = "pending_review"
    last_verified: date | None = None

    @model_validator(mode="after")
    def coordinate_pair_must_be_complete(self) -> "BundleCentreRecord":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("centre coordinates require both latitude and longitude")
        return self


class BundleEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    services: list[object] = Field(default_factory=list)
    centres: list[object] = Field(default_factory=list)


class QualityIssue(BaseModel):
    severity: Literal["review", "rejected"]
    code: str
    field: str | None = None
    message: str


class RecordReport(BaseModel):
    record_type: Literal["service", "centre"]
    index: int
    record_id: str | None = None
    name: str | None = None
    disposition: Literal["pending_review", "rejected"]
    issues: list[QualityIssue] = Field(default_factory=list)


class BundleQualityReport(BaseModel):
    schema_version: Literal[1] = 1
    status: Literal["valid", "review_required", "invalid"]
    service_count: int
    centre_count: int
    rejected_count: int
    review_required_count: int
    records: list[RecordReport] = Field(default_factory=list)
    fatal_error: str | None = None


class StagedService(BaseModel):
    record: BundleServiceRecord
    import_state: Literal["pending_review"] = "pending_review"


class StagedCentre(BaseModel):
    record: BundleCentreRecord
    import_state: Literal["pending_review"] = "pending_review"


class BundleIngestionResult(BaseModel):
    services: list[StagedService] = Field(default_factory=list)
    centres: list[StagedCentre] = Field(default_factory=list)
    report: BundleQualityReport


def _sanitized_validation_issues(error: ValidationError) -> list[QualityIssue]:
    return [
        QualityIssue(
            severity="rejected",
            code="invalid_field",
            field=".".join(str(part) for part in item.get("loc", ())) or None,
            message="missing required value" if item.get("type") == "missing" else "invalid value or type",
        )
        for item in error.errors()
    ]


def _normal(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _source_quality_issues(record: BundleServiceRecord | BundleCentreRecord) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if not record.sources:
        issues.append(QualityIssue(
            severity="review", code="missing_source_provenance", field="sources",
            message="record has no source provenance",
        ))
    for index, source in enumerate(record.sources):
        for field_name in ("title", "url", "retrieved_at"):
            value = getattr(source, field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                issues.append(QualityIssue(
                    severity="review", code="incomplete_source_provenance",
                    field=f"sources.{index}.{field_name}",
                    message="source provenance field is missing",
                ))
    source_urls = {source.url for source in record.sources if source.url}
    if isinstance(record, BundleServiceRecord):
        facts = (
            "description", "application_procedure", "documents", "eligibility", "fees",
            "online_application_links", "offline_application_locations",
        )
        for field_name in facts:
            fact = getattr(record, field_name)
            if fact and fact.status == "unverified":
                issues.append(QualityIssue(
                    severity="review", code="unverified_fact", field=field_name,
                    message="fact is explicitly marked unverified",
                ))
            if fact and fact.status in {"verified", "verified_unavailable"}:
                missing_refs = [url for url in fact.source_urls if url not in source_urls]
                if not fact.source_urls or missing_refs:
                    issues.append(QualityIssue(
                        severity="review", code="missing_fact_provenance", field=field_name,
                        message="verified information must reference a source record in this bundle",
                    ))
    else:
        for index, capability in enumerate(record.service_capabilities):
            if capability.authorization_status == "unverified":
                issues.append(QualityIssue(
                    severity="review", code="unverified_service_capability",
                    field=f"service_capabilities.{index}",
                    message="centre service authorization is explicitly unverified",
                ))
            if capability.authorization_status in {"verified", "not_authorized", "verified_unavailable"}:
                missing_refs = [url for url in capability.source_urls if url not in source_urls]
                if not capability.source_urls or missing_refs:
                    issues.append(QualityIssue(
                        severity="review", code="missing_capability_provenance",
                        field=f"service_capabilities.{index}",
                        message="service authorization claim must reference a source record in this bundle",
                    ))
    return issues


def _record_quality_issues(
    record: BundleServiceRecord | BundleCentreRecord, today: date
) -> list[QualityIssue]:
    issues = _source_quality_issues(record)
    if isinstance(record, BundleServiceRecord):
        for field_name, value in (
            ("department", record.department), ("responsible_authority", record.responsible_authority),
            ("category", record.category),
            ("application_procedure", record.application_procedure), ("documents", record.documents),
            ("eligibility", record.eligibility), ("fees", record.fees),
            ("service_channels", record.service_channels),
        ):
            if value is None or (isinstance(value, str) and not value.strip()) or value == []:
                issues.append(QualityIssue(
                    severity="review", code="missing_important_field", field=field_name,
                    message="important field is absent; verify whether information is unavailable",
                ))
        if not any((record.jurisdiction.state, record.jurisdiction.district, record.jurisdiction.taluk)):
            issues.append(QualityIssue(
                severity="review", code="missing_important_field", field="jurisdiction",
                message="jurisdiction is absent",
            ))
    else:
        for field_name, value in (
            ("provider_type", record.provider_type), ("district", record.district or record.jurisdiction.district),
            ("service_capabilities", record.service_capabilities),
        ):
            if value is None or value == []:
                issues.append(QualityIssue(
                    severity="review", code="missing_important_field", field=field_name,
                    message="important centre field is absent; review before claiming coverage",
                ))
    if record.verification_status != "verified":
        issues.append(QualityIssue(
            severity="review", code="record_not_verified", field="verification_status",
            message="record is not explicitly marked verified",
        ))
    if record.last_verified is None:
        issues.append(QualityIssue(
            severity="review", code="missing_verification_date", field="last_verified",
            message="verification date is missing",
        ))
    elif record.last_verified > today:
        issues.append(QualityIssue(
            severity="review", code="future_verification_date", field="last_verified",
            message="verification date is in the future",
        ))
    elif today - record.last_verified > timedelta(days=STALE_AFTER_DAYS):
        issues.append(QualityIssue(
            severity="review", code="stale_verification", field="last_verified",
            message=f"verification date is older than {STALE_AFTER_DAYS} days",
        ))
    return issues


def ingest_bundle(
    payload: object,
    *,
    trusted_services: dict[str, object] | None = None,
    today: date | None = None,
) -> BundleIngestionResult:
    """Validate a JSON-shaped bundle into pending staging records, never trusted records."""
    today = today or date.today()
    try:
        envelope = BundleEnvelope.model_validate(payload)
    except ValidationError as exc:
        issue = _sanitized_validation_issues(exc)
        report = BundleQualityReport(
            status="invalid", service_count=0, centre_count=0, rejected_count=1,
            review_required_count=0,
            records=[RecordReport(
                record_type="service", index=0, disposition="rejected", issues=issue
            )],
            fatal_error="Bundle envelope is missing or invalid",
        )
        return BundleIngestionResult(report=report)

    staged_services: list[StagedService] = []
    staged_centres: list[StagedCentre] = []
    reports: list[RecordReport] = []
    service_rows: list[tuple[int, BundleServiceRecord, list[QualityIssue]]] = []
    centre_rows: list[tuple[int, BundleCentreRecord, list[QualityIssue]]] = []

    for index, raw in enumerate(envelope.services):
        try:
            record = BundleServiceRecord.model_validate(raw)
        except ValidationError as exc:
            reports.append(RecordReport(
                record_type="service", index=index,
                record_id=raw.get("id") if isinstance(raw, dict) and isinstance(raw.get("id"), str) else None,
                name=raw.get("name") if isinstance(raw, dict) and isinstance(raw.get("name"), str) else None,
                disposition="rejected", issues=_sanitized_validation_issues(exc),
            ))
            continue
        issues = _record_quality_issues(record, today)
        service_rows.append((index, record, issues))

    for index, raw in enumerate(envelope.centres):
        try:
            record = BundleCentreRecord.model_validate(raw)
        except ValidationError as exc:
            reports.append(RecordReport(
                record_type="centre", index=index,
                record_id=raw.get("id") if isinstance(raw, dict) and isinstance(raw.get("id"), str) else None,
                name=raw.get("name") if isinstance(raw, dict) and isinstance(raw.get("name"), str) else None,
                disposition="rejected", issues=_sanitized_validation_issues(exc),
            ))
            continue
        issues = _record_quality_issues(record, today)
        centre_rows.append((index, record, issues))

    service_groups: dict[str, list[int]] = {}
    for row_index, (_, record, _) in enumerate(service_rows):
        service_groups.setdefault(record.id, []).append(row_index)
    existing = trusted_services or {}
    for row_index, (_, record, _) in enumerate(service_rows):
        same_id = service_groups[record.id]
        if len(same_id) > 1:
            differences = set()
            for other_index in same_id:
                if other_index == row_index:
                    continue
                other = service_rows[other_index][1]
                for field_name in type(record).model_fields:
                    left = getattr(record, field_name)
                    right = getattr(other, field_name)
                    if left is not None and right is not None and left != right:
                        differences.add(field_name)
            service_rows[row_index][2].append(QualityIssue(
                severity="review", code="duplicate_service_id", field="id",
                message=("duplicate service ID has conflicting fields: " + ", ".join(sorted(differences)))
                if differences else "duplicate service ID appears more than once",
            ))
        trusted = existing.get(record.id)
        if trusted is not None:
            trusted_dump = trusted.model_dump(mode="json") if hasattr(trusted, "model_dump") else trusted
            incoming = record.model_dump(mode="json")
            if isinstance(trusted_dump, dict):
                conflict_fields = [
                    key for key, value in incoming.items()
                    if value not in (None, [], {}) and trusted_dump.get(key) not in (None, [], {})
                    and value != trusted_dump.get(key)
                ]
            else:
                conflict_fields = ["unavailable existing record shape"]
            service_rows[row_index][2].append(QualityIssue(
                severity="review", code="trusted_record_conflict", field="id",
                message=("incoming record differs from trusted record in: " + ", ".join(conflict_fields))
                if conflict_fields else "service ID already exists in trusted catalogue; preserve and review before integration",
            ))

    # Different IDs with the same normalized name or alias are possible duplicates, never auto-merged.
    identity_groups: dict[str, list[int]] = {}
    for row_index, (_, record, _) in enumerate(service_rows):
        for label in {record.name, *record.aliases}:
            normalized = _normal(label)
            if normalized:
                identity_groups.setdefault(normalized, []).append(row_index)
    for rows in identity_groups.values():
        unique_rows = sorted(set(rows))
        if len(unique_rows) < 2:
            continue
        unique_ids = {service_rows[index][1].id for index in unique_rows}
        if len(unique_ids) > 1:
            for index in unique_rows:
                service_rows[index][2].append(QualityIssue(
                    severity="review", code="possible_duplicate_service", field="name/aliases",
                    message="service name or alias also appears under another service ID",
                ))

    centre_ids: dict[str, list[int]] = {}
    centre_signatures: dict[tuple[str, str, str], list[int]] = {}
    for row_index, (_, record, _) in enumerate(centre_rows):
        if record.id:
            centre_ids.setdefault(record.id.casefold(), []).append(row_index)
        region = _normal(record.district or record.jurisdiction.district)
        taluk = _normal(record.taluk or record.jurisdiction.taluk)
        name = _normal(record.name)
        if region and name:
            centre_signatures.setdefault((name, region, taluk), []).append(row_index)
    duplicate_centre_rows = {i for group in centre_ids.values() if len(group) > 1 for i in group}
    duplicate_centre_rows.update(i for group in centre_signatures.values() if len(group) > 1 for i in group)
    for row_index in duplicate_centre_rows:
        centre_rows[row_index][2].append(QualityIssue(
            severity="review", code="possible_duplicate_centre", field="id/name/location",
            message="centre ID or normalized name and locality appears more than once",
        ))

    for index, record, issues in service_rows:
        disposition: Literal["pending_review", "rejected"] = "pending_review"
        staged_services.append(StagedService(record=record))
        reports.append(RecordReport(
            record_type="service", index=index, record_id=record.id, name=record.name,
            disposition=disposition, issues=issues,
        ))
    for index, record, issues in centre_rows:
        staged_centres.append(StagedCentre(record=record))
        reports.append(RecordReport(
            record_type="centre", index=index, record_id=record.id, name=record.name,
            disposition="pending_review", issues=issues,
        ))

    reports.sort(key=lambda item: (item.record_type, item.index))
    rejected_count = sum(item.disposition == "rejected" for item in reports)
    review_count = sum(
        item.disposition == "pending_review" and bool(item.issues) for item in reports
    )
    report_status = "invalid" if rejected_count else "review_required" if review_count else "valid"
    return BundleIngestionResult(
        services=staged_services,
        centres=staged_centres,
        report=BundleQualityReport(
            status=report_status,
            service_count=len(envelope.services),
            centre_count=len(envelope.centres),
            rejected_count=rejected_count,
            review_required_count=review_count,
            records=reports,
        ),
    )


def ingest_bundle_file(
    path: Path,
    *,
    trusted_services: dict[str, object] | None = None,
    today: date | None = None,
) -> BundleIngestionResult:
    """Read JSON bundle without changing its file or the trusted catalogue."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        report = BundleQualityReport(
            status="invalid", service_count=0, centre_count=0, rejected_count=0,
            review_required_count=0, fatal_error="Bundle file is unavailable or invalid JSON",
        )
        return BundleIngestionResult(report=report)
    return ingest_bundle(payload, trusted_services=trusted_services, today=today)


def write_pending_stage(result: BundleIngestionResult, path: Path) -> None:
    """Write a new, explicitly non-runtime artifact without ever replacing existing files."""
    payload = {
        "schema_version": 1,
        "bundle_state": "pending_review_not_trusted",
        "services": [item.model_dump(mode="json") for item in result.services],
        "centres": [item.model_dump(mode="json") for item in result.centres],
    }
    try:
        with Path(path).open("x", encoding="utf-8") as stage_file:
            json.dump(payload, stage_file, ensure_ascii=False, indent=2)
            stage_file.write("\n")
    except FileExistsError:
        raise FileExistsError("staging output already exists; choose a new path") from None
    except OSError:
        raise OSError("staging output could not be written") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a service bundle without trusting or importing it into runtime data.")
    parser.add_argument("bundle", type=Path, help="JSON bundle file supplied by the data teammate")
    parser.add_argument("--report", type=Path, help="optional report output path; must not already exist")
    parser.add_argument("--staging-output", type=Path, help="optional pending-review output path; must not already exist")
    args = parser.parse_args(argv)
    try:
        trusted_services = load_services()
    except KnowledgeBaseError:
        parser.error("existing trusted service catalogue is unavailable or invalid")
    result = ingest_bundle_file(args.bundle, trusted_services=trusted_services)
    output = result.report.model_dump_json(indent=2)
    if args.report:
        try:
            with args.report.open("x", encoding="utf-8") as report_file:
                report_file.write(output + "\n")
        except FileExistsError:
            parser.error("report output already exists; choose a new path")
        except OSError:
            parser.error("report output could not be written")
    if args.staging_output:
        try:
            write_pending_stage(result, args.staging_output)
        except (FileExistsError, OSError) as exc:
            parser.error(str(exc))
    else:
        print(output)
    return 0 if result.report.status in {"valid", "review_required"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
