"""Full intake pipeline (ingestion -> redaction -> intake_agent -> re-hydration ->
Case) tested against test_data/expected_extraction.json. The LLM is mocked with the
ground-truth fields for each fixture -- these tests are about the pipeline's
plumbing (parsing, OCR, status determination, field mapping), not about how well a
real model extracts, which the opt-in test in test_nodes_intake_agent.py covers.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.nodes import ARRAY_FIELDS, REQUIRED_FIELDS
from app.db import get_connection
from app.main import app
from app.models import CaseStatus, Channel
from app.services.email_connector import parse_eml

client = TestClient(app)

TEST_DATA_DIR = Path(__file__).resolve().parent.parent / "test_data"


@pytest.fixture(autouse=True)
def _clean_cases_table():
    yield
    conn = get_connection()
    try:
        conn.execute("DELETE FROM cases")
    finally:
        conn.close()


def _load_expected(test_file: str) -> dict:
    with (TEST_DATA_DIR / "expected_extraction.json").open(encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    return next(c for c in cases if c["test_file"] == test_file)


def _mock_llm_response(expected_fields: dict) -> dict:
    """A stand-in for a perfectly correct LLM: returns the ground-truth values
    directly (no redaction tokens), since these tests are validating the pipeline
    around the LLM call, not the redact/rehydrate round trip (see test_redaction.py
    and test_nodes_intake_agent.py for that)."""
    result = {name: expected_fields.get(name) for name in REQUIRED_FIELDS}
    for name in ARRAY_FIELDS:
        result[name] = expected_fields.get(name) or []
    return result


def test_email_001_matches_expected_extraction():
    expected = _load_expected("emails/EMAIL-001_typical_with_attachment.eml")
    fields = expected["expected_fields"]
    parsed = parse_eml((TEST_DATA_DIR / "emails" / "EMAIL-001_typical_with_attachment.eml").read_bytes())

    from app.api.intake import build_case

    with patch("app.api.intake.intake_agent", return_value=_mock_llm_response(fields)):
        case = build_case("CASE-TEST-EMAIL-001", Channel.EMAIL, parsed.body_text, documents=[])

    assert case.status == CaseStatus.STRUCTURED
    assert case.member.member_id == fields["member_id"]
    assert case.member.name == fields["member_name"]
    assert case.member.date_of_birth.isoformat() == fields["date_of_birth"]
    assert case.request.case_reference == fields["case_reference"]
    assert case.request.denial_reference == fields["denial_reference"]
    assert case.provider.name == fields["requesting_provider"]
    assert case.provider.organization == fields["sender_org"]
    assert case.request.procedure == fields["procedure_requested"]


def test_email_002_ends_at_needs_clarification_with_member_id_null():
    expected = _load_expected("emails/EMAIL-002_minimal_missing_member_id.eml")
    fields = expected["expected_fields"]
    assert expected["expect_status_after_intake"] == "Needs Clarification"

    from app.api.intake import build_case

    with patch("app.api.intake.intake_agent", return_value=_mock_llm_response(fields)):
        case = build_case("CASE-TEST-EMAIL-002", Channel.EMAIL, "irrelevant working text", documents=[])

    assert case.status == CaseStatus.NEEDS_CLARIFICATION
    assert case.member.member_id is None
    assert case.member.date_of_birth is None
    assert case.request.case_reference is None


def test_email_003_matches_expected_extraction():
    expected = _load_expected("emails/EMAIL-003_forwarded_multi_attachment.eml")
    fields = expected["expected_fields"]

    from app.api.intake import build_case

    with patch("app.api.intake.intake_agent", return_value=_mock_llm_response(fields)):
        case = build_case("CASE-TEST-EMAIL-003", Channel.EMAIL, "irrelevant working text", documents=[])

    assert case.status == CaseStatus.STRUCTURED
    assert case.member.member_id == fields["member_id"]
    assert case.request.denial_reference == fields["denial_reference"]
    assert case.provider.organization == fields["sender_org"]


def test_medhist_001_via_upload_endpoint():
    expected = _load_expected("medical_history/MEDHIST-001_structured_case_record.pdf")
    fields = expected["expected_fields"]
    pdf_bytes = (TEST_DATA_DIR / "medical_history" / "MEDHIST-001_structured_case_record.pdf").read_bytes()

    with patch("app.api.intake.intake_agent", return_value=_mock_llm_response(fields)):
        response = client.post(
            "/intake/upload",
            files={"file": ("MEDHIST-001_structured_case_record.pdf", pdf_bytes, "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Structured"
    assert body["channel"] == "upload"
    assert body["member"]["member_id"] == fields["member_id"]
    assert body["diagnosis_codes"] == fields["diagnosis_codes"]


def test_medhist_002_via_upload_endpoint_is_structured_via_name_dob():
    expected = _load_expected("medical_history/MEDHIST-002_appeal_letter_narrative.pdf")
    fields = expected["expected_fields"]
    assert expected["expect_status_after_intake"] == "Structured"
    pdf_bytes = (TEST_DATA_DIR / "medical_history" / "MEDHIST-002_appeal_letter_narrative.pdf").read_bytes()

    with patch("app.api.intake.intake_agent", return_value=_mock_llm_response(fields)):
        response = client.post(
            "/intake/upload",
            files={"file": ("MEDHIST-002_appeal_letter_narrative.pdf", pdf_bytes, "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    # member_id is genuinely absent from this letter -- Structured here rests on
    # the full name + DOB fallback, not a member_id.
    assert body["member"]["member_id"] is None
    assert body["member"]["name"] == fields["member_name"]
    assert body["member"]["date_of_birth"] == fields["date_of_birth"]
    assert body["status"] == "Structured"


def test_medhist_003_via_upload_endpoint():
    expected = _load_expected("medical_history/MEDHIST-003_multipage_labs.pdf")
    fields = expected["expected_fields"]
    pdf_bytes = (TEST_DATA_DIR / "medical_history" / "MEDHIST-003_multipage_labs.pdf").read_bytes()

    with patch("app.api.intake.intake_agent", return_value=_mock_llm_response(fields)):
        response = client.post(
            "/intake/upload",
            files={"file": ("MEDHIST-003_multipage_labs.pdf", pdf_bytes, "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Structured"
    assert body["member"]["member_id"] == fields["member_id"]
    assert body["diagnosis_codes"] == fields["diagnosis_codes"]


def test_poll_email_endpoint_returns_one_case_per_mailbox_file():
    from app.services.email_connector import list_test_mailbox

    expected_count = len(list_test_mailbox())

    with patch("app.api.intake.intake_agent", return_value=_mock_llm_response({})):
        response = client.post("/intake/email/poll")

    assert response.status_code == 200
    cases = response.json()
    assert len(cases) == expected_count
    for case in cases:
        assert case["channel"] == "email"
        assert case["status"] in {"Structured", "Needs Clarification"}


def test_uploaded_case_is_readable_via_get_cases_endpoint():
    expected = _load_expected("medical_history/MEDHIST-001_structured_case_record.pdf")
    fields = expected["expected_fields"]
    pdf_bytes = (TEST_DATA_DIR / "medical_history" / "MEDHIST-001_structured_case_record.pdf").read_bytes()

    with patch("app.api.intake.intake_agent", return_value=_mock_llm_response(fields)):
        upload_response = client.post(
            "/intake/upload",
            files={"file": ("MEDHIST-001_structured_case_record.pdf", pdf_bytes, "application/pdf")},
        )
    case_id = upload_response.json()["case_id"]

    get_response = client.get(f"/cases/{case_id}")

    assert get_response.status_code == 200
    assert get_response.json()["member"]["member_id"] == fields["member_id"]
