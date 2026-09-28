"""Tests for the reviewer-facing endpoints (app/api/review.py): the case queue,
the approve/edit/reject review action that closes a case, and the audit trail
read. Per CLAUDE.md, a recommendation is only ever a draft -- review_case() is
the one place a case is ever closed -- so most of these tests are really about
that invariant and about every status change leaving an audit entry
(app/security/audit.py).
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db import create_case
from app.main import app
from app.models import AuditEntry, Case, CaseStatus, Channel, Recommendation

client = TestClient(app)


def _drafted_case(case_id: str) -> Case:
    case = Case(case_id=case_id, channel=Channel.EMAIL, status=CaseStatus.DRAFTED)
    case.recommendation = Recommendation(decision="Deny", rationale="Not a covered benefit.", cited_guidelines=[])
    case.audit_trail.append(
        AuditEntry(timestamp=datetime.now(timezone.utc), actor="graph", action="Recommendation drafted: Deny (scenario 3)")
    )
    return case


def test_list_cases_returns_created_cases(db_conn):
    create_case(db_conn, _drafted_case("CASE-REVIEW-0001"))
    create_case(db_conn, _drafted_case("CASE-REVIEW-0002"))

    response = client.get("/cases")

    assert response.status_code == 200
    ids = {c["case_id"] for c in response.json()}
    assert {"CASE-REVIEW-0001", "CASE-REVIEW-0002"} <= ids


def test_list_cases_filters_by_status(db_conn):
    create_case(db_conn, _drafted_case("CASE-REVIEW-0003"))
    closed = _drafted_case("CASE-REVIEW-0004")
    closed.status = CaseStatus.CLOSED
    create_case(db_conn, closed)

    response = client.get("/cases", params={"status": "Drafted"})

    assert response.status_code == 200
    ids = {c["case_id"] for c in response.json()}
    assert "CASE-REVIEW-0003" in ids
    assert "CASE-REVIEW-0004" not in ids


def test_approve_closes_the_case_and_appends_an_audit_entry(db_conn):
    create_case(db_conn, _drafted_case("CASE-REVIEW-0010"))

    response = client.post(
        "/cases/CASE-REVIEW-0010/review",
        json={"reviewer": "jdoe", "action": "approve", "notes": "Looks right."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Closed"
    last_entry = body["audit_trail"][-1]
    assert last_entry["actor"] == "jdoe"
    assert "approved" in last_entry["action"]
    assert "Looks right." in last_entry["action"]


def test_edit_replaces_the_recommendation_and_closes_the_case(db_conn):
    create_case(db_conn, _drafted_case("CASE-REVIEW-0011"))

    response = client.post(
        "/cases/CASE-REVIEW-0011/review",
        json={
            "reviewer": "jdoe",
            "action": "edit",
            "edited_recommendation": {
                "decision": "Approve",
                "rationale": "Reviewer overturned on manual read of the chart.",
                "cited_guidelines": ["CP-OPHTH-005#c1"],
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Closed"
    assert body["recommendation"]["decision"] == "Approve"
    assert body["recommendation"]["cited_guidelines"] == ["CP-OPHTH-005#c1"]


def test_edit_without_edited_recommendation_is_rejected(db_conn):
    create_case(db_conn, _drafted_case("CASE-REVIEW-0012"))

    response = client.post(
        "/cases/CASE-REVIEW-0012/review",
        json={"reviewer": "jdoe", "action": "edit"},
    )

    assert response.status_code == 400


def test_reject_closes_the_case(db_conn):
    create_case(db_conn, _drafted_case("CASE-REVIEW-0013"))

    response = client.post(
        "/cases/CASE-REVIEW-0013/review",
        json={"reviewer": "jdoe", "action": "reject", "notes": "Needs a second look."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "Closed"
    assert "rejected" in body["audit_trail"][-1]["action"]


def test_review_unknown_case_returns_404(db_conn):
    response = client.post(
        "/cases/does-not-exist/review",
        json={"reviewer": "jdoe", "action": "approve"},
    )
    assert response.status_code == 404


def test_get_audit_trail_returns_every_entry(db_conn):
    create_case(db_conn, _drafted_case("CASE-REVIEW-0020"))
    client.post("/cases/CASE-REVIEW-0020/review", json={"reviewer": "jdoe", "action": "approve"})

    response = client.get("/cases/CASE-REVIEW-0020/audit")

    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 2  # drafted (seeded) + the review action
    assert entries[-1]["actor"] == "jdoe"


def test_get_audit_trail_for_unknown_case_returns_404(db_conn):
    response = client.get("/cases/does-not-exist/audit")
    assert response.status_code == 404


def test_chat_stub_responds_for_a_known_case(db_conn):
    create_case(db_conn, _drafted_case("CASE-REVIEW-0030"))

    response = client.post("/cases/CASE-REVIEW-0030/chat", json={"message": "Why was this denied?"})

    assert response.status_code == 200
    assert response.json()["reply"]


def test_chat_stub_for_unknown_case_returns_404(db_conn):
    response = client.post("/cases/does-not-exist/chat", json={"message": "hello"})
    assert response.status_code == 404
