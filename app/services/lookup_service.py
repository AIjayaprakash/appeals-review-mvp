"""Exact-match reads against the structured reference data and the cases table.

Denial / eligibility / utilization records are looked up by member_id or
denial_reference only -- never a similarity search (see CLAUDE.md's data rules).
Identity resolution (check 1) and duplicate detection (check 2) live here too,
since both require a database read; app/core/decision_engine.py stays pure and
just consumes whatever this module returns.
"""

from __future__ import annotations

from datetime import date

import psycopg

CLOSED_STATUS = "Closed"


def has_sufficient_identity(
    member_id: str | None, member_name: str | None, date_of_birth: date | None
) -> bool:
    """Whether there is enough to even attempt identification -- a member_id, or a
    full name (2+ tokens) plus a date of birth. Per CLAUDE.md, a first name alone,
    with or without a DOB, is never sufficient. Pure and DB-free: the Intake Agent
    uses this to decide Structured vs. Needs Clarification before any lookup runs;
    resolve_member_id() below uses it to gate the name+DOB fallback lookup itself.
    """
    if member_id:
        return True
    return bool(member_name and date_of_birth and len(member_name.strip().split()) >= 2)


def get_denial(conn: psycopg.Connection, denial_reference: str) -> dict | None:
    row = conn.execute(
        "SELECT data FROM denials WHERE denial_reference = %s", (denial_reference,)
    ).fetchone()
    return row[0] if row else None


def get_eligibility(conn: psycopg.Connection, member_id: str) -> dict | None:
    row = conn.execute(
        "SELECT data FROM eligibility_benefits WHERE member_id = %s", (member_id,)
    ).fetchone()
    return row[0] if row else None


def get_eligibility_by_name_dob(
    conn: psycopg.Connection, member_name: str, date_of_birth: date
) -> dict | None:
    row = conn.execute(
        """
        SELECT data FROM eligibility_benefits
        WHERE data->>'member_name' = %s AND data->>'date_of_birth' = %s
        """,
        (member_name, date_of_birth.isoformat()),
    ).fetchone()
    return row[0] if row else None


def get_utilization(conn: psycopg.Connection, member_id: str) -> dict | None:
    row = conn.execute(
        "SELECT data FROM utilization_history WHERE member_id = %s", (member_id,)
    ).fetchone()
    return row[0] if row else None


def resolve_member_id(
    conn: psycopg.Connection,
    member_id: str | None,
    member_name: str | None,
    date_of_birth: date | None,
) -> str | None:
    """Check 1's identity resolution.

    A direct member_id match is attempted first. Only when member_id is absent is a
    name+DOB fallback attempted, and only when the name has more than one token --
    per CLAUDE.md, a first name alone (with or without a DOB) must never resolve.
    """
    if not has_sufficient_identity(member_id, member_name, date_of_birth):
        return None

    if member_id:
        eligibility = get_eligibility(conn, member_id)
        return member_id if eligibility is not None else None

    eligibility = get_eligibility_by_name_dob(conn, member_name, date_of_birth)
    return eligibility["member_id"] if eligibility is not None else None


def find_open_case_by_reference(conn: psycopg.Connection, case_reference: str | None) -> dict | None:
    if not case_reference:
        return None
    row = conn.execute(
        """
        SELECT data FROM cases
        WHERE data->'request'->>'case_reference' = %s AND status <> %s
        """,
        (case_reference, CLOSED_STATUS),
    ).fetchone()
    return row[0] if row else None
