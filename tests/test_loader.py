from app.load_reference_data import load_all


def test_load_all_returns_expected_counts(db_conn):
    counts = load_all(db_conn)
    assert counts == {
        "denials": 4,
        "eligibility_benefits": 4,
        "utilization_history": 4,
    }


def test_known_denial_and_member_records_resolve(db_conn):
    load_all(db_conn)

    denial = db_conn.execute(
        "SELECT member_id, data FROM denials WHERE denial_reference = %s",
        ("DEN-2026-19811",),
    ).fetchone()
    assert denial is not None
    member_id, data = denial
    assert member_id == "MBR-778241"
    assert data["cited_policy_id"] == "CP-CARD-009"

    eligibility = db_conn.execute(
        "SELECT data FROM eligibility_benefits WHERE member_id = %s",
        ("MBR-778241",),
    ).fetchone()
    assert eligibility is not None
    assert eligibility[0]["plan_status"] == "active"

    utilization = db_conn.execute(
        "SELECT data FROM utilization_history WHERE member_id = %s",
        ("MBR-778241",),
    ).fetchone()
    assert utilization is not None
    assert utilization[0]["prior_appeals_on_file"] == 0


def test_denial_with_no_member_id_still_loads(db_conn):
    load_all(db_conn)

    denial = db_conn.execute(
        "SELECT member_id FROM denials WHERE denial_reference = %s",
        ("DEN-2026-19042",),
    ).fetchone()
    assert denial is not None
    assert denial[0] is None
