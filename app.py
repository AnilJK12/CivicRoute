"""CivicRoute API: answers are grounded in the local verified service catalog."""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal

from backend.llm import compose_steps
from backend.http_errors import sanitized_validation_error
from backend.url_validation import is_absolute_https_url
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("civicroute")
CATALOG_PATH = Path(__file__).with_name("services.json")

app = FastAPI(title="CivicRoute API", version="1.0.0")
app.add_exception_handler(RequestValidationError, sanitized_validation_error)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    language: Literal["en", "kn"] = "en"


class ServiceFields(BaseModel):
    documents: list[str]
    fees: list[dict[str, Any]]
    official_links: list[dict[str, str]]
    sources: list[dict[str, str]]
    last_verified: str

    @field_validator("official_links", "sources")
    @classmethod
    def require_absolute_https_urls(cls, records: list[dict[str, str]]) -> list[dict[str, str]]:
        for record in records:
            url = record.get("url", "")
            if not is_absolute_https_url(url):
                raise ValueError("links and sources require absolute HTTPS URLs")
        return records


class AskResponse(BaseModel):
    service_id: str
    supported: bool
    language: Literal["en", "kn"]
    answer: str
    steps: list[str]
    service: ServiceFields | None


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", value).strip()


def load_catalog() -> list[dict[str, Any]]:
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("catalog root must be a list")
        return raw
    except (OSError, json.JSONDecodeError, ValueError):
        logger.error("Could not load service catalog")
        raise HTTPException(status_code=503, detail="Service catalog is unavailable")


def classify(question: str, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match explicit catalog phrases only; no model invents or chooses service IDs."""
    text = _normalize(question)
    matches: list[tuple[int, dict[str, Any]]] = []
    for service in catalog:
        phrases = service.get("match_phrases", {})
        all_phrases = phrases.get("en", []) + phrases.get("kn", [])
        for phrase in all_phrases:
            normalized = _normalize(phrase)
            if normalized and normalized in text:
                matches.append((len(normalized), service))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    # Ambiguous best matches fall back safely instead of selecting arbitrarily.
    best = matches[0][0]
    winners = {item[1]["id"]: item[1] for item in matches if item[0] == best}
    return next(iter(winners.values())) if len(winners) == 1 else None


def _explanation(service: dict[str, Any], language: str) -> list[str]:
    """Use the isolated composer; legacy response shape exposes steps only."""
    return compose_steps(service, language).steps


def _direct_fields(service: dict[str, Any]) -> ServiceFields:
    # Validate and copy authoritative fields directly from the verified record.
    return ServiceFields(
        documents=service["documents"],
        fees=service["fees"],
        official_links=service["official_links"],
        sources=service["sources"],
        last_verified=service["last_verified"],
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
def ask(request: AskRequest) -> AskResponse:
    catalog = load_catalog()
    service = classify(request.question, catalog)
    if service is None:
        fallback = {
            "en": "I couldn’t confidently match that question to a verified Karnataka public service. Try naming the service, or ask about a supported service.",
            "kn": "ಈ ಪ್ರಶ್ನೆಯನ್ನು ಪರಿಶೀಲಿಸಿದ ಕರ್ನಾಟಕದ ಸಾರ್ವಜನಿಕ ಸೇವೆಯೊಂದಿಗೆ ಖಚಿತವಾಗಿ ಹೊಂದಿಸಲಾಗಲಿಲ್ಲ. ಸೇವೆಯ ಹೆಸರನ್ನು ತಿಳಿಸಿ ಅಥವಾ ಲಭ್ಯವಿರುವ ಸೇವೆಯ ಬಗ್ಗೆ ಕೇಳಿ.",
        }[request.language]
        return AskResponse(
            service_id="unknown", supported=False, language=request.language,
            answer=fallback, steps=[], service=None,
        )

    steps = _explanation(service, request.language)
    answer = " ".join(steps)
    return AskResponse(
        service_id=service["id"], supported=True, language=request.language,
        answer=answer, steps=steps, service=_direct_fields(service),
    )
