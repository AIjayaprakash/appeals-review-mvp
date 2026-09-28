"""Postgres connection + table setup.

Phase 0 only: table creation and bare Case read/write. No agent, redaction, or
decision-engine logic lives here.
"""

from __future__ import annotations

import os

import psycopg
from psycopg.types.json import Jsonb

from app.models import Case

DEFAULT_DATABASE_URL = "postgresql://appeals:appeals@localhost:5433/appeals"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_connection() -> psycopg.Connection:
    conn = psycopg.connect(get_database_url(), autocommit=True)
    return conn


def init_db(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cases (
            case_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            data JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS denials (
            denial_reference TEXT PRIMARY KEY,
            member_id TEXT,
            data JSONB NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS eligibility_benefits (
            member_id TEXT PRIMARY KEY,
            data JSONB NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS utilization_history (
            member_id TEXT PRIMARY KEY,
            data JSONB NOT NULL
        )
        """
    )


def create_case(conn: psycopg.Connection, case: Case) -> None:
    conn.execute(
        """
        INSERT INTO cases (case_id, status, data)
        VALUES (%s, %s, %s)
        ON CONFLICT (case_id) DO UPDATE
        SET status = EXCLUDED.status, data = EXCLUDED.data, updated_at = now()
        """,
        (case.case_id, case.status.value, Jsonb(case.model_dump(mode="json"))),
    )


def get_case(conn: psycopg.Connection, case_id: str) -> Case | None:
    row = conn.execute(
        "SELECT data FROM cases WHERE case_id = %s", (case_id,)
    ).fetchone()
    if row is None:
        return None
    return Case.model_validate(row[0])
