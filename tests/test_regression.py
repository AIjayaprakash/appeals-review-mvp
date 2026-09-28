"""Phase 5: full-graph regression suite.

Runs every case in reference_data/scenario_manifest.json (all 10 outcome
scenarios, including every negative one) and every case in
test_data/expected_extraction.json through the compiled LangGraph
(app.core.graph.run_pipeline) -- not just decision_engine.evaluate() in
isolation (see test_decision_engine.py for that) or a single node in isolation
(see test_nodes_*.py) -- so a regression in how the graph wires nodes together
is caught even when every node's own unit tests still pass.

The three LLM nodes are mocked with recorded fixtures: for the scenarios that
reach check 8 (1 and 2), the fixture is produced by actually calling the real
evaluation_agent() node against a scripted Azure response (same technique as
test_nodes_evaluation_agent.py), so decision_engine.check_8_from_criteria's own
compilation logic runs for real -- only the network call is faked. Everything
else uses simple mocks, same pattern as test_graph.py.

One opt-in test at the bottom (test_full_pipeline_live_run_thomas_reyes_...)
runs the full graph against the real Azure OpenAI intake_agent and
summarization_agent; it picks a scenario where check 4 is conclusive so
evaluation_agent (which would also need a live ChromaDB) is never reached --
that node's own live test is test_nodes_evaluation_agent.py's opt-in test.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core import graph as pipeline_graph
from app.core.nodes import ARRAY_FIELDS, REQUIRED_FIELDS
from app.core.nodes import evaluation_agent as real_evaluation_agent
from app.db import create_case
from app.load_reference_data import REFERENCE_DATA_DIR, load_all
from app.models import Case, CaseStatus, Channel
from app.services import email_connector, ocr_service
from app.services.policy_retrieval_service import (
    APPEAL_GUIDANCE,
    CRITERION,
    DENIAL_REASONS,
    load_policy_bulletin_chunks,
)

TEST_DATA_DIR = Path(__file__).resolve().parent.parent / "test_data"
_ALL_POLICY_CHUNKS = load_policy_bulletin_chunks()

_MOCK_SUMMARY = "Member [[MEMBER_ID_1]] appeals a denied service (recorded regression fixture)."


def _load_manifest() -> dict[int, dict]:
    with (Path(REFERENCE_DATA_DIR) / "scenario_manifest.json").open(encoding="utf-8") as f:
        scenarios = json.load(f)["scenarios"]
    return {s["scenario"]: s for s in scenarios}


def _load_expected_extraction() -> list[dict]:
    with (TEST_DATA_DIR / "expected_extraction.json").open(encoding="utf-8") as f:
        return json.load(f)["cases"]


MANIFEST = _load_manifest()
EXPECTED_EXTRACTION = _load_expected_extraction()

# --------------------------------------------------------------------------
# Per-scenario intake facts that no node extracts yet from real test files
# (scenarios 4 and 7 have no backing test_data document -- see
# scenario_manifest.json's own notes on that), matched 1:1 against the values
# test_decision_engine.py already validates the pure decision_engine against.
_REQUESTED_UNITS = {4: 15, 7: 12}
_SUPPORTING_EVIDENCE = {7: {"functional_outcome_measure": True}}


def _intake_fields(scenario: dict) -> dict:
    result = {name: None for name in REQUIRED_FIELDS}
    result["member_id"] = scenario["member_id"]
    result["member_name"] = scenario["member_name"]
    result["date_of_birth"] = scenario["date_of_birth"]
    result["case_reference"] = scenario["case_reference"]
    result["denial_reference"] = scenario["denial_reference"]
    for name in ARRAY_FIELDS:
        result[name] = []
    result["requested_units"] = _REQUESTED_UNITS.get(scenario["scenario"])
    result["good_cause_for_late_filing"] = False
    result["supporting_evidence"] = _SUPPORTING_EVIDENCE.get(scenario["scenario"], {})
    return result


def _fake_azure_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.choices[0].message.content = json.dumps(payload)
    return response


def _row(number: int, status: str, cite_id: str | None) -> dict:
    return {
        "number": number,
        "status": status,
        "rationale": f"criterion {number} is {status.lower()}",
        "cited_guidelines": [cite_id] if cite_id else [],
    }


def _criteria_for(policy_id: str) -> list:
    return sorted(
        (c for c in _ALL_POLICY_CHUNKS if c.policy_id == policy_id and c.kind == CRITERION),
        key=lambda c: c.number,
    )


def _context_for(policy_id: str) -> list:
    return [c for c in _ALL_POLICY_CHUNKS if c.policy_id == policy_id and c.kind in (DENIAL_REASONS, APPEAL_GUIDANCE)]


def _recorded_evaluation_result(policy_id: str, rows: list[dict], overall_rationale: str) -> dict:
    """A "recorded" evaluation_agent fixture: the real node function is run once
    against a scripted Azure response, so check_8_from_criteria's real compilation
    logic (not a hand-rolled decision/scenario) produces the fixture -- only the
    network call is faked.
    """
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_azure_response(
        {"criteria": rows, "overall_rationale": overall_rationale, "cited_guidelines": []}
    )
    return real_evaluation_agent(_criteria_for(policy_id), _context_for(policy_id), "redacted clinical evidence (recorded fixture)", client=client)


# Scenarios 1 and 2 are the only ones that reach check 8 in the manifest (see
# scenario_manifest.json's own notes -- scenario 7 resolves at check 7 directly).
_EVALUATION_FIXTURES = {
    1: _recorded_evaluation_result(
        "CP-CARD-009",
        [
            _row(1, "Met", "CP-CARD-009#c1"),
            _row(2, "Met", "CP-CARD-009#c2"),
            _row(3, "Met", "CP-CARD-009#c3"),
            _row(4, "Met", "CP-CARD-009#c4"),
            _row(5, "Met", "CP-CARD-009#c5"),
        ],
        "All CP-CARD-009 criteria are met; the previously-missing GDMT trial is now documented.",
    ),
    2: _recorded_evaluation_result(
        "CP-BARI-021",
        [
            _row(1, "Not met", "CP-BARI-021#c1"),
            _row(2, "Not met", "CP-BARI-021#c2"),
            _row(3, "Not documented", None),
            _row(4, "Met", "CP-BARI-021#c4"),
        ],
        "BMI and weight-management duration both fall short of CP-BARI-021's thresholds.",
    ),
}


def _scenario_number_from_audit(case: Case) -> int | None:
    for entry in reversed(case.audit_trail):
        match = re.search(r"\(scenario (\d+)\)", entry.action)
        if match:
            return int(match.group(1))
    return None


def _run_manifest_scenario(conn, scenario: dict) -> dict:
    number = scenario["scenario"]
    case_id = f"CASE-REG-{number:02d}"

    if number == 9:
        # Check 2 (duplicate/merge) only fires if an open case already exists for
        # this case_reference -- same stub-case setup as test_graph.py. Scenario 9's
        # case_reference (APL-2026-00312) is, by design, the same one EMAIL-001 /
        # MEDHIST-001 use below -- clean the stub up immediately after so it can't
        # shadow the later extraction-fixture runs for that same reference.
        existing = Case(case_id="CASE-REG-EXISTING-09", channel=Channel.EMAIL)
        existing.request.case_reference = scenario["case_reference"]
        create_case(conn, existing)

    with (
        patch("app.core.graph.intake_agent", return_value=_intake_fields(scenario)),
        patch("app.core.graph.evaluation_agent", return_value=_EVALUATION_FIXTURES.get(number)),
        patch("app.core.graph.summarization_agent", return_value=_MOCK_SUMMARY),
    ):
        case = pipeline_graph.run_pipeline(
            Case(case_id=case_id, channel=Channel.EMAIL),
            "irrelevant raw text (LLM mocked)",
            received_date=date.fromisoformat(scenario["date_received"]),
        )

    if number == 9:
        conn.execute("DELETE FROM cases WHERE case_id = %s", ("CASE-REG-EXISTING-09",))

    if number == 8:
        # Member can't be identified -- short-circuits at intake, before rules_engine
        # or finalize ever set a recommendation (see test_graph.py's own test of this).
        passed = case.status == CaseStatus.NEEDS_CLARIFICATION and case.recommendation is None
        expected_display = "Cannot process (hold) [status=Needs Clarification, no recommendation]"
        actual_display = f"status={case.status.value}, recommendation={case.recommendation}"
    else:
        actual_decision = case.recommendation.decision if case.recommendation else None
        actual_scenario_number = _scenario_number_from_audit(case)
        passed = actual_decision == scenario["outcome"] and actual_scenario_number == number
        expected_display = f"{scenario['outcome']} (scenario {number})"
        actual_display = f"{actual_decision} (scenario {actual_scenario_number})"

    return {
        "group": "scenario",
        "id": str(number),
        "name": scenario["name"],
        "expected": expected_display,
        "actual": actual_display,
        "passed": passed,
    }


_EXPECTED_EXTRACTION_FIELD_MAP = {
    "member_id": lambda case: case.member.member_id,
    "member_name": lambda case: case.member.name,
    "date_of_birth": lambda case: case.member.date_of_birth.isoformat() if case.member.date_of_birth else None,
    "case_reference": lambda case: case.request.case_reference,
    "denial_reference": lambda case: case.request.denial_reference,
    "requesting_provider": lambda case: case.provider.name,
    "sender_org": lambda case: case.provider.organization,
    "procedure_requested": lambda case: case.request.procedure,
    "diagnosis_codes": lambda case: case.diagnosis_codes,
}


def _mock_llm_response(expected_fields: dict) -> dict:
    result = {name: expected_fields.get(name) for name in REQUIRED_FIELDS}
    for name in ARRAY_FIELDS:
        result[name] = expected_fields.get(name) or []
    result["requested_units"] = None
    result["good_cause_for_late_filing"] = False
    result["supporting_evidence"] = {}
    return result


def _working_text_for(test_file: str) -> str:
    path = TEST_DATA_DIR / test_file
    if test_file.startswith("emails/"):
        parsed = email_connector.parse_eml(path.read_bytes())
        parts = [parsed.body_text]
        for attachment in parsed.attachments:
            if attachment.filename.lower().endswith(".pdf"):
                parts.append(ocr_service.extract_text_from_pdf(attachment.content))
            else:
                parts.append(attachment.content.decode("utf-8", errors="replace"))
        return "\n\n".join(parts)
    return ocr_service.extract_text_from_pdf(path.read_bytes())


def _run_extraction_case(conn, expected_case: dict, index: int) -> dict:
    test_file = expected_case["test_file"]
    expected_fields = expected_case["expected_fields"]
    working_text = _working_text_for(test_file)
    channel = Channel.EMAIL if test_file.startswith("emails/") else Channel.UPLOAD

    with (
        patch("app.core.graph.intake_agent", return_value=_mock_llm_response(expected_fields)),
        patch("app.core.graph.evaluation_agent", return_value=_EVALUATION_FIXTURES[1]),
        patch("app.core.graph.summarization_agent", return_value=_MOCK_SUMMARY),
    ):
        case = pipeline_graph.run_pipeline(
            Case(case_id=f"CASE-REG-EXTRACT-{index:02d}", channel=channel),
            working_text,
            received_date=date.today(),
        )

    mismatches = []
    for field_name, getter in _EXPECTED_EXTRACTION_FIELD_MAP.items():
        if field_name not in expected_fields:
            continue
        actual_value = getter(case)
        expected_value = expected_fields[field_name]
        if actual_value != expected_value:
            mismatches.append(f"{field_name}: expected {expected_value!r}, got {actual_value!r}")

    expected_status = expected_case["expect_status_after_intake"]
    actual_status = case.status.value
    status_ok = (
        actual_status == CaseStatus.NEEDS_CLARIFICATION.value
        if expected_status == CaseStatus.NEEDS_CLARIFICATION.value
        else actual_status != CaseStatus.NEEDS_CLARIFICATION.value
    )
    if not status_ok:
        mismatches.append(f"status: expected reachable from {expected_status!r}, got {actual_status!r}")

    return {
        "group": "extraction",
        "id": test_file,
        "name": expected_case["scenario"],
        "expected": f"fields match expected_extraction.json, status from {expected_status!r}",
        "actual": "match" if not mismatches else "; ".join(mismatches),
        "passed": not mismatches,
    }


def _print_regression_table(rows: list[dict]) -> None:
    header = f"{'group':<10} {'id':<45} {'result':<6} expected / actual"
    print("\n" + header)
    print("-" * len(header))
    for row in rows:
        result = "PASS" if row["passed"] else "FAIL"
        print(f"{row['group']:<10} {row['id']:<45} {result:<6} expected={row['expected']} actual={row['actual']}")
    passed_count = sum(r["passed"] for r in rows)
    print("-" * len(header))
    print(f"{passed_count}/{len(rows)} passed")


def test_full_regression_suite(db_conn):
    load_all(db_conn)

    rows = [_run_manifest_scenario(db_conn, MANIFEST[n]) for n in sorted(MANIFEST)]
    rows += [_run_extraction_case(db_conn, case, i) for i, case in enumerate(EXPECTED_EXTRACTION)]

    _print_regression_table(rows)

    failures = [r for r in rows if not r["passed"]]
    assert not failures, "regression failures:\n" + "\n".join(
        f"[{r['group']} {r['id']}] {r['name']}: expected {r['expected']!r}, got {r['actual']!r}" for r in failures
    )


@pytest.mark.skipif(
    not (os.environ.get("AZURE_OPENAI_ENDPOINT") and os.environ.get("AZURE_OPENAI_API_KEY")),
    reason="opt-in: requires real AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY",
)
def test_full_pipeline_live_run_thomas_reyes_late_filing(db_conn):
    """One opt-in live run through the full graph, per claude/PHASE_PROMPTS.md
    Phase 5. Thomas Reyes' appeal is late-filed, so check 4 is conclusive and
    evaluation_agent -- the one node that would also need a live ChromaDB -- is
    never reached; intake_agent and summarization_agent both make real Azure
    OpenAI calls.
    """
    load_all(db_conn)
    scenario = MANIFEST[6]
    working_text = (
        f"Appeal on behalf of member {scenario['member_id']} ({scenario['member_name']}, "
        f"DOB {scenario['date_of_birth']}). This concerns denial reference "
        f"{scenario['denial_reference']}, case reference {scenario['case_reference']}. "
        "We are requesting reconsideration of the denied knee arthroscopy "
        "(partial meniscectomy), CPT 29881, performed by Dr. Hana Osei."
    )

    case = pipeline_graph.run_pipeline(
        Case(case_id="CASE-REG-LIVE-06", channel=Channel.EMAIL),
        working_text,
        received_date=date.fromisoformat(scenario["date_received"]),
    )

    assert case.recommendation.decision == "Deny"
    assert "scenario 6" in case.audit_trail[-1].action
    assert case.clinical_summary
