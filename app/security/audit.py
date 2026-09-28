"""Audit trail: appends an AuditEntry to a Case whenever its status changes.

Every status transition must be recorded (CLAUDE.md). transition_status() is the
only place that changes case.status -- callers should never set case.status
directly, so a status change can never happen without an audit entry.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import AuditEntry, Case, CaseStatus


def transition_status(case: Case, new_status: CaseStatus, *, actor: str, action: str) -> Case:
    case.status = new_status
    case.audit_trail.append(
        AuditEntry(timestamp=datetime.now(timezone.utc), actor=actor, action=action)
    )
    return case
