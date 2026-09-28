"""Loads reference_data/*.json into the structured Postgres tables.

Exact-match reference data only (denials, eligibility & benefits, utilization
history). Policy bulletins are not loaded here -- they belong in ChromaDB (Phase 3).
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from app.db import get_connection, init_db

REFERENCE_DATA_DIR = Path(__file__).resolve().parent.parent / "reference_data"


def _load_records(filename: str) -> list[dict]:
    path = REFERENCE_DATA_DIR / filename
    with path.open(encoding="utf-8") as f:
        return json.load(f)["records"]


def load_denials(conn: psycopg.Connection) -> int:
    records = _load_records("denials.json")
    for record in records:
        conn.execute(
            """
            INSERT INTO denials (denial_reference, member_id, data)
            VALUES (%s, %s, %s)
            ON CONFLICT (denial_reference) DO UPDATE
            SET member_id = EXCLUDED.member_id, data = EXCLUDED.data
            """,
            (record["denial_reference"], record.get("member_id"), Jsonb(record)),
        )
    return len(records)


def load_eligibility_benefits(conn: psycopg.Connection) -> int:
    records = _load_records("eligibility_benefits.json")
    for record in records:
        conn.execute(
            """
            INSERT INTO eligibility_benefits (member_id, data)
            VALUES (%s, %s)
            ON CONFLICT (member_id) DO UPDATE
            SET data = EXCLUDED.data
            """,
            (record["member_id"], Jsonb(record)),
        )
    return len(records)


def load_utilization_history(conn: psycopg.Connection) -> int:
    records = _load_records("utilization_history.json")
    for record in records:
        conn.execute(
            """
            INSERT INTO utilization_history (member_id, data)
            VALUES (%s, %s)
            ON CONFLICT (member_id) DO UPDATE
            SET data = EXCLUDED.data
            """,
            (record["member_id"], Jsonb(record)),
        )
    return len(records)


def load_all(conn: psycopg.Connection) -> dict[str, int]:
    return {
        "denials": load_denials(conn),
        "eligibility_benefits": load_eligibility_benefits(conn),
        "utilization_history": load_utilization_history(conn),
    }


def main() -> None:
    conn = get_connection()
    try:
        init_db(conn)
        counts = load_all(conn)
        for name, count in counts.items():
            print(f"loaded {count} records into {name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
