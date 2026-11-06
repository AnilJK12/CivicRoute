"""The only module that calls the Google Gen AI SDK."""

import json
import logging
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import settings

logger = logging.getLogger(__name__)
ALLOWED_SERVICE_IDS = ["aadhaar_update", "income_certificate", "civic_complaint", "unknown"]


class IntentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str


class TaskIntentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: Literal[
        "application_procedure", "document_checklist", "eligibility", "fee",
        "complaint_filing", "grievance_route", "office_location", "unknown",
    ]


class StepsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[str] = Field(min_length=1)

    @field_validator("steps")
    @classmethod
    def steps_must_be_nonblank(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("steps must not contain blank items")
        return value


class StepsComposition(BaseModel):
    steps: list[str]
    warnings: list[str] = Field(default_factory=list)


class GeminiClient:
    def __init__(self, api_key: str | None, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def _create_interaction(self, prompt: str, schema: dict[str, Any]) -> str | None:
        if not self.api_key:
            return None
        try:
            from google import genai

            client = genai.Client(api_key=self.api_key)
            response_format = {"type": "text", "mime_type": "application/json", "schema": schema}
            for attempt in range(2):
                try:
                    if hasattr(client, "interactions") and hasattr(client.interactions, "create"):
                        try:
                            interaction = client.interactions.create(
                                model=self.model,
                                input=prompt,
                                response_format=response_format,
                                timeout=8.0,
                                store=False,
                            )
                            output = getattr(interaction, "output_text", None)
                            if isinstance(output, str):
                                return output
                        except Exception as exc:
                            if hasattr(client, "models") and hasattr(client.models, "generate_content"):
                                res = client.models.generate_content(model=self.model, contents=prompt)
                                text = getattr(res, "text", None)
                                if isinstance(text, str):
                                    clean = text.strip()
                                    if clean.startswith("```json"):
                                        clean = clean[7:]
                                    elif clean.startswith("```"):
                                        clean = clean[3:]
                                    if clean.endswith("```"):
                                        clean = clean[:-3]
                                    return clean.strip()
                            raise exc
                    elif hasattr(client, "models") and hasattr(client.models, "generate_content"):
                        res = client.models.generate_content(model=self.model, contents=prompt)
                        text = getattr(res, "text", None)
                        if isinstance(text, str):
                            clean = text.strip()
                            if clean.startswith("```json"):
                                clean = clean[7:]
                            elif clean.startswith("```"):
                                clean = clean[3:]
                            if clean.endswith("```"):
                                clean = clean[:-3]
                            return clean.strip()
                except Exception:
                    if attempt == 0:
                        time.sleep(0.1)
            logger.warning("Gemini request failed after bounded retry")
        except Exception:
            # Never include prompts, keys, or provider exception details in logs.
            logger.warning("Gemini client could not complete the request")
        return None

    def classify_service(
        self,
        question: str,
        language: str,
        *,
        allowed_service_ids: list[str] | None = None,
        service_descriptions: list[dict[str, str]] | None = None,
    ) -> dict[str, object] | None:
        """Use structured output; pipeline validates shape, ID, and query relevance."""
        allowed_ids = list(dict.fromkeys(allowed_service_ids or ALLOWED_SERVICE_IDS[:-1]))
        schema = IntentOutput.model_json_schema()
        schema["properties"]["service_id"]["enum"] = [*allowed_ids, "unknown"]
        prompt = (
            "Classify the user's intent only when it clearly concerns one listed CivicRoute "
            "service. For unrelated, vague, or uncertain requests return unknown; do not infer "
            "the nearest service. Use only the supplied catalogue IDs and metadata. "
            f"Catalogue entries: {json.dumps(service_descriptions or [], ensure_ascii=False)}\n"
            f"Allowed IDs: {', '.join(allowed_ids)}, unknown.\n"
            f"Language: {language}\nQuestion: {question}"
        )
        text = self._create_interaction(prompt, schema)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Gemini intent output was not valid JSON")
            return None

    def classify_task(self, question: str, language: str) -> dict[str, object] | None:
        """Classify only the requested action; ambiguity is returned as `unknown`."""
        prompt = (
            "Classify the explicit task requested in the question. Do not infer a task from "
            "the service name alone. If the task is vague, multiple task types are requested, "
            "or no listed task is clear, return unknown. Do not provide service facts.\n"
            "Task types: application_procedure, document_checklist, eligibility, fee, "
            "complaint_filing, grievance_route, office_location, unknown.\n"
            f"Language: {language}\nQuestion: {question}"
        )
        text = self._create_interaction(prompt, TaskIntentOutput.model_json_schema())
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Gemini task output was not valid JSON")
            return None

    def compose_verified_steps(self, source_steps: list[str], language: str) -> str | None:
        """Send only source steps and target language; expect explanatory steps only."""
        prompt = (
            "Rewrite or translate these verified explanatory steps into the requested language. "
            "Preserve every meaning exactly: do not add, remove, strengthen, or infer any "
            "requirement. Do not create documents, fees, links, eligibility, office authority, "
            "deadlines, or legal claims. Return the same number of steps and JSON matching the "
            "provided schema. Requested language: "
            + language
            + "\nVerified steps: "
            + json.dumps(source_steps, ensure_ascii=False)
        )
        return self._create_interaction(prompt, StepsOutput.model_json_schema())


def _steps_from_entry(entry: Any) -> dict[str, list[str]]:
    if isinstance(entry, dict):
        value = entry.get("steps", {})
    else:
        value = getattr(entry, "steps", {})
    if not isinstance(value, dict):
        return {}
    return {
        language: steps
        for language, steps in value.items()
        if language in {"en", "kn"} and isinstance(steps, list)
        and all(isinstance(step, str) for step in steps)
    }


def compose_steps(
    entry: Any,
    language: str,
    *,
    gemini_client: Any | None = None,
) -> StepsComposition:
    """Compose explanatory wording without exposing non-step catalog fields to Gemini."""
    if language not in {"en", "kn"}:
        return StepsComposition(steps=[], warnings=["Requested language is unsupported."])

    stored = _steps_from_entry(entry)
    requested_steps = stored.get(language, [])
    # Prefer reviewed catalog Kannada text and never replace it with model output.
    if language == "kn" and requested_steps:
        return StepsComposition(steps=requested_steps)

    if requested_steps:
        source_steps = requested_steps
    elif stored.get("en"):
        source_steps = stored["en"]
    elif stored.get("kn"):
        source_steps = stored["kn"]
    else:
        return StepsComposition(steps=[], warnings=["No verified explanatory steps are available."])

    if not settings.gemini_api_key or not settings.gemini_model:
        warning = "Gemini is not configured; using stored verified steps."
        if source_steps is not requested_steps:
            warning = "Requested language is unavailable; using original verified steps."
        return StepsComposition(steps=list(source_steps), warnings=[warning])

    client = gemini_client or GeminiClient(settings.gemini_api_key, settings.gemini_model)
    try:
        raw = client.compose_verified_steps(list(source_steps), language)
        output = StepsOutput.model_validate_json(raw) if isinstance(raw, str) else StepsOutput.model_validate(raw)
        if len(output.steps) != len(source_steps):
            raise ValueError("step count changed")
        return StepsComposition(steps=output.steps)
    except Exception:
        # Provider output and exception details can contain sensitive context.
        logger.warning("Step composition failed; using stored verified steps")
        fallback = requested_steps or source_steps
        return StepsComposition(
            steps=list(fallback),
            warnings=["Generated wording was unavailable; original verified steps are shown."],
        )
