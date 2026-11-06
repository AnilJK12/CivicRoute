"""Multi-task and named-unknown conversation tests use synthetic temporary records only."""

from datetime import date

from backend import pipeline
from backend.knowledge_base import ServiceRecord
from backend.schemas import AskRequest, ClarificationResponse, SupportedResponse, UnsupportedResponse


def entry(*, eligibility_status="verified", fee_status="verified", source_urls=None):
    source_url = "https://example.test/source"
    fact = None
    if eligibility_status is not None:
        fact = {
            "status": eligibility_status,
            "value": ["Synthetic eligibility criterion."] if eligibility_status == "verified" else None,
            "note": "Synthetic confirmed unavailable note." if eligibility_status == "verified_unavailable" else None,
            "source_urls": source_urls if source_urls is not None else [source_url],
        }
    return ServiceRecord.model_validate({
        "id": "sample_service",
        "name": "Synthetic sample service",
        "aliases": ["sample service"],
        "steps": {"en": ["Synthetic verified procedure step."]},
        "documents": [
            {"name": "Synthetic required document", "status": "required", "note": None},
            {"name": "Synthetic conditional document", "status": "conditional", "note": "Synthetic condition."},
            {"name": "Synthetic commonly requested document", "status": "commonly_requested", "note": None},
        ],
        "fee": {
            "amount": 125 if fee_status == "verified" else None,
            "currency": "INR", "status": fee_status, "note": None,
        },
        "online_option": {"available": None, "url": None, "status": "not_verified"},
        "office_type": None,
        "grievance_route": None,
        "sources": [{"title": "Synthetic source", "url": source_url}],
        "last_verified": date.today().isoformat(),
        "bundle_data": {"eligibility": fact} if fact is not None else {},
    })


def prepare(monkeypatch, record):
    monkeypatch.setattr(pipeline, "_known_service_ids", lambda: {record.id})
    monkeypatch.setattr(pipeline, "classify_intent", lambda *_: record.id)
    monkeypatch.setattr(pipeline, "retrieve_service", lambda service_id: record if service_id == record.id else None)
    monkeypatch.setattr(pipeline, "compose_steps", lambda service, language: service.steps[language])


def ask(question):
    return pipeline.answer_request(AskRequest(question=question, language="en"))


def test_eligibility_and_fees_are_returned_together(monkeypatch):
    record = entry()
    prepare(monkeypatch, record)

    response = ask("Am I eligible for a sample service and what are the fees?")

    assert isinstance(response, SupportedResponse)
    assert response.eligibility.status == "verified"
    assert response.eligibility.criteria == ["Synthetic eligibility criterion."]
    assert response.fee.status == "verified" and response.fee.amount == 125


def test_procedure_and_document_checklist_are_returned_together(monkeypatch):
    record = entry()
    prepare(monkeypatch, record)

    response = ask("How do I apply for the sample service and which documents do I need?")

    assert isinstance(response, SupportedResponse)
    assert response.steps == record.steps["en"]
    assert [document.status for document in response.documents] == [
        "required", "conditional", "commonly_requested",
    ]


def test_verified_eligibility_is_preserved_when_fee_is_unverified(monkeypatch):
    record = entry(fee_status="not_verified")
    prepare(monkeypatch, record)

    response = ask("Am I eligible for the sample service and what is its fee?")

    assert isinstance(response, SupportedResponse)
    assert response.eligibility.status == "verified"
    assert response.fee.status == "not_verified" and response.fee.amount is None
    assert any("Fee is not verified" in warning for warning in response.warnings)


def test_multiple_verified_fields_are_retained_and_unverified_eligibility_is_not_invented(monkeypatch):
    record = entry(eligibility_status="unverified")
    prepare(monkeypatch, record)

    response = ask("Am I eligible for the sample service and what are the fees?")

    assert isinstance(response, SupportedResponse)
    assert response.eligibility.status == "not_verified"
    assert response.eligibility.criteria == []
    assert response.fee.amount == 125 and response.fee.status == "verified"


def test_eligibility_without_matching_source_provenance_is_not_exposed(monkeypatch):
    record = entry(source_urls=["https://different.example.test/source"])
    prepare(monkeypatch, record)

    response = pipeline.validate_and_build_response(record, record.steps["en"], "en")

    assert isinstance(response, SupportedResponse)
    assert response.eligibility.status == "not_verified"
    assert response.eligibility.criteria == []


def test_named_uncatalogued_service_returns_unsupported(monkeypatch):
    monkeypatch.setattr(pipeline, "_in_memory_knowledge_base", lambda: {})

    response = ask("How do I apply for a transport permit?")

    assert isinstance(response, UnsupportedResponse)
    assert response.service_id == "unknown"
    assert "Which service do you mean" not in response.message


def test_genuinely_ambiguous_services_still_request_clarification():
    response = ask("How do I apply for an Aadhaar update or income certificate?")

    assert isinstance(response, ClarificationResponse)
    assert "service_id" in response.missing_fields


def test_task_only_question_still_asks_which_service():
    response = ask("Which documents do I need?")

    assert isinstance(response, ClarificationResponse)
    assert "service_id" in response.missing_fields
