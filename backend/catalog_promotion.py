"""Explicit, reviewed promotion from a versioned bundle to the trusted service catalogue."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .config import settings
from .data_ingestion import BundleCentreRecord, BundleServiceRecord, ingest_bundle_file
from .knowledge_base import KnowledgeBaseError, ServiceRecord, load_services

_PROMOTION_LOCK = threading.RLock()
_BLOCKING_ISSUES = {
    "incomplete_source_provenance", "missing_source_provenance", "missing_fact_provenance",
    "unverified_fact", "record_not_verified", "stale_verification", "future_verification_date",
    "duplicate_service_id", "possible_duplicate_service", "unverified_service_capability",
    "missing_capability_provenance", "possible_duplicate_centre",
}


class PromotionItem(BaseModel):
    record_type: Literal["service", "centre"]
    record_id: str | None = None
    name: str | None = None
    reason: str


class PromotionReport(BaseModel):
    status: Literal["complete", "review_required", "failed"]
    imported: list[PromotionItem] = Field(default_factory=list)
    rejected: list[PromotionItem] = Field(default_factory=list)
    skipped: list[PromotionItem] = Field(default_factory=list)
    review_required: list[PromotionItem] = Field(default_factory=list)
    backup_path: str | None = None
    error: str | None = None


def _runtime_record(record: BundleServiceRecord, *, reviewer: str, approval_note: str) -> ServiceRecord:
    """Map only reviewed facts supported by the current runtime schema; preserve full bundle."""
    steps_fact = record.application_procedure
    if not steps_fact or steps_fact.status != "verified" or not steps_fact.value:
        raise ValueError("verified application procedure is required by the runtime schema")
    docs_fact = record.documents
    documents = docs_fact.value if docs_fact and docs_fact.status == "verified" and docs_fact.value else []
    fee_fact = record.fees
    fee_verified = bool(fee_fact and fee_fact.status == "verified" and fee_fact.value)
    fee_value = fee_fact.value if fee_verified else None
    fee_status = "verified" if fee_verified and fee_value.amount is not None else "not_verified"
    fee_note = fee_fact.note if fee_fact else None
    if fee_status == "not_verified" and fee_note is None:
        fee_note = "Fee is not verified in the bundle."

    links_fact = record.online_application_links
    links = links_fact.value if links_fact and links_fact.status == "verified" and links_fact.value else []
    if links_fact and links_fact.status == "verified_unavailable":
        online = {"available": False, "url": None, "status": "verified_unavailable"}
    elif links:
        online = {"available": True, "url": links[0].url, "status": "verified"}
    else:
        online = {"available": None, "url": None, "status": "not_verified"}

    sources = [{
        "title": source.title,
        "url": source.url,
        "publisher": source.publisher,
        "accessed_on": source.retrieved_at.date().isoformat(),
    } for source in record.sources]
    jurisdiction = [
        value for value in (
            record.jurisdiction.state, record.jurisdiction.district, record.jurisdiction.taluk
        ) if value
    ]
    return ServiceRecord.model_validate({
        "id": record.id,
        "name": record.name,
        "aliases": record.aliases,
        "departments": [value for value in (record.department, record.responsible_authority) if value],
        "categories": [record.category] if record.category else [],
        "service_channels": record.service_channels,
        "task_types": [],
        "jurisdiction": jurisdiction,
        "provider_types": [],
        "steps": {"en": [step.text for step in sorted(steps_fact.value, key=lambda step: step.order)]},
        "documents": [item.model_dump() for item in documents],
        "fee": {
            "amount": fee_value.amount if fee_status == "verified" else None,
            "currency": fee_value.currency if fee_value else "INR",
            "status": fee_status,
            "note": fee_note,
        },
        "online_option": online,
        "official_links": [item.model_dump() for item in links],
        "office_type": None,
        "grievance_route": None,
        "sources": sources,
        "last_verified": record.last_verified,
        "bundle_data": record.model_dump(mode="json"),
        "promotion_approval": {
            "reviewer": reviewer.strip(),
            "note": approval_note.strip(),
            "approved_at": datetime.now(timezone.utc).isoformat(),
        },
    })


def _atomic_catalog_write(path: Path, raw_catalog: dict, original_bytes: bytes) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.backup-{stamp}")
    temp_path: Path | None = None
    try:
        if path.read_bytes() != original_bytes:
            raise OSError("catalogue changed during promotion")
        with backup.open("xb") as backup_file:
            backup_file.write(original_bytes)
            backup_file.flush()
            os.fsync(backup_file.fileno())
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(raw_catalog, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
        return backup
    except OSError:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise OSError("trusted catalogue could not be safely updated") from None


def promote_bundle_file(
    bundle_path: Path,
    *,
    approved_ids: set[str],
    reviewer: str,
    approval_note: str,
    catalog_path: Path | None = None,
    today: date | None = None,
) -> PromotionReport:
    """Promote only explicitly approved, provenance-complete services; centre data stays separate."""
    if not reviewer.strip() or not approval_note.strip():
        return PromotionReport(status="failed", error="Reviewer identity and approval note are required")
    target = Path(catalog_path or settings.catalog_path)
    try:
        original_bytes = target.read_bytes()
        raw = json.loads(original_bytes)
        if not isinstance(raw, dict) or not isinstance(raw.get("services"), list):
            raise ValueError
        existing = load_services(target)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KnowledgeBaseError):
        return PromotionReport(status="failed", error="Trusted catalogue is unavailable or invalid")
    if not isinstance(raw.get("discoveries", []), list):
        return PromotionReport(status="failed", error="Trusted catalogue discovery section is invalid")
    existing_centres_raw = raw.get("centres", [])
    if not isinstance(existing_centres_raw, list):
        return PromotionReport(status="failed", error="Trusted centre section is invalid")
    try:
        existing_centres = [BundleCentreRecord.model_validate(item) for item in existing_centres_raw]
    except Exception:
        return PromotionReport(status="failed", error="Trusted centre section is invalid")

    ingestion = ingest_bundle_file(bundle_path, trusted_services=existing, today=today)
    report = PromotionReport(status="complete")
    if ingestion.report.fatal_error:
        return PromotionReport(status="failed", error="Supplied bundle is unavailable or invalid")
    # Valid pending quality rows and staged records preserve input order, including gaps for invalid rows.
    staged_services: dict[int, BundleServiceRecord] = {}
    valid_rows = [
        row for row in ingestion.report.records
        if row.record_type == "service" and row.disposition == "pending_review"
    ]
    for row, staged in zip(valid_rows, ingestion.services):
        staged_services[row.index] = staged.record
    valid_centre_rows = [
        row for row in ingestion.report.records
        if row.record_type == "centre" and row.disposition == "pending_review"
    ]
    staged_centres = {
        row.index: staged.record for row, staged in zip(valid_centre_rows, ingestion.centres)
    }

    append_records: list[dict] = []
    append_centres: list[dict] = []
    rejected_indices = {
        row.index for row in ingestion.report.records
        if row.record_type == "service" and row.disposition == "rejected"
    }
    for row in ingestion.report.records:
        if row.record_type == "centre":
            if row.disposition == "rejected":
                report.rejected.append(PromotionItem(
                    record_type="centre", record_id=row.record_id, name=row.name,
                    reason="Centre record failed schema validation; see bundle quality report.",
                ))
                continue
            centre = staged_centres.get(row.index)
            if centre is None:
                report.rejected.append(PromotionItem(
                    record_type="centre", record_id=row.record_id, name=row.name,
                    reason="Validated centre record could not be matched to its quality report.",
                ))
                continue
            if centre.id not in approved_ids:
                report.skipped.append(PromotionItem(
                    record_type="centre", record_id=centre.id, name=centre.name,
                    reason="Explicit record-level approval is missing.",
                ))
                continue
            centre_blocking = [issue for issue in row.issues if issue.code in _BLOCKING_ISSUES]
            if centre_blocking or centre.verification_status != "verified":
                report.review_required.append(PromotionItem(
                    record_type="centre", record_id=centre.id, name=centre.name,
                    reason="; ".join(sorted({issue.message for issue in centre_blocking})) or "Centre record is not verified.",
                ))
                continue
            unlinked_capabilities = [
                item.service_id for item in centre.service_capabilities
                if item.service_id not in existing
            ]
            if unlinked_capabilities:
                report.review_required.append(PromotionItem(
                    record_type="centre", record_id=centre.id, name=centre.name,
                    reason="Centre capability references service IDs absent from the trusted catalogue: "
                    + ", ".join(sorted(set(unlinked_capabilities))),
                ))
                continue
            duplicate_id = next((item for item in existing_centres if centre.id and item.id == centre.id), None)
            if duplicate_id is not None:
                if duplicate_id.model_dump(mode="json") == centre.model_dump(mode="json"):
                    report.skipped.append(PromotionItem(
                        record_type="centre", record_id=centre.id, name=centre.name,
                        reason="Identical trusted centre already exists; repeated import is idempotent.",
                    ))
                else:
                    report.rejected.append(PromotionItem(
                        record_type="centre", record_id=centre.id, name=centre.name,
                        reason="Centre ID already exists with different trusted data.",
                    ))
                continue
            append_centres.append(centre.model_dump(mode="json"))
            report.imported.append(PromotionItem(
                record_type="centre", record_id=centre.id, name=centre.name,
                reason=f"Explicitly approved by {reviewer.strip()}: {approval_note.strip()}",
            ))
            continue
        if row.index in rejected_indices:
            report.rejected.append(PromotionItem(
                record_type="service", record_id=row.record_id, name=row.name,
                reason="Bundle record failed schema validation; see bundle quality report.",
            ))
            continue
        record = staged_services.get(row.index)
        if record is None:
            report.rejected.append(PromotionItem(
                record_type="service", record_id=row.record_id, name=row.name,
                reason="Validated bundle record could not be matched to its quality report.",
            ))
            continue
        blocking = [issue for issue in row.issues if issue.code in _BLOCKING_ISSUES]
        if record.id not in approved_ids:
            report.skipped.append(PromotionItem(
                record_type="service", record_id=record.id, name=record.name,
                reason="Explicit record-level approval is missing.",
            ))
            continue
        if blocking or record.verification_status != "verified":
            reason = "; ".join(sorted({issue.message for issue in blocking})) or "Source record is not verified."
            report.review_required.append(PromotionItem(
                record_type="service", record_id=record.id, name=record.name, reason=reason,
            ))
            continue
        try:
            runtime = _runtime_record(record, reviewer=reviewer, approval_note=approval_note)
        except (ValueError, TypeError):
            report.review_required.append(PromotionItem(
                record_type="service", record_id=record.id, name=record.name,
                reason="Bundle lacks verified information required by the runtime schema.",
            ))
            continue
        previous = existing.get(record.id)
        if previous is not None:
            previous_data = previous.model_dump(mode="json")
            runtime_data = runtime.model_dump(mode="json")
            previous_data.pop("promotion_approval", None)
            runtime_data.pop("promotion_approval", None)
            if previous_data == runtime_data:
                report.skipped.append(PromotionItem(
                    record_type="service", record_id=record.id, name=record.name,
                    reason="Identical trusted record already exists; repeated import is idempotent.",
                ))
            else:
                report.rejected.append(PromotionItem(
                    record_type="service", record_id=record.id, name=record.name,
                    reason="ID already exists with different trusted facts; no overwrite was performed.",
                ))
            continue
        append_records.append(runtime.model_dump(mode="json"))
        report.imported.append(PromotionItem(
            record_type="service", record_id=record.id, name=record.name,
            reason=f"Explicitly approved by {reviewer.strip()}: {approval_note.strip()}",
        ))

    reported_ids = {
        item.record_id for item in report.imported + report.skipped + report.rejected + report.review_required
    }
    for unknown_id in sorted(approved_ids - reported_ids):
        report.skipped.append(PromotionItem(
            record_type="service", record_id=unknown_id,
            reason="Approval refers to no valid service record in the supplied bundle.",
        ))

    if append_records or append_centres:
        raw["services"].extend(append_records)
        if append_centres:
            raw.setdefault("centres", []).extend(append_centres)
        with _PROMOTION_LOCK:
            try:
                backup = _atomic_catalog_write(target, raw, original_bytes)
            except OSError:
                return PromotionReport(
                    status="failed", rejected=report.rejected, skipped=report.skipped,
                    review_required=report.review_required,
                    error="Atomic catalogue write or backup failed; no promotion was reported as complete",
                )
        report.backup_path = str(backup)
    if report.review_required:
        report.status = "review_required"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Promote explicitly reviewed service records into the runtime catalogue.")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--approve", action="append", default=[], help="service ID reviewed and approved; repeat per record")
    parser.add_argument("--reviewer", required=True, help="reviewer name or team identifier")
    parser.add_argument("--approval-note", required=True, help="reason for this explicit approval")
    parser.add_argument("--catalog", type=Path, default=settings.catalog_path)
    parser.add_argument("--report", type=Path, required=True, help="new report path; existing files are not replaced")
    args = parser.parse_args(argv)
    try:
        with args.report.open("x", encoding="utf-8") as stream:
            result = promote_bundle_file(
                args.bundle, approved_ids=set(args.approve), reviewer=args.reviewer,
                approval_note=args.approval_note, catalog_path=args.catalog,
            )
            stream.write(result.model_dump_json(indent=2) + "\n")
    except (FileExistsError, OSError):
        parser.error("promotion report could not be created without overwriting an existing file")
    print(result.model_dump_json(indent=2))
    return 0 if result.status in {"complete", "review_required"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
