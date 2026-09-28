"""End-to-end tests for the compiled LangGraph (app/core/graph.py): intake ->
lookup -> rules_engine -> (evaluation_agent, conditional) with summarization in
parallel -> finalize. Reference-data lookups are real (same load_all(db_conn)
pattern as test_decision_engine.py); only the three LLM nodes are mocked, same
pattern as test_intake_api.py's endpoint-level tests.

These tests exist specifically to pin down two things unit tests on the
individual nodes can't: that finalize only ever runs once per case (the
evaluation/summarization branches must reconverge cleanly -- see graph.py's
module docstring for why that's not automatic in LangGraph), and that the
right branch (evaluate vs. skip, or the Needs-Clarification short-circuit) is
taken for a real decision_engine outcome.
"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.core import graph as pipeline_graph
from app.core.nodes import ARRAY_FIELDS, REQUIRED_FIELDS
from app.db import create_case
from app.load_reference_data import REFERENCE_DATA_DIR, load_all
from app.models import Case, Channel

_MOCK_EVALUATION_RESULT = {
    "decision": "Approve",
    "scenario": 1,
    "overall_rationale": "All CP-CARD-009 criteria are met (mocked).",
    "cited_guidelines": ["CP-CARD-009#c1"],
}
_MOCK_SUMMARY = "Member [[MEMBER_ID_1]] appeals a denied CRT-D implant (mocked summary)."


def _load_manifest() -> dict[int, dict]:
    path = Path(REFERENCE_DATA_DIR) / "scenario_manifest.json"
    with path.open(encoding="utf-8") as f:
        scenarios = json.load(f)["scenarios"]
    return {s["scenario"]: s for s in scenarios}


MANIFEST = _load_manifest()


def _intake_fields(scenario: dict) -> dict:
    result = {name: None for name in REQUIRED_FIELDS}
    result["member_id"] = scenario["member_id"]
    result["member_name"] = scenario["member_name"]
    result["date_of_birth"] = scenario["date_of_birth"]
    result["case_reference"] = scenario["case_reference"]
    result["denial_reference"] = scenario["denial_reference"]
    for name in ARRAY_FIELDS:
        result[name] = []
    return result


def _run(scenario: dict, case_id: str) -> Case:
    case = Case(case_id=case_id, channel=Channel.EMAIL)
    return pipeline_graph.run_pipeline(
        case, "irrelevant raw text", received_date=date.fromisoformat(scenario["date_received"])
    )


def test_needs_clarification_short_circuits_before_lookup_or_evaluation(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[8]  # Marion Carter -- first name only, no member_id

    with (
        patch("app.core.graph.intake_agent", return_value=_intake_fields(scenario)),
        patch("app.core.graph.evaluation_agent") as mock_eval,
        patch("app.core.graph.summarization_agent") as mock_summary,
    ):
        case = _run(scenario, "CASE-GRAPH-0001")

    assert case.status == "Needs Clarification"
    assert case.recommendation is None
    assert case.clinical_summary is None
    mock_eval.assert_not_called()
    mock_summary.assert_not_called()
    assert case.audit_trail[-1].action.startswith("Extracted intake fields; insufficient identity")


def test_deny_not_covered_skips_evaluation_agent(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[3]  # Priya Anand -- check 6 conclusive, check 8 never reached

    with (
        patch("app.core.graph.intake_agent", return_value=_intake_fields(scenario)),
        patch("app.core.graph.evaluation_agent") as mock_eval,
        patch("app.core.graph.summarization_agent", return_value=_MOCK_SUMMARY),
    ):
        case = _run(scenario, "CASE-GRAPH-0002")

    mock_eval.assert_not_called()
    assert case.status == "Drafted"
    assert case.recommendation.decision == "Deny"
    assert case.recommendation.cited_guidelines == []
    assert "scenario 3" in case.audit_trail[-1].action
    assert case.clinical_summary == _MOCK_SUMMARY


def test_all_checks_pass_reaches_evaluation_agent_and_finalizes_once(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[1]  # Eleanor Whitfield -- checks 1-7 all pass

    with (
        patch("app.core.graph.intake_agent", return_value=_intake_fields(scenario)) as mock_intake,
        patch("app.core.graph.evaluation_agent", return_value=_MOCK_EVALUATION_RESULT) as mock_eval,
        patch("app.core.graph.summarization_agent", return_value=_MOCK_SUMMARY) as mock_summary,
    ):
        case = _run(scenario, "CASE-GRAPH-0003")

    mock_intake.assert_called_once()
    mock_eval.assert_called_once()
    mock_summary.assert_called_once()
    assert case.status == "Drafted"
    assert case.recommendation.decision == "Approve"
    assert case.recommendation.cited_guidelines == ["CP-CARD-009#c1"]
    assert case.clinical_summary == _MOCK_SUMMARY
    # finalize must run exactly once: SUMMARIZED then DRAFTED, not duplicated or
    # interleaved with a premature partial finalize.
    statuses = [entry.action for entry in case.audit_trail]
    assert sum("Clinical summary and decision compiled" in a for a in statuses) == 1
    assert sum("Recommendation drafted" in a for a in statuses) == 1


def test_duplicate_submission_is_detected_and_skips_evaluation(db_conn):
    load_all(db_conn)
    scenario = MANIFEST[9]  # Eleanor Whitfield's duplicate submission

    existing_case = Case(case_id="CASE-GRAPH-EXISTING", channel=Channel.EMAIL)
    existing_case.request.case_reference = scenario["case_reference"]
    create_case(db_conn, existing_case)

    with (
        patch("app.core.graph.intake_agent", return_value=_intake_fields(scenario)),
        patch("app.core.graph.evaluation_agent") as mock_eval,
        patch("app.core.graph.summarization_agent", return_value=_MOCK_SUMMARY),
    ):
        case = _run(scenario, "CASE-GRAPH-0004")

    mock_eval.assert_not_called()
    assert case.recommendation.decision == "Duplicate (merge)"
    assert "scenario 9" in case.audit_trail[-1].action
