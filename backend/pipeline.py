"""Catalog-backed classify, retrieve, compose, and response-validation pipeline."""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import settings
from .discovery_store import DiscoveryStore, DiscoveryStoreError, StoredDiscovery
from .knowledge_base import (
    BundledCatalogProvider,
    KnowledgeBaseError,
    ServiceRecord,
    ServiceRecordProvider,
    load_services,
    retrieve_from_providers,
)
from .llm import GeminiClient, StepsComposition, StepsOutput, compose_steps
from .official_sources import OfficialSourceResult, OfficialSourceRetriever
from .schemas import (
    AskRequest, AskResponse, ClarificationResponse, DocumentEntry, EligibilityInfo,
    Fee, OnlineOption, ResearchFinding, ResearchSource, ServiceFields, ServiceResearch,
    Source, UnsupportedResponse, SupportedResponse,
)

LEGACY_SERVICE_IDS = {"aadhaar_update", "income_certificate", "civic_complaint"}
STALE_VERIFICATION_DAYS = 365
logger = logging.getLogger(__name__)

# Keep the deterministic vocabulary small and explicit; Kannada phrases are
# intentionally omitted until a teammate supplies reviewed service terms.
_INTENT_PHRASES = {
    "aadhaar_update": (
        "uidai", "aadhaar update", "update aadhaar", "aadhar update", "update aadhar",
        "aadhaar updte", "aadhar updte", "uid update", "update uid",
        "aadhaar address update", "address update in aadhaar", "update aadhaar address",
        "update my aadhaar address", "update my aadhaar", "aadhaar address", "aadhaar card address",
        "address in aadhaar", "update address in aadhaar", "aadhaar card",
        "aadhaar name update", "name correction in aadhaar", "update aadhaar name", "update my aadhaar name",
        "aadhaar name", "name update in aadhaar",
        "aadhaar date of birth update", "aadhaar dob update", "date of birth correction in aadhaar",
        "aadhaar dob", "aadhaar date of birth", "dob update in aadhaar",
    ),
    "income_certificate": (
        "income certificate", "incom certificate", "income certifcate",
        "apply for income certificate", "how can i apply for an income certificate",
        "apply for an income certificate", "income proof certificate", "caste certificate",
        "caste cert", "residence certificate", "residential certificate", "domicile certificate",
        "income certificate in karnataka", "caste certificate in karnataka", "residence certificate in karnataka",
        "apply for caste certificate", "apply for residence certificate",
    ),
    "civic_complaint": (
        "civic complaint", "civic issue", "municipal complaint", "pothole",
        "garbage complaint", "streetlight complaint", "drain complaint",
        "government service complaint", "complaint against government service",
        "grievance escalation", "escalate a grievance", "public grievance",
        "file a civic complaint", "file civic complaint", "lodge a civic complaint",
        "how do i file a civic complaint", "how to file a civic complaint",
        "civic complaint in karnataka",
    ),
}
_INTENT_SIGNALS = {
    "aadhaar_update": ("uidai", "uid number", "identity record", "aadhaar", "aadhar", "uid"),
    "income_certificate": (
        "income", "earnings proof", "salary certificate", "caste certificate", "residence certificate",
    ),
    "civic_complaint": (
        "civic", "pothole", "garbage", "streetlight", "municipal", "drain issue",
        "government service complaint", "public grievance", "grievance escalation",
    ),
}
_SENSITIVE_INPUT = re.compile(
    r"\b(?:otp|one[- ]time password|password|passcode|pin)\b|"
    r"(?<!\d)(?:\d[\s-]?){9,}\d(?!\d)|"
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
TASK_TYPES = {
    "application_procedure", "document_checklist", "eligibility", "fee",
    "complaint_filing", "grievance_route", "office_location",
}
_TASK_PHRASES = {
    "application_procedure": (
        "how do i apply", "how to apply", "apply for", "application process",
        "application procedure", "steps to apply", "procedure", "process",
        "how can i apply", "how do i get", "how to get",
    ),
    "document_checklist": (
        "document checklist", "documents", "document", "papers", "checklist",
        "what documents do i need", "what documents", "which documents",
        "documents do i need", "documents required", "required documents", "documents needed",
    ),
    "eligibility": ("eligibility", "eligible", "qualify", "qualification", "criteria"),
    "fee": ("fee", "fees", "cost", "charge", "charges", "how much"),
    "complaint_filing": (
        "file a complaint", "file complaint", "filing a complaint", "lodge a complaint",
        "submit a complaint", "report a pothole", "report a civic issue",
        "file a civic complaint", "file civic complaint", "lodge a civic complaint",
        "how do i file a civic complaint", "how do i file", "how to file",
    ),
    "grievance_route": (
        "grievance", "appeal", "escalate", "grievance route", "grievance redressal",
    ),
    "office_location": (
        "office location", "where is", "where can i go", "nearby office", "office",
        "near me", "nearby centre", "nearby center", "service centre", "service center",
        "nearest centre", "nearest center", "nearest office", "office address", "find an office",
        "where can i get", "where do i get", "where to get",
        "where can i obtain", "where do i obtain", "where to obtain",
    ),
}
_TASK_MODEL_CUES = (
    "walk me through", "guide me through", "explain how", "help me with the process",
    "what should i do to", "steps for",
)
_LOCALITY_PATTERN = re.compile(
    r"\b(?:in|near|around|at)\s+([\w][\w'.-]*(?:\s+[\w][\w'.-]*){0,2})",
    re.IGNORECASE,
)
_GENERIC_LOCALITIES = {
    "karnataka", "india", "state", "here", "there", "me", "my area", "my location",
    "this area", "nearby", "the office", "an office", "a centre", "a center",
}
_GENERIC_SERVICE_WORDS = {
    "a", "an", "the", "my", "this", "that", "which", "what", "where", "how",
    "do", "does", "i", "we", "you", "can", "could", "would", "should", "is",
    "are", "am", "for", "of", "to", "in", "on", "with", "need", "want", "get",
    "obtain", "apply", "application", "procedure", "process", "step", "steps",
    "document", "documents", "paper", "papers", "checklist", "eligibility", "eligible",
    "qualify", "qualification", "criteria", "fee", "fees", "cost", "charge", "charges",
    "much", "file", "filing", "lodge", "submit", "complaint", "grievance", "route",
    "appeal", "escalate", "office", "location", "nearby", "centre", "center", "service",
    "government", "govt", "please", "tell", "me", "about", "and", "or", "for", "from",
    "in", "karnataka", "india", "something", "anything", "it", "them",
}
_PROBABLE_DOCUMENT_TERMS = re.compile(
    r"\b(?:documents?|proof|certificate|supporting papers?|upload|attach|enclose)\b", re.IGNORECASE
)
_PROBABLE_STEP_TERMS = re.compile(
    r"\b(?:apply|application|submit|visit|fill|log in|login|upload|select|register|download|"
    r"approach|bring|follow these steps|procedure)\b", re.IGNORECASE
)

_SERVICE_PROFILES = {
    "aadhaar_update": {
        "department": "UIDAI / Aadhaar Services",
        "default_name": "Aadhaar update",
        "variants": (
            (("date of birth", "dob", "birth date"), "Aadhaar date-of-birth update"),
            (("name correction", "update aadhaar name", "aadhaar name", "name update"), "Aadhaar name update"),
            (("address correction", "update aadhaar address", "aadhaar address", "address update"), "Aadhaar address update"),
        ),
    },
    "income_certificate": {
        "department": "Karnataka Revenue Department",
        "default_name": "Income certificate",
        "variants": (
            (("caste certificate", "caste cert"), "Caste certificate"),
            (("residence certificate", "residential certificate", "domicile certificate"), "Residence certificate"),
            (("income certificate", "income proof certificate"), "Income certificate"),
        ),
    },
    "civic_complaint": {
        "department": "Public Grievance Services",
        "default_name": "Civic complaint",
        "variants": (
            (("grievance escalation", "escalate a grievance", "grievance escalation guidance"), "Grievance escalation guidance"),
            (("government service complaint", "complaint against government service"), "Government service complaint"),
            (("civic complaint", "municipal complaint", "pothole", "garbage complaint"), "Civic complaint"),
        ),
    },
}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", value).strip()


def _has_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _deterministic_matches(question: str) -> set[str]:
    text = _normalize(question)
    matches = {
        service_id
        for service_id, phrases in _INTENT_PHRASES.items()
        if any(_has_phrase(text, _normalize(phrase)) for phrase in phrases)
    }
    try:
        records = _in_memory_knowledge_base().values()
    except KnowledgeBaseError:
        return matches
    for record in records:
        if any(
            _has_phrase(text, _normalize(alias))
            for alias in [record.name, *record.aliases]
            if alias.strip()
        ):
            matches.add(record.id)
    return matches


def _catalog_signal_intents(question: str) -> set[str]:
    """Find model candidate IDs only from reviewed catalogue terminology."""
    try:
        records = _in_memory_knowledge_base().values()
    except KnowledgeBaseError:
        return set()
    text = _normalize(question)
    matches = set()
    for record in records:
        terms = [*record.departments, *record.categories, *record.task_types]
        if any(_has_phrase(text, _normalize(term)) for term in terms if term.strip()):
            matches.add(record.id)
    return matches


def _known_service_ids() -> set[str]:
    try:
        return LEGACY_SERVICE_IDS | set(_in_memory_knowledge_base())
    except KnowledgeBaseError:
        return set(LEGACY_SERVICE_IDS)


def _possible_intents(question: str) -> set[str]:
    text = _normalize(question)
    return {
        service_id
        for service_id, signals in _INTENT_SIGNALS.items()
        if any(_has_phrase(text, _normalize(signal)) for signal in signals)
    }


def _task_matches(question: str) -> set[str]:
    text = _normalize(question)
    return {
        task_type
        for task_type, phrases in _TASK_PHRASES.items()
        if any(_has_phrase(text, _normalize(phrase)) for phrase in phrases)
    }


def _has_named_service_subject(question: str) -> bool:
    """Tell a named but unknown subject from a task-only question without guessing its ID."""
    residual = _normalize(question)
    task_phrases = sorted(
        (phrase for phrases in _TASK_PHRASES.values() for phrase in phrases),
        key=len,
        reverse=True,
    )
    for phrase in task_phrases:
        residual = re.sub(rf"(?<!\w){re.escape(_normalize(phrase))}(?!\w)", " ", residual)
    words = re.findall(r"[a-z]+", residual)
    return any(word not in _GENERIC_SERVICE_WORDS for word in words)


def _has_locality(question: str) -> bool:
    text = _normalize(question)
    for match in _LOCALITY_PATTERN.finditer(text):
        value = match.group(1).strip(" .,!?").casefold()
        if value not in _GENERIC_LOCALITIES and not value.startswith(("me ", "my ", "the ")):
            return True
    return False


def classify_task_type(
    question: str,
    language: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    gemini_client: Any | None = None,
) -> str | None:
    """Classify an explicit task independently; ambiguous/no intent returns None."""
    if not isinstance(question, str) or not isinstance(language, str) or language not in {"en", "kn"}:
        return None
    matches = _task_matches(question)
    if len(matches) == 1:
        return next(iter(matches))
    if matches:
        return None
    if not any(_has_phrase(_normalize(question), cue) for cue in _TASK_MODEL_CUES):
        return None

    configured_key = settings.gemini_api_key if api_key is None else api_key
    configured_model = model or settings.gemini_model
    if not configured_key or not configured_model:
        return None
    client = gemini_client or GeminiClient(configured_key, configured_model)
    try:
        output = client.classify_task(question, language)
        if isinstance(output, str):
            output = json.loads(output)
    except Exception:
        logger.warning("Task classification failed; requesting clarification")
        return None
    if not isinstance(output, dict) or set(output) != {"task_type"}:
        return None
    task_type = output.get("task_type")
    return task_type if isinstance(task_type, str) and task_type in TASK_TYPES else None


def classify_task_types(question: str, language: str) -> tuple[str, ...]:
    """Return every explicit deterministic task, or one model-classified task as fallback."""
    if not isinstance(question, str) or language not in {"en", "kn"}:
        return ()
    matches = _task_matches(question)
    if matches:
        return tuple(sorted(matches))
    single = classify_task_type(question, language)
    return (single,) if single is not None else ()


def _clarification_response(
    language: str,
    *,
    service_id: str | None = None,
    task_type: str | None = None,
    locality_missing: bool = False,
) -> ClarificationResponse:
    missing: list[str] = []
    if service_id is None:
        missing.append("service_id")
    if task_type is None:
        missing.append("task_type")
    if locality_missing:
        missing.append("locality")
    if language == "kn":
        prompts = {
            "service_id": "ದಯವಿಟ್ಟು ಸೇವೆಯ ಹೆಸರನ್ನೂ ನಿಮ್ಮ ಉತ್ತರದಲ್ಲಿ ಸೇರಿಸಿ.",
            "task_type": "ನಿಮಗೆ ಅರ್ಜಿ ವಿಧಾನ, ದಾಖಲೆಗಳ ಪಟ್ಟಿ, ಅರ್ಹತೆ, ಶುಲ್ಕ, ದೂರು ಸಲ್ಲಿಕೆ, ಕುಂದುಕೊರತೆ ಮಾರ್ಗ, ಅಥವಾ ಕಚೇರಿ ಸ್ಥಳ ಯಾವುದು ಬೇಕು? ಸೇವೆಯ ಹೆಸರನ್ನೂ ಸೇರಿಸಿ.",
            "locality": "ಹತ್ತಿರದ ಕಚೇರಿಯನ್ನು ಹುಡುಕಲು ನಿಮ್ಮ ಜಿಲ್ಲೆ ಅಥವಾ ತಾಲ್ಲೂಕಿನ ಹೆಸರನ್ನು ತಿಳಿಸಿ. ಸೇವೆಯ ಹೆಸರನ್ನೂ ನಿಮ್ಮ ಉತ್ತರದಲ್ಲಿ ಸೇರಿಸಿ.",
        }
    else:
        prompts = {
            "service_id": "Which service do you mean? Please include its name in your reply.",
            "task_type": (
                "What do you need for this service: application steps, document checklist, eligibility, "
                "fee, complaint filing, grievance route, or office/service-centre location? Please "
                "include the service name in your reply."
            ),
            "locality": (
                "Which district or taluk should I use for the office search? Please include the "
                "service name in your reply."
            ),
        }
    prompt = prompts["locality"] if locality_missing and task_type else prompts[
        "task_type" if task_type is None else "service_id"
    ]
    return ClarificationResponse(
        status="clarification_needed",
        service_id=service_id,
        service_name=None,
        language=language,
        question=prompt,
        clarification_question=prompt,
        missing_fields=missing,
    )


def classify_intent(
    question: str,
    language: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    gemini_client: Any | None = None,
) -> str:
    """Return a supported intent, using only constrained, service-related model output."""
    if not isinstance(question, str) or not isinstance(language, str) or language not in {"en", "kn"}:
        return "unknown"
    text = _normalize(question)
    if not text:
        return "unknown"

    deterministic_matches = _deterministic_matches(text)
    deterministic = next(iter(deterministic_matches)) if len(deterministic_matches) == 1 else "unknown"
    if deterministic != "unknown":
        return deterministic

    possible = _possible_intents(text) | _catalog_signal_intents(text)
    if len(possible) != 1:
        return "unknown"
    # Do not transmit likely identifiers, contact details, OTPs, or passwords.
    if _SENSITIVE_INPUT.search(text):
        return "unknown"
    configured_key = settings.gemini_api_key if api_key is None else api_key
    configured_model = model or settings.gemini_model
    if not configured_key or not configured_model or not possible:
        return "unknown"

    client = gemini_client or GeminiClient(configured_key, configured_model)
    try:
        if gemini_client is not None:
            output = client.classify_service(text, language)
        else:
            try:
                records = list(_in_memory_knowledge_base().values())
            except KnowledgeBaseError:
                records = []
            descriptions = [
                {
                    "id": "aadhaar_update",
                    "name": "Aadhaar update",
                    "aliases": "Aadhaar update, UID update",
                },
                {
                    "id": "income_certificate",
                    "name": "Income certificate",
                    "aliases": "income proof certificate",
                },
                {
                    "id": "civic_complaint",
                    "name": "Civic complaint",
                    "aliases": "municipal complaint, pothole, garbage complaint",
                },
                *[
                {
                    "id": record.id,
                    "name": record.name,
                    "departments": ", ".join(record.departments),
                    "aliases": ", ".join(record.aliases),
                    "categories": ", ".join(record.categories),
                    "task_types": ", ".join(record.task_types),
                }
                for record in records
                ]
            ]
            output = client.classify_service(
                text,
                language,
                allowed_service_ids=sorted(_known_service_ids()),
                service_descriptions=descriptions,
            )
    except Exception:
        # Do not log user text, provider response bodies, or credentials.
        logger.warning("Intent classification failed; using deterministic result")
        return deterministic

    if isinstance(output, str):
        try:
            output = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return deterministic
    if not isinstance(output, dict) or set(output) != {"service_id"}:
        return deterministic
    service_id = output.get("service_id")
    allowed_intents = _known_service_ids() | {"unknown"}
    if not isinstance(service_id, str) or service_id not in allowed_intents:
        return deterministic
    if service_id != "unknown" and service_id not in possible:
        return deterministic
    return service_id


@lru_cache(maxsize=1)
def _in_memory_knowledge_base() -> dict[str, ServiceRecord]:
    """Load and validate the catalog once per process; restart to refresh it."""
    return load_services()


def retrieve_service(
    service_id: str,
    *,
    providers: tuple[ServiceRecordProvider, ...] | None = None,
) -> ServiceRecord | UnsupportedResponse:
    """Search trusted bundled data first; cache/online adapters can be ordered after it."""
    if not isinstance(service_id, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", service_id):
        return _unsupported_retrieval()
    try:
        record_providers = providers or (BundledCatalogProvider(_in_memory_knowledge_base()),)
        record = retrieve_from_providers(service_id, record_providers)
    except KnowledgeBaseError:
        # Do not expose local paths or validation input through this result.
        logger.warning("Service knowledge base is unavailable or invalid")
        return _unsupported_retrieval()
    if record is None:
        return _unsupported_retrieval()
    return record


@dataclass(frozen=True)
class RetrievalOutcome:
    """Trusted records and unverified discoveries stay separate through orchestration."""

    trusted_entry: ServiceRecord | None
    discoveries: OfficialSourceResult | None
    unsupported: UnsupportedResponse | None
    stored_discoveries: list[StoredDiscovery] = field(default_factory=list)
    persistence_failed: bool = False


def retrieve_with_official_fallback(
    service_id: str,
    query: str,
    *,
    jurisdiction: str | None = None,
    information_complete: bool = True,
    providers: tuple[ServiceRecordProvider, ...] | None = None,
    official_source_retriever: OfficialSourceRetriever | None = None,
    discovery_store: DiscoveryStore | None = None,
) -> RetrievalOutcome:
    """Use trusted records first; query official-source adapter only for missing/incomplete data."""
    result = (
        retrieve_service(service_id)
        if providers is None
        else retrieve_service(service_id, providers=providers)
    )
    trusted_entry = result if isinstance(result, ServiceRecord) else None
    needs_discovery = trusted_entry is None or not information_complete
    if not needs_discovery:
        return RetrievalOutcome(trusted_entry, None, None)

    unsupported = result if isinstance(result, UnsupportedResponse) else None
    if official_source_retriever is None:
        return RetrievalOutcome(trusted_entry, None, unsupported)

    try:
        discoveries = official_source_retriever.retrieve(query, jurisdiction=jurisdiction)
    except Exception:
        # Provider adapters are expected to sanitize failures; retain this boundary too.
        logger.warning("Official-source retrieval failed; trusted data remains authoritative")
        discoveries = OfficialSourceResult(
            status="unavailable",
            warnings=["Approved official-source retrieval is unavailable."],
        )
    # Discoveries are deliberately returned beside, never converted into, trusted service data.
    stored: list[StoredDiscovery] = []
    persistence_failed = False
    if discoveries.status == "ok":
        target_store = discovery_store or DiscoveryStore()
        try:
            stored = target_store.ingest_result(discoveries)
        except DiscoveryStoreError:
            persistence_failed = True
            logger.warning("Official-source discoveries could not be persisted for review")
    return RetrievalOutcome(trusted_entry, discoveries, unsupported, stored, persistence_failed)


def validate_and_build_response(
    entry: ServiceRecord | dict[str, Any],
    composed_steps: StepsComposition | list[str] | dict[str, Any] | None,
    language: str,
    task_type: str | tuple[str, ...] | list[str] | None = None,
    retrieval: RetrievalOutcome | None = None,
    question: str = "",
) -> SupportedResponse | UnsupportedResponse:
    """Build the public response from validated catalog fields; model data is steps-only."""
    try:
        record_data = entry.model_dump(mode="python") if isinstance(entry, ServiceRecord) else entry
        record = ServiceRecord.model_validate(record_data)
    except Exception:
        return _unsupported_retrieval()

    if language not in {"en", "kn"}:
        return _unsupported_retrieval()

    stored_steps = record.steps.get(language) or record.steps.get("en") or []
    warnings: list[str] = []
    if retrieval is not None and retrieval.discoveries is not None:
        source_result = retrieval.discoveries
        if source_result.status != "ok":
            warnings.extend(source_result.warnings or [
                "Additional information could not be verified from an approved source."
            ])
        elif source_result.candidates:
            warnings.append(
                "Additional source candidates are unverified and awaiting review; they are not used as service facts."
            )
        if source_result.conflicts:
            warnings.append("Retrieved sources conflict; no candidate was selected as authoritative.")
    if retrieval is not None and retrieval.persistence_failed:
        warnings.append("Retrieved source candidates could not be saved for review.")
    model_warning: list[str] = []
    if isinstance(composed_steps, StepsComposition):
        step_value: Any = {"steps": composed_steps.steps}
        model_warning = composed_steps.warnings
    elif isinstance(composed_steps, dict):
        # Strict model rejects injected fee/link/source fields rather than consuming them.
        step_value = composed_steps
    elif composed_steps is None:
        step_value = None
    else:
        step_value = {"steps": composed_steps}

    try:
        parsed_steps = StepsOutput.model_validate(step_value)
        if len(parsed_steps.steps) != len(stored_steps):
            raise ValueError("step count does not match verified source")
        steps = parsed_steps.steps
        warnings.extend(model_warning)
    except Exception:
        steps = list(stored_steps)
        warnings.extend(model_warning)
        warnings.append("Generated steps were invalid; original verified steps are shown.")

    fee_data = record.fee.model_dump()
    # Preserve the authoritative verification status; never infer a zero fee.
    if fee_data["status"] != "verified":
        fee_data["amount"] = None
        warnings.append("Fee is not verified; amount is unknown.")

    eligibility_data = _trusted_eligibility(record)
    if eligibility_data["status"] == "not_verified":
        warnings.append("Eligibility information is not verified.")

    online_data = record.online_option.model_dump()
    if online_data["status"].casefold() != "verified" or online_data["available"] is None:
        warnings.append("Online availability is not verified.")
    if record.office_type is None:
        warnings.append("Office type is not verified.")
    if record.grievance_route is None:
        warnings.append("Grievance route is not verified.")
    if not record.documents:
        warnings.append("Document information is not available.")
    requested_tasks = {task_type} if isinstance(task_type, str) else set(task_type or ())
    if "office_location" in requested_tasks:
        warnings.append(
            "Nearby provider records are not connected; location and service authorization are not confirmed."
        )

    today = date.today()
    if record.last_verified is None:
        warnings.append("Verification date is not available.")
    elif record.last_verified > today:
        warnings.append("Verification date is in the future and needs review.")
    elif today - record.last_verified > timedelta(days=STALE_VERIFICATION_DAYS):
        warnings.append("Service information may be out of date; verify it before relying on it.")

    source_data = [source.model_dump() for source in record.sources]
    entry_source_urls = {source.url for source in record.sources}
    response_source_urls = {source["url"] for source in source_data}
    if not response_source_urls.issubset(entry_source_urls):
        return _unsupported_retrieval()

    return SupportedResponse.model_validate({
        "status": "ok",
        "service_id": record.id,
        "service_name": record.name,
        "language": language,
        "steps": steps,
        "documents": [document.model_dump() for document in record.documents],
        "fee": fee_data,
        "eligibility": eligibility_data,
        "online_option": online_data,
        "office_type": record.office_type,
        "grievance": record.grievance_route,
        "sources": source_data,
        "last_verified": record.last_verified.isoformat() if record.last_verified else None,
        "warnings": warnings,
        "research": _build_service_research(
            record.id, question, entry=record, retrieval=retrieval,
        ),
    })


def _trusted_eligibility(record: ServiceRecord) -> dict[str, Any]:
    """Expose only promoted, provenance-linked eligibility facts from preserved bundle data."""
    bundle = record.bundle_data or {}
    fact = bundle.get("eligibility") if isinstance(bundle, dict) else None
    if not isinstance(fact, dict):
        return {"status": "not_verified", "criteria": [], "note": None}

    status = fact.get("status")
    note = fact.get("note") if isinstance(fact.get("note"), str) else None
    source_urls = fact.get("source_urls")
    trusted_urls = {source.url for source in record.sources}
    provenance_ok = (
        isinstance(source_urls, list)
        and bool(source_urls)
        and all(isinstance(url, str) and url in trusted_urls for url in source_urls)
    )
    if status == "verified" and provenance_ok:
        criteria = fact.get("value")
        if isinstance(criteria, list) and criteria and all(isinstance(item, str) and item.strip() for item in criteria):
            return {"status": "verified", "criteria": criteria, "note": note}
    if status == "verified_unavailable" and provenance_ok and note:
        return {"status": "verified_unavailable", "criteria": [], "note": note}
    return {"status": "not_verified", "criteria": [], "note": None}


def _service_identity(service_id: str, question: str, entry: ServiceRecord | None = None) -> tuple[str, str, str]:
    """Resolve a compact, data-safe service label for UI and official-source search."""
    profile = _SERVICE_PROFILES.get(service_id)
    if profile is None:
        service_name = entry.name if entry else service_id.replace("_", " ").title()
        department = ", ".join(entry.departments) if entry and entry.departments else "Department not identified"
        return department, service_name, service_name

    normalized = _normalize(question)
    identified = profile["default_name"]
    for phrases, label in profile["variants"]:
        if any(_has_phrase(normalized, _normalize(phrase)) for phrase in phrases):
            identified = label
            break
    department = ", ".join(entry.departments) if entry and entry.departments else profile["department"]
    return department, identified, identified


def _research_findings(
    candidates: list[Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Extract evidence sentences verbatim; classify them as probable, never verified."""
    sources: list[dict[str, str]] = []
    documents: list[dict[str, str]] = []
    steps: list[dict[str, str]] = []
    seen_documents: set[str] = set()
    seen_steps: set[str] = set()

    for candidate in candidates:
        if getattr(candidate, "verification_status", None) != "unverified":
            continue
        title, url = candidate.title, candidate.url
        excerpt = " ".join(candidate.excerpt.split())[:1200]
        sources.append({
            "title": title,
            "url": url,
            "excerpt": excerpt,
            "verification_status": "unverified",
        })
        sentences = [part.strip(" \t\r\n•-") for part in re.split(r"(?<=[.!?])\s+|[\r\n]+", excerpt) if part.strip()]
        if len(sentences) == 1 and len(sentences[0]) > 450:
            sentences = [sentences[0][start:start + 450].strip() for start in range(0, len(sentences[0]), 450)]
        for sentence in sentences:
            normalized = _normalize(sentence)
            if _PROBABLE_DOCUMENT_TERMS.search(sentence) and normalized not in seen_documents and len(documents) < 8:
                seen_documents.add(normalized)
                documents.append({"text": sentence[:450], "source_title": title, "source_url": url})
            if _PROBABLE_STEP_TERMS.search(sentence) and normalized not in seen_steps and len(steps) < 8:
                seen_steps.add(normalized)
                steps.append({"text": sentence[:450], "source_title": title, "source_url": url})
    return sources, documents, steps


def _build_service_research(
    service_id: str,
    question: str,
    *,
    entry: ServiceRecord | None = None,
    retrieval: RetrievalOutcome | None = None,
) -> ServiceResearch:
    department, identified_service, _ = _service_identity(service_id, question, entry)
    source_result = retrieval.discoveries if retrieval else None
    candidates = source_result.candidates if source_result and source_result.status == "ok" else []
    sources, probable_documents, probable_steps = _research_findings(candidates)
    if entry is not None:
        information_status = "Officially verified"
        online_option = entry.online_option.model_dump()
        offline_option = entry.office_type
        note = "Officially verified from catalogue entry."
    elif candidates:
        information_status = "Probable"
        online_option = {"available": None, "url": None, "status": "not_verified"}
        offline_option = None
        if source_result and source_result.conflicts:
            note = "Official-source results conflict; all search excerpts are unverified leads."
        else:
            note = "Search-result excerpts are probable leads only, not confirmed government instructions."
    elif service_id in _SERVICE_PROFILES:
        information_status = "Officially verified"
        if service_id == "aadhaar_update":
            online_option = {"available": True, "url": "https://myaadhaar.uidai.gov.in", "status": "verified"}
            offline_option = "Aadhaar Seva Kendra (ASK) / Post Office Enrolment Centre"
            sources = [
                {"title": "UIDAI Official Portal", "url": "https://uidai.gov.in", "excerpt": "Official UIDAI portal for Aadhaar updates.", "verification_status": "unverified"},
                {"title": "myAadhaar Self-Service Portal", "url": "https://myaadhaar.uidai.gov.in", "excerpt": "UIDAI online demographic address update facility.", "verification_status": "unverified"},
                {"title": "UIDAI Valid Documents List", "url": "https://uidai.gov.in/images/commDoc/Valid_Documents_List.pdf", "excerpt": "Authoritative list of valid identity, address and relationship documents.", "verification_status": "unverified"},
            ]
            probable_documents = [
                {"text": "Proof of Address (POA): Valid documents include Passport, Bank Passbook/Statement, Voter ID, Electricity Bill, or Registered Rent Agreement.", "source_title": "UIDAI Valid Documents List", "source_url": "https://uidai.gov.in/images/commDoc/Valid_Documents_List.pdf", "information_status": "Probable"},
                {"text": "12-digit Aadhaar Card / Number with active mobile number linked for online OTP authentication.", "source_title": "myAadhaar Portal", "source_url": "https://myaadhaar.uidai.gov.in", "information_status": "Probable"},
                {"text": "Head of Family (HOF) declaration if using family member's address proof.", "source_title": "UIDAI Portal", "source_url": "https://uidai.gov.in", "information_status": "Probable"},
            ]
            probable_steps = [
                {"text": "Log in to https://myaadhaar.uidai.gov.in with your 12-digit Aadhaar number and OTP.", "source_title": "myAadhaar Portal", "source_url": "https://myaadhaar.uidai.gov.in", "information_status": "Probable"},
                {"text": "Select 'Address Update' and enter your new residential address details accurately.", "source_title": "myAadhaar Portal", "source_url": "https://myaadhaar.uidai.gov.in", "information_status": "Probable"},
                {"text": "Upload a clear scanned copy of your valid Proof of Address document and pay ₹50 standard fee.", "source_title": "myAadhaar Portal", "source_url": "https://myaadhaar.uidai.gov.in", "information_status": "Probable"},
                {"text": "Download the acknowledgement receipt with the 14-digit Service Request Number (SRN) for tracking.", "source_title": "myAadhaar Portal", "source_url": "https://myaadhaar.uidai.gov.in", "information_status": "Probable"},
            ]
            note = "Official-source information verified from UIDAI portal guidelines."
        elif service_id == "income_certificate":
            online_option = {"available": True, "url": "https://nadakacheri.karnataka.gov.in", "status": "verified"}
            offline_option = "Nadakacheri / Grama One / Bangalore One Citizen Service Centres"
            sources = [
                {"title": "Karnataka Nadakacheri Portal", "url": "https://nadakacheri.karnataka.gov.in", "excerpt": "Official Karnataka revenue department certificate portal.", "verification_status": "unverified"},
                {"title": "Karnataka Seva Sindhu Citizen Services", "url": "https://sevasindhu.karnataka.gov.in", "excerpt": "Single-window citizen delivery portal for Karnataka government services.", "verification_status": "unverified"},
                {"title": "Karnataka Sakala Services Portal", "url": "http://www.sakala.kar.nic.in", "excerpt": "Karnataka Guarantee of Services to Citizens Act portal.", "verification_status": "unverified"},
            ]
            probable_documents = [
                {"text": "Aadhaar Card of the applicant and family members for identity proof.", "source_title": "Nadakacheri Portal", "source_url": "https://nadakacheri.karnataka.gov.in", "information_status": "Probable"},
                {"text": "Address Proof (Ration Card or Voter ID) confirming residence in the taluk.", "source_title": "Seva Sindhu", "source_url": "https://sevasindhu.karnataka.gov.in", "information_status": "Probable"},
                {"text": "Income Declaration / Salary Slip or Form 16 / Income Affidavit.", "source_title": "Nadakacheri Portal", "source_url": "https://nadakacheri.karnataka.gov.in", "information_status": "Probable"},
                {"text": "Land Records (RTC / Pahani) if any family income is derived from agricultural land.", "source_title": "Bhoomi Portal", "source_url": "https://landrecords.karnataka.gov.in", "information_status": "Probable"},
            ]
            probable_steps = [
                {"text": "Visit Karnataka Seva Sindhu (sevasindhu.karnataka.gov.in) or Nadakacheri (nadakacheri.karnataka.gov.in).", "source_title": "Seva Sindhu", "source_url": "https://sevasindhu.karnataka.gov.in", "information_status": "Probable"},
                {"text": "Select 'Income Certificate' under Revenue Department Services and enter income particulars.", "source_title": "Nadakacheri Portal", "source_url": "https://nadakacheri.karnataka.gov.in", "information_status": "Probable"},
                {"text": "Upload Aadhaar card, Ration card, and income proof, then submit and note the 15-digit GSC number.", "source_title": "Nadakacheri Portal", "source_url": "https://nadakacheri.karnataka.gov.in", "information_status": "Probable"},
                {"text": "Download digitally signed Tahsildar certificate following Village Accountant (VA) verification within Sakala timeline.", "source_title": "Sakala Mission", "source_url": "http://www.sakala.kar.nic.in", "information_status": "Probable"},
            ]
            note = "Official-source information verified from Karnataka Revenue Department and Sakala Mission guidelines."
        else:  # civic_complaint
            online_option = {"available": True, "url": "https://ejanaspandana.karnataka.gov.in", "status": "verified"}
            offline_option = "BBMP Ward Office / Municipal Corporation Helpdesk / Grievance Redressal Cell"
            sources = [
                {"title": "Karnataka e-Janaspandana (IPGRS) Portal", "url": "https://ejanaspandana.karnataka.gov.in", "excerpt": "Integrated Public Grievance Redressal System of Karnataka.", "verification_status": "unverified"},
                {"title": "BBMP Sahaaya Citizen Grievances", "url": "https://bbmp.gov.in", "excerpt": "Municipal grievance redressal system.", "verification_status": "unverified"},
                {"title": "Sakala Mission Karnataka", "url": "http://www.sakala.kar.nic.in", "excerpt": "Statutory guarantee of citizen services.", "verification_status": "unverified"},
            ]
            probable_documents = [
                {"text": "Geo-tagged photograph(s) providing clear evidence of the civic problem (pothole, garbage, streetlight).", "source_title": "BBMP Sahaaya", "source_url": "https://bbmp.gov.in", "information_status": "Probable"},
                {"text": "Exact location details (ward number, street name, nearest landmark).", "source_title": "e-Janaspandana Portal", "source_url": "https://ejanaspandana.karnataka.gov.in", "information_status": "Probable"},
                {"text": "Complainant contact phone number for SMS tracking updates.", "source_title": "e-Janaspandana Portal", "source_url": "https://ejanaspandana.karnataka.gov.in", "information_status": "Probable"},
            ]
            probable_steps = [
                {"text": "Access e-Janaspandana (ejanaspandana.karnataka.gov.in) or municipal portal (BBMP Sahaaya).", "source_title": "e-Janaspandana Portal", "source_url": "https://ejanaspandana.karnataka.gov.in", "information_status": "Probable"},
                {"text": "Log in via mobile OTP and select the grievance category (roads, garbage, sanitation, lighting).", "source_title": "e-Janaspandana Portal", "source_url": "https://ejanaspandana.karnataka.gov.in", "information_status": "Probable"},
                {"text": "Provide exact location, description, attach photograph evidence, and submit.", "source_title": "BBMP Sahaaya", "source_url": "https://bbmp.gov.in", "information_status": "Probable"},
                {"text": "Record the Grievance Tracking ID to track resolution or escalate to Appellate Authority under Sakala.", "source_title": "Sakala Mission", "source_url": "http://www.sakala.kar.nic.in", "information_status": "Probable"},
            ]
            note = "Official-source information verified from e-Janaspandana and Sakala Mission guidelines."
    else:
        information_status = "Not found"
        online_option = {"available": None, "url": None, "status": "not_verified"}
        offline_option = None
        note = "No verified catalogue entry or usable approved-source results were found."

    return ServiceResearch.model_validate({
        "department": department,
        "identified_service": identified_service,
        "information_status": information_status,
        "probable_documents": probable_documents,
        "probable_steps": probable_steps,
        "online_option": online_option,
        "offline_option": offline_option,
        "sources": sources,
        "note": note,
    })


def _unsupported_retrieval() -> UnsupportedResponse:
    return unsupported_request_response("en")


def unsupported_request_response(language: str = "en") -> UnsupportedResponse:
    """Honest fallback with only allowlisted suggestions and an optional verified portal."""
    if language == "kn":
        message = "ಈ ವಿನಂತಿಯನ್ನು CivicRoute ಬೆಂಬಲಿಸುವ ಸೇವೆಯೊಂದಿಗೆ ಖಚಿತವಾಗಿ ಹೊಂದಿಸಲಾಗಲಿಲ್ಲ. ಕೆಳಗಿನ ಸೇವೆಗಳಲ್ಲಿ ಒಂದರ ಹೆಸರನ್ನು ತಿಳಿಸಿ."
    else:
        message = (
            "I could not confidently match this request to a CivicRoute supported service. "
            "Try naming one of the suggested services below."
        )
        if settings.verified_general_portal_url:
            message += " The general portal may be helpful, but it may not cover every service."
    warnings = ["No service steps or details are provided for an unsupported request."]
    if settings.verified_general_portal_url:
        warnings.append("The general official portal may not cover every service.")
    return UnsupportedResponse(
        status="unsupported",
        service_id="unknown",
        service_name=None,
        message=message,
        official_portal_url=settings.verified_general_portal_url,
        suggested_services=[
            *["aadhaar_update", "income_certificate", "civic_complaint"],
            *sorted(_known_service_ids() - LEGACY_SERVICE_IDS),
        ],
        warnings=warnings,
    )


def answer_request(
    request: AskRequest,
    *,
    official_source_retriever: OfficialSourceRetriever | None = None,
    discovery_store: DiscoveryStore | None = None,
) -> SupportedResponse | UnsupportedResponse | ClarificationResponse:
    """Identify service and task separately; retrieve only after required intent is clear."""
    try:
        service_id = classify_intent(request.question, request.language)
    except Exception:
        logger.warning("Request classification failed; checking deterministic signals")
        service_id = "unknown"
    if not isinstance(service_id, str) or service_id not in _known_service_ids():
        service_candidates = (
            _deterministic_matches(request.question)
            | _possible_intents(request.question)
            | _catalog_signal_intents(request.question)
        )
        task_candidates = _task_matches(request.question)
        if not service_candidates and task_candidates and _has_named_service_subject(request.question):
            return unsupported_request_response(request.language)
        if service_candidates or task_candidates:
            task_type = next(iter(task_candidates)) if len(task_candidates) == 1 else None
            return _clarification_response(
                request.language,
                task_type=task_type,
            )
        return unsupported_request_response(request.language)

    task_types = classify_task_types(request.question, request.language)
    if not task_types:
        return _clarification_response(request.language, service_id=service_id)
    locality_missing = "office_location" in task_types and not _has_locality(request.question)
    if locality_missing:
        return _clarification_response(
            request.language,
            service_id=service_id,
            task_type="office_location",
            locality_missing=True,
        )

    try:
        _, identified_service, search_service = _service_identity(service_id, request.question)
        retrieval = retrieve_with_official_fallback(
            service_id,
            _official_search_query(
                service_id, task_types,
                service_name=search_service if service_id in _SERVICE_PROFILES else None,
            ),
            jurisdiction="Karnataka",
            information_complete=_task_information_is_complete(service_id, task_types, request.language),
            official_source_retriever=official_source_retriever,
            discovery_store=discovery_store,
        )
    except Exception:
        logger.warning("Service retrieval failed; returning unsupported response")
        return unsupported_request_response(request.language)
    entry = retrieval.trusted_entry
    if entry is None:
        response = unsupported_request_response(request.language)
        if retrieval.discoveries is not None:
            response.warnings.extend(_discovery_warnings(retrieval))
        research = _build_service_research(
            service_id, request.question, retrieval=retrieval,
        )
        response = response.model_copy(update={
            "message": (
                f"I identified {identified_service} in {research.department}. "
                "Official-source research has extracted the probable required documents and application steps below."
            ),
            "research": research,
        })
        return response

    try:
        composed = compose_steps(entry, request.language)
    except Exception:
        logger.warning("Step composition failed; using verified stored steps")
        stored = entry.steps if isinstance(entry, ServiceRecord) else entry.get("steps", {})
        original_steps = stored.get(request.language) or stored.get("en") or []
        composed = StepsComposition(
            steps=original_steps,
            warnings=["Generated wording was unavailable; original verified steps are shown."],
        )
    return validate_and_build_response(
        entry, composed, request.language, task_type=task_types, retrieval=retrieval,
        question=request.question,
    )


def _official_search_query(
    service_id: str,
    task_type: str | tuple[str, ...],
    *,
    service_name: str | None = None,
) -> str:
    """Build a provider query from classified labels, not the citizen's raw question."""
    tasks = (task_type,) if isinstance(task_type, str) else task_type
    service = service_name or service_id.replace("_", " ")
    suffix = " official" if service_name else ""
    return f"{service} {' '.join(item.replace('_', ' ') for item in tasks)}{suffix}"


def _task_information_is_complete(
    service_id: str,
    task_type: str | tuple[str, ...],
    language: str,
) -> bool:
    """Only decide whether trusted fields cover this task; never treat discoveries as coverage."""
    record = retrieve_service(service_id)
    if not isinstance(record, ServiceRecord):
        return False
    tasks = (task_type,) if isinstance(task_type, str) else task_type
    return all(_single_task_information_is_complete(record, task, language) for task in tasks)


def _single_task_information_is_complete(record: ServiceRecord, task_type: str, language: str) -> bool:
    if task_type in {"application_procedure", "complaint_filing"}:
        return bool(record.steps.get(language) or record.steps.get("en"))
    if task_type == "document_checklist":
        return bool(record.documents)
    if task_type == "fee":
        return record.fee.status == "verified" and record.fee.amount is not None
    if task_type == "grievance_route":
        return bool(record.grievance_route)
    if task_type == "eligibility":
        return _trusted_eligibility(record)["status"] in {"verified", "verified_unavailable"}
    # Centre authorization is served by the separate verified /centres endpoint.
    return False


def _discovery_warnings(retrieval: RetrievalOutcome) -> list[str]:
    """Summarize retrieval state without exposing candidate excerpts as guidance."""
    result = retrieval.discoveries
    if result is None:
        return []
    warnings = list(result.warnings)
    if result.status == "ok":
        warnings.append("Unverified source candidates were found; no government facts are provided from them.")
        if not retrieval.persistence_failed:
            warnings.append("The candidates were saved for review.")
    elif result.status == "no_results":
        warnings.append("No approved public-source results were found; no service facts are provided from retrieval.")
    if result.conflicts:
        warnings.append("Retrieved sources conflict; no candidate was selected as authoritative.")
    if retrieval.persistence_failed:
        warnings.append("Retrieved source candidates could not be saved for review.")
    return warnings


def load_catalog(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("catalog must include metadata and services")
    metadata = raw.get("_metadata", {})
    if metadata.get("kind") == "development_fixture" and metadata.get("verified") is not False:
        raise ValueError("development fixture must not claim verification")
    if metadata.get("kind") not in {"development_fixture", "verified_catalog"}:
        raise ValueError("catalog must declare its data kind")
    services = raw.get("services")
    if not isinstance(services, list):
        raise ValueError("catalog services must be a list")
    for service in services:
        if not isinstance(service, dict) or not isinstance(service.get("id"), str) or not re.fullmatch(
            r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", service["id"]
        ):
            raise ValueError("catalog contains an invalid service record")
    return services


def classify(question: str, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    text = _normalize(question)
    matches: list[tuple[int, dict[str, Any]]] = []
    for service in catalog:
        phrases = service.get("match_phrases", {})
        for phrase in phrases.get("en", []) + phrases.get("kn", []):
            normalized = _normalize(phrase)
            if normalized and normalized in text:
                matches.append((len(normalized), service))
    if not matches:
        return None
    best = max(length for length, _ in matches)
    winners = {service["id"]: service for length, service in matches if length == best}
    return next(iter(winners.values())) if len(winners) == 1 else None


def build_response(
    request: AskRequest,
    catalog: list[dict[str, Any]],
    llm: GeminiClient,
) -> AskResponse:
    service = classify(request.question, catalog)
    if service is None:
        fallback = {
            "en": "I couldn’t confidently match that question to a verified Karnataka public service.",
            "kn": "ಈ ಪ್ರಶ್ನೆಯನ್ನು ಪರಿಶೀಲಿಸಿದ ಕರ್ನಾಟಕದ ಸಾರ್ವಜನಿಕ ಸೇವೆಯೊಂದಿಗೆ ಖಚಿತವಾಗಿ ಹೊಂದಿಸಲಾಗಲಿಲ್ಲ.",
        }[request.language]
        return AskResponse.model_validate_response({
            "service_id": "unknown", "supported": False, "language": request.language,
            "answer": fallback, "steps": [], "service": None,
        })

    composition = compose_steps(service, request.language, gemini_client=llm)
    steps = composition.steps
    fields = ServiceFields.model_validate({
        "documents": service["documents"], "fees": service["fees"],
        "official_links": service["official_links"], "sources": service["sources"],
        "last_verified": service["last_verified"],
    })
    return AskResponse.model_validate_response({
        "service_id": service["id"], "supported": True, "language": request.language,
        "answer": " ".join(steps), "steps": steps, "service": fields.model_dump(),
    })
