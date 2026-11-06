"""Response builder tests ensure generated wording cannot control service facts."""

from datetime import date

from backend.knowledge_base import ServiceRecord
from backend.pipeline import validate_and_build_response
from backend.schemas import SupportedResponse


def make_entry(*, fee=None, last_verified="2026-10-01"):
    return ServiceRecord.model_validate({
        "id": "civic_complaint",
        "name": "Synthetic verified entry",
        "steps": {"en": ["Verified stored step one.", "Verified stored step two."],
                  "kn": ["ಪರಿಶೀಲಿಸಿದ ಹಂತ ಒಂದು.", "ಪರಿಶೀಲಿಸಿದ ಹಂತ ಎರಡು."]},
        "documents": [
            {"name": "Synthetic required item", "status": "required", "note": None},
            {"name": "Synthetic optional item", "status": "commonly_requested", "note": "Not mandatory."},
            {"name": "Synthetic conditional item", "status": "conditional", "note": "Only if applicable."},
        ],
        "fee": fee or {"amount": 25, "currency": "INR", "status": "verified", "note": "Synthetic verified amount."},
        "online_option": {"available": True, "url": "https://example.test/official", "status": "verified"},
        "office_type": "Synthetic office type",
        "grievance_route": "Synthetic grievance route",
        "sources": [{"title": "Synthetic official source", "url": "https://example.test/source"}],
        "last_verified": last_verified,
    })


def test_response_structured_fields_come_directly_from_entry():
    entry = make_entry()
    result = validate_and_build_response(entry, ["Simplified step one.", "Simplified step two."], "en")
    assert isinstance(result, SupportedResponse)
    assert result.steps == ["Simplified step one.", "Simplified step two."]
    assert result.documents[1].status == "commonly_requested"
    assert result.documents[2].status == "conditional"
    assert result.fee.amount == entry.fee.amount
    assert result.online_option.url == entry.online_option.url
    assert result.office_type == entry.office_type
    assert result.grievance == entry.grievance_route
    assert [source.url for source in result.sources] == [source.url for source in entry.sources]
    assert result.last_verified == entry.last_verified.isoformat()


def test_model_fake_fee_and_link_cannot_override_authoritative_entry():
    entry = make_entry()
    composed = {
        "steps": ["Generated explanatory step one.", "Generated explanatory step two."],
        "fee": {"amount": 0, "currency": "INR", "status": "verified", "note": "free"},
        "online_option": {"available": True, "url": "https://fake.test/portal", "status": "verified"},
        "sources": [{"title": "Fake", "url": "https://fake.test/source"}],
    }
    result = validate_and_build_response(entry, composed, "en")
    assert isinstance(result, SupportedResponse)
    # Extra generated fields invalidate generated output; stored steps and all facts win.
    assert result.steps == entry.steps["en"]
    assert result.fee.amount == 25
    assert result.fee.status == "verified"
    assert result.online_option.url == "https://example.test/official"
    assert [source.url for source in result.sources] == ["https://example.test/source"]
    assert any("invalid" in warning for warning in result.warnings)


def test_unverified_fee_remains_unknown_not_zero():
    entry = make_entry(fee={"amount": None, "currency": "INR", "status": "not_verified", "note": None})
    result = validate_and_build_response(entry, entry.steps["en"], "en")
    assert result.fee.amount is None
    assert result.fee.status == "not_verified"
    assert any("unknown" in warning for warning in result.warnings)


def test_missing_and_old_dates_warn_without_inventing_date():
    missing = validate_and_build_response(make_entry(last_verified=None), None, "en")
    assert missing.last_verified is None
    assert any("not available" in warning for warning in missing.warnings)

    old = validate_and_build_response(make_entry(last_verified="2000-01-01"), None, "en")
    assert old.last_verified == "2000-01-01"
    assert any("out of date" in warning for warning in old.warnings)


def test_source_urls_are_exactly_copied_from_entry():
    entry = make_entry()
    result = validate_and_build_response(entry, entry.steps["en"], "en")
    assert {source.url for source in result.sources} <= {source.url for source in entry.sources}


def test_missing_information_is_marked_in_warnings():
    entry = make_entry()
    record = entry.model_dump()
    record["online_option"] = {"available": None, "url": None, "status": "not_verified"}
    record["office_type"] = None
    record["grievance_route"] = None
    record["documents"] = []
    record["last_verified"] = None
    result = validate_and_build_response(record, record["steps"]["en"], "en")
    assert result.last_verified is None
    assert len(result.warnings) >= 4
