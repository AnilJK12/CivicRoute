"""JSON-backed review queue for public-source discoveries, separate from trusted services."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .official_sources import OfficialSourceCandidate, OfficialSourceResult
from .url_validation import is_absolute_https_url

ReviewState = Literal["pending_review", "verified", "rejected", "stale"]
VerificationStatus = Literal["unverified", "verified", "rejected", "stale"]
DEFAULT_STORE_PATH = Path(__file__).resolve().parent / "data" / "discoveries.json"


class StoredDiscovery(BaseModel):
    """Candidate provenance and human review state; this is not a ServiceRecord."""

    model_config = ConfigDict(extra="forbid")

    discovery_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    retrieved_at: datetime
    jurisdiction: str | None
    excerpt: str = Field(min_length=1)
    verification_status: VerificationStatus = "unverified"
    review_state: ReviewState = "pending_review"
    review_notes: str | None = Field(default=None, max_length=4000)
    reviewed_at: datetime | None = None
    claim_key: str | None = None
    conflicts_with: list[str] = Field(default_factory=list)

    @field_validator("title", "excerpt")
    @classmethod
    def provenance_text_must_be_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("discovery provenance must not be blank")
        return value

    @field_validator("jurisdiction", "claim_key", "review_notes")
    @classmethod
    def optional_text_must_be_null_or_nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("optional discovery text must be null or nonblank")
        return value

    @field_validator("url")
    @classmethod
    def url_must_be_absolute_https(cls, value: str) -> str:
        if not is_absolute_https_url(value):
            raise ValueError("discovery URL must be absolute HTTPS")
        return value

    @field_validator("retrieved_at", "reviewed_at")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("discovery timestamp must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def review_state_must_match_verification_status(self) -> "StoredDiscovery":
        expected = {
            "pending_review": "unverified",
            "verified": "verified",
            "rejected": "rejected",
            "stale": "stale",
        }[self.review_state]
        if self.verification_status != expected:
            raise ValueError("review state and verification status do not match")
        if self.review_state in {"verified", "rejected"} and (not self.reviewed_at or not self.review_notes):
            raise ValueError("final review states require a timestamp and review note")
        if any(item == self.discovery_id for item in self.conflicts_with):
            raise ValueError("a discovery cannot conflict with itself")
        return self


class _DiscoveryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    discoveries: list[StoredDiscovery] = Field(default_factory=list)


class DiscoveryStoreError(ValueError):
    """Sanitized error for the internal review store."""


class DiscoveryStore:
    """Persist untrusted candidates as JSON; no method promotes them into services.json."""

    _lock = threading.RLock()

    def __init__(self, path: Path = DEFAULT_STORE_PATH) -> None:
        self.path = Path(path)

    @staticmethod
    def _fingerprint(candidate: OfficialSourceCandidate | StoredDiscovery) -> tuple[str, str, str, str]:
        parts = urlsplit(candidate.url)
        canonical_url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))
        jurisdiction = (candidate.jurisdiction or "").casefold().strip()
        claim_key = (candidate.claim_key or "").casefold().strip()
        excerpt = " ".join(candidate.excerpt.casefold().split())
        return canonical_url, jurisdiction, claim_key, excerpt

    @staticmethod
    def _conflict_key(record: StoredDiscovery) -> tuple[str, str] | None:
        if not record.claim_key:
            return None
        return record.claim_key.casefold().strip(), (record.jurisdiction or "").casefold().strip()

    def _read(self) -> list[StoredDiscovery]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            document = _DiscoveryDocument.model_validate(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
            raise DiscoveryStoreError("Discovery review store is unavailable or malformed") from exc
        ids = [record.discovery_id for record in document.discoveries]
        fingerprints = [self._fingerprint(record) for record in document.discoveries]
        if len(ids) != len(set(ids)) or len(fingerprints) != len(set(fingerprints)):
            raise DiscoveryStoreError("Discovery review store contains duplicate records")
        return document.discoveries

    def _write(self, records: list[StoredDiscovery]) -> None:
        temp_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            document = _DiscoveryDocument(discoveries=records)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(document.model_dump_json(indent=2))
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, self.path)
        except (OSError, ValidationError, TypeError) as exc:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise DiscoveryStoreError("Discovery review store could not be written") from exc

    @staticmethod
    def _from_candidate(candidate: OfficialSourceCandidate) -> StoredDiscovery:
        return StoredDiscovery(
            discovery_id=uuid.uuid4().hex,
            title=candidate.title,
            url=candidate.url,
            retrieved_at=candidate.retrieved_at,
            jurisdiction=candidate.jurisdiction,
            excerpt=candidate.excerpt,
            verification_status="unverified",
            review_state="pending_review",
            claim_key=candidate.claim_key,
        )

    @staticmethod
    def _refresh_conflicts(records: list[StoredDiscovery]) -> list[StoredDiscovery]:
        groups: dict[tuple[str, str], list[int]] = {}
        for index, record in enumerate(records):
            key = DiscoveryStore._conflict_key(record)
            if key is not None:
                groups.setdefault(key, []).append(index)
        updated = list(records)
        for indexes in groups.values():
            excerpt_values = {" ".join(records[index].excerpt.casefold().split()) for index in indexes}
            if len(indexes) < 2 or len(excerpt_values) < 2:
                continue
            ids = [records[index].discovery_id for index in indexes]
            for index in indexes:
                updated[index] = records[index].model_copy(
                    update={"conflicts_with": [item for item in ids if item != records[index].discovery_id]}
                )
        return updated

    def store_candidates(
        self, candidates: list[OfficialSourceCandidate | dict[str, object]]
    ) -> list[StoredDiscovery]:
        """Validate and persist candidates, deduplicating without resetting human review."""
        parsed: list[OfficialSourceCandidate] = []
        try:
            parsed = [
                candidate if isinstance(candidate, OfficialSourceCandidate)
                else OfficialSourceCandidate.model_validate(candidate)
                for candidate in candidates
            ]
        except (ValidationError, TypeError) as exc:
            raise DiscoveryStoreError("Discovery candidate has missing or invalid provenance") from exc

        with self._lock:
            records = self._read()
            by_fingerprint = {self._fingerprint(record): index for index, record in enumerate(records)}
            stored: list[StoredDiscovery] = []
            for candidate in parsed:
                fingerprint = self._fingerprint(candidate)
                index = by_fingerprint.get(fingerprint)
                if index is None:
                    record = self._from_candidate(candidate)
                    records.append(record)
                    index = len(records) - 1
                    by_fingerprint[fingerprint] = index
                else:
                    current = records[index]
                    if candidate.retrieved_at > current.retrieved_at:
                        records[index] = current.model_copy(update={
                            "retrieved_at": candidate.retrieved_at,
                            "title": candidate.title,
                        })
                stored.append(records[index])
            records = self._refresh_conflicts(records)
            self._write(records)
            index_by_id = {record.discovery_id: record for record in records}
            return [index_by_id[record.discovery_id] for record in stored]

    def ingest_result(self, result: OfficialSourceResult) -> list[StoredDiscovery]:
        """Persist successful retrieval results only; other statuses have no candidates to store."""
        if result.status != "ok":
            return []
        return self.store_candidates(result.candidates)

    def list(self, review_state: ReviewState | None = None) -> list[StoredDiscovery]:
        with self._lock:
            records = self._read()
            return [record for record in records if review_state is None or record.review_state == review_state]

    def get(self, discovery_id: str) -> StoredDiscovery | None:
        with self._lock:
            return next((record for record in self._read() if record.discovery_id == discovery_id), None)

    def review(
        self,
        discovery_id: str,
        review_state: Literal["pending_review", "verified", "rejected"],
        *,
        notes: str | None = None,
    ) -> StoredDiscovery:
        """Record a human decision in the discovery store only; never updates trusted records."""
        if review_state not in {"pending_review", "verified", "rejected"}:
            raise DiscoveryStoreError("Unsupported discovery review state")
        normalized_notes = notes.strip() if isinstance(notes, str) else None
        if review_state in {"verified", "rejected"} and not normalized_notes:
            raise DiscoveryStoreError("Verified or rejected discoveries require a review note")
        if normalized_notes is not None and len(normalized_notes) > 4000:
            raise DiscoveryStoreError("Review note is too long")
        with self._lock:
            records = self._read()
            for index, current in enumerate(records):
                if current.discovery_id != discovery_id:
                    continue
                verified_status: VerificationStatus = {
                    "pending_review": "unverified",
                    "verified": "verified",
                    "rejected": "rejected",
                }[review_state]
                updated = current.model_copy(update={
                    "review_state": review_state,
                    "verification_status": verified_status,
                    "review_notes": normalized_notes,
                    "reviewed_at": datetime.now(timezone.utc) if review_state != "pending_review" else None,
                })
                records[index] = updated
                self._write(records)
                return updated
        raise DiscoveryStoreError("Discovery record was not found")

    def mark_stale_before(self, cutoff: datetime, *, note: str = "Review freshness threshold reached.") -> list[StoredDiscovery]:
        """Mark records older than a caller-selected cutoff stale; no freshness period is assumed."""
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise DiscoveryStoreError("Stale cutoff must include a timezone")
        normalized_note = note.strip()
        if not normalized_note or len(normalized_note) > 4000:
            raise DiscoveryStoreError("A bounded stale-review note is required")
        cutoff = cutoff.astimezone(timezone.utc)
        with self._lock:
            records = self._read()
            changed: list[StoredDiscovery] = []
            for index, current in enumerate(records):
                if current.review_state in {"rejected", "stale"} or current.retrieved_at >= cutoff:
                    continue
                review_notes = "; ".join(filter(None, [current.review_notes, normalized_note]))
                if len(review_notes) > 4000:
                    raise DiscoveryStoreError("Combined review notes are too long")
                updated = current.model_copy(update={
                    "review_state": "stale",
                    "verification_status": "stale",
                    "review_notes": review_notes,
                })
                records[index] = updated
                changed.append(updated)
            if changed:
                self._write(records)
            return changed
