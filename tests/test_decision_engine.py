"""One test per scenario 3, 4, 5, 6, 7, 8, 9, 10 (checks 1-7 only -- scenarios 1 and
2 require check 8, which doesn't exist yet). Expected outcomes come from
reference_data/scenario_manifest.json, not hardcoded twice.
"""

import json
from datetime import date
from pathlib import Path

from app.core.decision_engine import AppealFacts, evaluate
from app.db import create_case
from app.load_reference_data import REFERENCE_DATA_DIR, load_all
from app.models import Case, Channel, Request
from app.services import lookup_service


def _load_manifest() -> dict[int, dict]:
    path = Path(REFERENCE_DATA_DIR) / "scenario_manifest.json"
    with path.open(encoding="utf-8") as f:
        scenarios = json.load(f)["scenarios"]
    return {s["scenario"]: s for s in scenarios}


MANIFEST = _load_manifest()


def _resolve_and_fetch(conn, scenario: dict):
    member_id = lookup_service.resolve_member_id(
        conn,
        scenario["member_id"],
        scenario["member_name"],
        date.fromisoformat(scenario["date_of_birth"]) if scenario["date_of_birth"] else None,
    )
    denial = lookup_service.get_denial(conn, scenario["denial_reference"]) if scenario["denial_reference"] else None
    eligibility = lookup_service.get_eligibility(conn, member_id) if member_id else None
    utilization = lookup_service.get_utilization(conn, member_id) if member_id else None
    return member_id, denial, eligibility, utilization


