import json
from pathlib import Path

from app.load_reference_data import REFERENCE_DATA_DIR, load_all

MANIFEST_PATH = REFERENCE_DATA_DIR / "scenario_manifest.json"


def _load_manifest() -> list[dict]:
    with Path(MANIFEST_PATH).open(encoding="utf-8") as f:
        return json.load(f)["scenarios"]


def test_manifest_covers_all_ten_scenarios():
    scenarios = _load_manifest()
    assert sorted(s["scenario"] for s in scenarios) == list(range(1, 11))


def test_every_scenario_with_a_member_id_resolves_in_reference_data(db_conn):
    load_all(db_conn)
    scenarios = _load_manifest()

    for s in scenarios:
        if s["member_id"] is None:
            continue

        eligibility = db_conn.execute(
            "SELECT 1 FROM eligibility_benefits WHERE member_id = %s",
            (s["member_id"],),
        ).fetchone()
        assert eligibility is not None, f"scenario {s['scenario']}: no eligibility_benefits row for {s['member_id']}"

        utilization = db_conn.execute(
            "SELECT 1 FROM utilization_history WHERE member_id = %s",
            (s["member_id"],),
        ).fetchone()
        assert utilization is not None, f"scenario {s['scenario']}: no utilization_history row for {s['member_id']}"


def test_every_scenario_with_a_denial_reference_resolves_in_denials(db_conn):
    load_all(db_conn)
    scenarios = _load_manifest()

    for s in scenarios:
        if s["denial_reference"] is None:
            continue

        row = db_conn.execute(
            "SELECT member_id FROM denials WHERE denial_reference = %s",
            (s["denial_reference"],),
        ).fetchone()
        assert row is not None, f"scenario {s['scenario']}: no denials row for {s['denial_reference']}"
        # denials.member_id may be null even when the manifest names a member_id --
        # that's the name+DOB fallback path (scenario 7), where identity is resolved
        # via eligibility_benefits, not stamped on the original denial record.
        if row[0] is not None:
            assert row[0] == s["member_id"], f"scenario {s['scenario']}: denials.member_id mismatch"


def test_scenario_10_prior_appeal_is_marked_upheld_in_utilization_history(db_conn):
    load_all(db_conn)
    row = db_conn.execute(
        "SELECT data FROM utilization_history WHERE member_id = %s",
        ("MBR-556012",),
    ).fetchone()
    assert row is not None
    history = row[0]["history"]
    upheld_appeals = [h for h in history if h["type"] == "appeal" and h["outcome"] == "upheld"]
    assert len(upheld_appeals) == 1


def test_scenario_6_appeal_received_after_deadline(db_conn):
    load_all(db_conn)
    scenarios = _load_manifest()
    scenario_6 = next(s for s in scenarios if s["scenario"] == 6)

    row = db_conn.execute(
        "SELECT data FROM denials WHERE denial_reference = %s",
        (scenario_6["denial_reference"],),
    ).fetchone()
    assert row is not None
    assert scenario_6["date_received"] > row[0]["appeal_deadline"]


def test_scenario_5_date_of_service_falls_inside_coverage_gap(db_conn):
    load_all(db_conn)
    denial = db_conn.execute(
        "SELECT data FROM denials WHERE denial_reference = %s",
        ("DEN-2026-20690",),
    ).fetchone()
    eligibility = db_conn.execute(
        "SELECT data FROM eligibility_benefits WHERE member_id = %s",
        ("MBR-904433",),
    ).fetchone()
    assert denial is not None and eligibility is not None

    date_of_service = denial[0]["date_of_service"]
    gap = eligibility[0]["coverage_gaps"][0]
    assert gap["start_date"] <= date_of_service <= gap["end_date"]
