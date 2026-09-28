"""Reviewer-facing endpoints: the case queue, and the single action -- approve,
edit, or reject a drafted recommendation -- that closes a case. Per CLAUDE.md, a
recommendation is only ever a draft; this review action is the one place a case
is ever closed, and it always appends an audit entry (app/security/audit.py).
"""

from __future__ import annotations

from enum import Enum

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db
from app.models import AuditEntry, Case, CaseStatus, Recommendation
from app.security.audit import transition_status

router = APIRouter(prefix="/cases", tags=["review"])


class ReviewAction(str, Enum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


_ACTION_VERBS = {
    ReviewAction.APPROVE: "approved",
    ReviewAction.EDIT: "edited",
    ReviewAction.REJECT: "rejected",
}


class ReviewRequest(BaseModel):
    reviewer: str
    action: ReviewAction
    notes: str | None = None
    edited_recommendation: Recommendation | None = None


@router.get("", response_model=list[Case])
def list_cases(status: CaseStatus | None = None) -> list[Case]:
    conn = db.get_connection()
    try:
        return db.list_cases(conn, status.value if status else None)
    finally:
        conn.close()


@router.post("/{case_id}/review", response_model=Case)
def review_case(case_id: str, review: ReviewRequest) -> Case:
    conn = db.get_connection()
    try:
        case = db.get_case(conn, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")

        if review.action == ReviewAction.EDIT:
            if review.edited_recommendation is None:
                raise HTTPException(status_code=400, detail="edited_recommendation is required for action 'edit'")
            case.recommendation = review.edited_recommendation

        action_summary = f"Reviewer {review.reviewer} {_ACTION_VERBS[review.action]} the recommendation"
        if review.notes:
            action_summary += f": {review.notes}"
        transition_status(case, CaseStatus.CLOSED, actor=review.reviewer, action=action_summary)

        db.create_case(conn, case)
        return case
    finally:
        conn.close()


@router.get("/{case_id}/audit", response_model=list[AuditEntry])
def get_audit_trail(case_id: str) -> list[AuditEntry]:
    conn = db.get_connection()
    try:
        case = db.get_case(conn, case_id)
    finally:
        conn.close()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case.audit_trail