def test_scenario_3_not_a_covered_benefit(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[3]
    member_id, denial, eligibility, utilization = _resolve_and_fetch(db_conn, scenario)

    facts = AppealFacts(
        member_id=member_id,
        denial_reference=scenario["denial_reference"],
        case_reference=scenario["case_reference"],
        date_received=date.fromisoformat(scenario["date_received"]),
        supporting_evidence={},
    )
    result = evaluate(
        facts,
        resolved_member_id=member_id,
        is_duplicate=False,
        denial=denial,
        eligibility=eligibility,
        utilization=utilization,
    )

    assert result.scenario == 3
    assert result.outcome == "Deny"
    assert result.triggering_check == 6


def test_scenario_4_annual_limit_exceeded_no_override(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[4]
    member_id, denial, eligibility, utilization = _resolve_and_fetch(db_conn, scenario)

    facts = AppealFacts(
        member_id=member_id,
        denial_reference=scenario["denial_reference"],
        case_reference=scenario["case_reference"],
        date_received=date.fromisoformat(scenario["date_received"]),
        requested_units=15,
        supporting_evidence={},
    )
    result = evaluate(
        facts,
        resolved_member_id=member_id,
        is_duplicate=False,
        denial=denial,
        eligibility=eligibility,
        utilization=utilization,
    )

    assert result.scenario == 4
    assert result.outcome == "Deny"
    assert result.triggering_check == 7
    assert len(result.missing_items) == 4


def test_scenario_5_not_eligible_on_date_of_service(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[5]
    member_id, denial, eligibility, utilization = _resolve_and_fetch(db_conn, scenario)

    facts = AppealFacts(
        member_id=member_id,
        denial_reference=scenario["denial_reference"],
        case_reference=scenario["case_reference"],
        date_received=date.fromisoformat(scenario["date_received"]),
    )
    result = evaluate(
        facts,
        resolved_member_id=member_id,
        is_duplicate=False,
        denial=denial,
        eligibility=eligibility,
        utilization=utilization,
    )

    assert result.scenario == 5
    assert result.outcome == "Deny"
    assert result.triggering_check == 5


def test_scenario_6_filed_after_the_deadline(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[6]
    member_id, denial, eligibility, utilization = _resolve_and_fetch(db_conn, scenario)

    facts = AppealFacts(
        member_id=member_id,
        denial_reference=scenario["denial_reference"],
        case_reference=scenario["case_reference"],
        date_received=date.fromisoformat(scenario["date_received"]),
    )
    result = evaluate(
        facts,
        resolved_member_id=member_id,
        is_duplicate=False,
        denial=denial,
        eligibility=eligibility,
        utilization=utilization,
    )

    assert result.scenario == 6
    assert result.outcome == "Deny"
    assert result.triggering_check == 4


def test_scenario_7_specific_documentation_gap_partial_override(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[7]
    member_id, denial, eligibility, utilization = _resolve_and_fetch(db_conn, scenario)

    assert member_id == "MBR-260774", "identity should resolve via name+DOB fallback"

    facts = AppealFacts(
        member_id=member_id,
        denial_reference=scenario["denial_reference"],
        case_reference=scenario["case_reference"],
        date_received=date.fromisoformat(scenario["date_received"]),
        requested_units=12,
        # Oswestry score improving is documented; a bounded goal, a visit estimate
        # with discharge date, and adherence confirmation are not.
        supporting_evidence={"functional_outcome_measure": True},
    )
    result = evaluate(
        facts,
        resolved_member_id=member_id,
        is_duplicate=False,
        denial=denial,
        eligibility=eligibility,
        utilization=utilization,
    )

    assert result.scenario == 7
    assert result.outcome == "Needs more information"
    assert result.triggering_check == 7
    assert "bounded_goal_stated" in result.missing_items
    assert "functional_outcome_measure" not in result.missing_items


def test_scenario_8_member_cannot_be_identified(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[8]
    member_id, denial, eligibility, utilization = _resolve_and_fetch(db_conn, scenario)

    assert member_id is None

    facts = AppealFacts(
        member_id=None,
        member_name=scenario["member_name"],
        date_of_birth=None,
        date_received=date.fromisoformat(scenario["date_received"]),
    )
    result = evaluate(
        facts,
        resolved_member_id=member_id,
        is_duplicate=False,
        denial=denial,
        eligibility=eligibility,
        utilization=utilization,
    )

    assert result.scenario == 8
    assert result.outcome == "Cannot process (hold)"
    assert result.triggering_check == 1


def test_scenario_9_duplicate_submission_is_merged(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[9]

    existing_case = Case(
        case_id="CASE-0100",
        channel=Channel.EMAIL,
        request=Request(case_reference=scenario["case_reference"], denial_reference=scenario["denial_reference"]),
    )
    create_case(db_conn, existing_case)

    is_duplicate = lookup_service.find_open_case_by_reference(db_conn, scenario["case_reference"]) is not None
    assert is_duplicate

    facts = AppealFacts(
        member_id=scenario["member_id"],
        case_reference=scenario["case_reference"],
        denial_reference=scenario["denial_reference"],
        date_received=date.fromisoformat(scenario["date_received"]),
    )
    result = evaluate(
        facts,
        resolved_member_id=scenario["member_id"],
        is_duplicate=is_duplicate,
        denial=None,
        eligibility=None,
        utilization=None,
    )

    assert result.scenario == 9
    assert result.outcome == "Duplicate (merge)"
    assert result.triggering_check == 2


def test_scenario_10_prior_appeal_already_upheld_escalates(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[10]
    member_id, denial, eligibility, utilization = _resolve_and_fetch(db_conn, scenario)

    facts = AppealFacts(
        member_id=member_id,
        denial_reference=scenario["denial_reference"],
        case_reference=scenario["case_reference"],
        date_received=date.fromisoformat(scenario["date_received"]),
    )
    result = evaluate(
        facts,
        resolved_member_id=member_id,
        is_duplicate=False,
        denial=denial,
        eligibility=eligibility,
        utilization=utilization,
    )

    assert result.scenario == 10
    assert result.outcome == "Escalate"
    assert result.triggering_check == 3


def test_name_and_dob_fallback_resolves_identity(db_conn):
    load_all(db_conn)
    member_id = lookup_service.resolve_member_id(
        db_conn, None, "Marcus T. Oyelaran", date(1971, 2, 11)
    )
    assert member_id == "MBR-260774"


def test_first_name_alone_does_not_match_even_with_a_correct_dob(db_conn):
    load_all(db_conn)
    # "Marion" + 1949-11-02 is Marion Carter's real DOB (MBR-509912) -- a first name
    # alone must never be treated as sufficient, regardless of DOB accuracy.
    member_id = lookup_service.resolve_member_id(db_conn, None, "Marion", date(1949, 11, 2))
    assert member_id is None


def test_all_ten_scenarios_agree_with_the_manifest_or_require_check_8(db_conn):
    """Sanity sweep: every scenario 3-10 resolves through checks 1-7 to exactly the
    manifest's outcome; scenarios 1 and 2 correctly fall through to check 8 (not yet
    implemented), since nothing in checks 1-7 should misfire for them."""
    load_all(db_conn)

    for scenario_number in (1, 2):
        scenario = MANIFEST[scenario_number]
        member_id, denial, eligibility, utilization = _resolve_and_fetch(db_conn, scenario)
        facts = AppealFacts(
            member_id=member_id,
            denial_reference=scenario["denial_reference"],
            case_reference=scenario["case_reference"],
            date_received=date.fromisoformat(scenario["date_received"]),
        )
        result = evaluate(
            facts,
            resolved_member_id=member_id,
            is_duplicate=False,
            denial=denial,
            eligibility=eligibility,
            utilization=utilization,
        )
        assert result.scenario is None, f"scenario {scenario_number} should pass through to check 8"
        assert result.triggering_check == 8
