"""Unit tests for the evaluation_agent LLM node (check 8), with the Azure OpenAI
client mocked -- same pattern as test_nodes_intake_agent.py. Policy chunks come
from the real bulletins via policy_retrieval_service's pure parser (no ChromaDB or
network needed for these). The three "validate on" cases from claude/PHASE_PROMPTS.md
Phase 3 map onto scenario_manifest.json scenarios 1, 2, and 7.

One opt-in test at the bottom hits the real model and is skipped unless Azure
OpenAI credentials are configured.
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from app.core.nodes import (
    EVALUATION_MODEL_DEPLOYMENT,
    EvaluationError,
    IncompleteCriteriaError,
    UncitedClaimError,
    evaluation_agent,
)
from app.services.policy_retrieval_service import APPEAL_GUIDANCE, CRITERION, DENIAL_REASONS, load_policy_bulletin_chunks

_ALL_CHUNKS = load_policy_bulletin_chunks()


def _criteria(policy_id: str):
    return sorted(
        (c for c in _ALL_CHUNKS if c.policy_id == policy_id and c.kind == CRITERION),
        key=lambda c: c.number,
    )


def _context(policy_id: str):
    return [c for c in _ALL_CHUNKS if c.policy_id == policy_id and c.kind in (DENIAL_REASONS, APPEAL_GUIDANCE)]


def _fake_response(payload: dict) -> MagicMock:
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


def test_eleanor_whitfield_all_criteria_met_approves_scenario_1():
    # Scenario 1: the GDMT trial the original denial called missing is now
    # documented (utilization_history shows sacubitril/valsartan since 2026-05-11);
    # LVEF, QRS, NYHA, and life expectancy are all otherwise supported. All five
    # CP-CARD-009 criteria come back Met -- one (GDMT) flagged in its own rationale
    # as worth a reviewer's second look, but that doesn't change its status.
    criteria = _criteria("CP-CARD-009")
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {
            "criteria": [
                _row(1, "Met", "CP-CARD-009#c1"),
                _row(2, "Met", "CP-CARD-009#c2"),
                _row(3, "Met", "CP-CARD-009#c3"),
                {
                    "number": 4,
                    "status": "Met",
                    "rationale": (
                        "GDMT (sacubitril/valsartan) started 2026-05-11, documented through the "
                        "2026-08-05 request -- meets the 90-day window, though the reviewer may want "
                        "to confirm the follow-up visit was framed as a GDMT check."
                    ),
                    "cited_guidelines": ["CP-CARD-009#c4", "CP-CARD-009#denial-reasons"],
                },
                _row(5, "Met", "CP-CARD-009#c5"),
            ],
            "overall_rationale": "All CP-CARD-009 criteria are met; the previously-missing GDMT trial is now documented.",
            "cited_guidelines": ["CP-CARD-009#c1", "CP-CARD-009#c2", "CP-CARD-009#c3", "CP-CARD-009#c4", "CP-CARD-009#c5"],
        }
    )

    result = evaluation_agent(criteria, _context("CP-CARD-009"), "redacted clinical evidence text", client=client)

    assert result["decision"] == "Approve"
    assert result["scenario"] == 1
    assert result["missing_items"] == []
    assert "CP-CARD-009#c4" in result["cited_guidelines"]
    client.chat.completions.create.assert_called_once()
    assert client.chat.completions.create.call_args.kwargs["model"] == EVALUATION_MODEL_DEPLOYMENT


def test_robert_yeung_criteria_not_met_denies_scenario_2():
    # Scenario 2: BMI 34 (below the >=40, or >=35-with-comorbidity threshold) and
    # only 2 of the required 6 months of supervised weight management -- a genuine
    # clinical mismatch, not a documentation gap.
    criteria = _criteria("CP-BARI-021")
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {
            "criteria": [
                _row(1, "Not met", "CP-BARI-021#c1"),
                _row(2, "Not met", "CP-BARI-021#c2"),
                _row(3, "Not documented", None),
                _row(4, "Met", "CP-BARI-021#c4"),
            ],
            "overall_rationale": "BMI and weight-management duration both fall short of CP-BARI-021's thresholds.",
            "cited_guidelines": ["CP-BARI-021#c1", "CP-BARI-021#c2", "CP-BARI-021#c4"],
        }
    )

    result = evaluation_agent(criteria, _context("CP-BARI-021"), "redacted clinical evidence text", client=client)

    assert result["decision"] == "Deny"
    assert result["scenario"] == 2
    # A clear Not-met is conclusive even though criterion 3 is also undocumented.
    assert result["missing_items"] == []


def test_marcus_oyelaran_partial_evidence_needs_more_information_scenario_7():
    # Scenario 7: Oswestry score improving satisfies Section 3.1, but no bounded
    # goal, visit estimate, or adherence confirmation is documented.
    criteria = _criteria("CP-PT-002")
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {
            "criteria": [
                _row(1, "Met", "CP-PT-002#c1"),
                _row(2, "Not documented", None),
                _row(3, "Not documented", None),
                _row(4, "Not documented", None),
            ],
            "overall_rationale": "Objective improvement is documented, but no bounded goal, visit estimate, or adherence confirmation.",
            "cited_guidelines": ["CP-PT-002#c1"],
        }
    )

    result = evaluation_agent(criteria, _context("CP-PT-002"), "redacted clinical evidence text", client=client)

    assert result["decision"] == "Needs more information"
    assert result["scenario"] == 7
    assert set(result["missing_items"]) == {"criterion 2", "criterion 3", "criterion 4"}


def test_uncited_claim_is_rejected_and_retried_then_succeeds():
    criteria = _criteria("CP-OPHTH-005")
    client = MagicMock()
    bad_response = _fake_response({"criteria": [_row(1, "Met", None)]})  # "Met" with no citation
    good_response = _fake_response(
        {"criteria": [_row(1, "Not met", "CP-OPHTH-005#c1")], "overall_rationale": "no visual field test submitted"}
    )
    client.chat.completions.create.side_effect = [bad_response, good_response]

    result = evaluation_agent(criteria, [], "redacted clinical evidence text", client=client, max_attempts=2)

    assert result["decision"] == "Deny"
    assert client.chat.completions.create.call_count == 2


def test_citation_to_an_unretrieved_id_is_rejected():
    criteria = _criteria("CP-OPHTH-005")
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"criteria": [_row(1, "Met", "CP-MADE-UP#c99")]}
    )

    with pytest.raises(UncitedClaimError):
        evaluation_agent(criteria, [], "redacted clinical evidence text", client=client, max_attempts=1)


def test_missing_a_criterion_is_rejected():
    criteria = _criteria("CP-BARI-021")  # 4 criteria
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"criteria": [_row(1, "Met", "CP-BARI-021#c1"), _row(2, "Met", "CP-BARI-021#c2")]}
    )

    with pytest.raises(IncompleteCriteriaError):
        evaluation_agent(criteria, [], "redacted clinical evidence text", client=client, max_attempts=1)


def test_persistently_invalid_response_raises_after_max_attempts():
    criteria = _criteria("CP-OPHTH-005")
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response({"criteria": [_row(1, "Met", None)]})

    with pytest.raises(EvaluationError):
        evaluation_agent(criteria, [], "redacted clinical evidence text", client=client, max_attempts=2)
    assert client.chat.completions.create.call_count == 2


@pytest.mark.skipif(
    not (os.environ.get("AZURE_OPENAI_ENDPOINT") and os.environ.get("AZURE_OPENAI_API_KEY")),
    reason="opt-in: requires real AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY",
)
def test_evaluation_agent_real_azure_openai_call():
    criteria = _criteria("CP-CARD-009")
    context = _context("CP-CARD-009")
    valid_ids = {c.chunk_id for c in criteria} | {c.chunk_id for c in context}

    clinical_evidence_text = (
        "Echo on 2026-06-22 shows LVEF 32%. NYHA Class III symptoms at time of request. "
        "QRS 160ms with LBBB morphology. Sacubitril/valsartan (GDMT) initiated 2026-05-11, "
        "continued symptoms documented through follow-up. Life expectancy >1 year, good "
        "functional status per treating cardiologist."
    )

    result = evaluation_agent(criteria, context, clinical_evidence_text)

    assert result["decision"] in ("Approve", "Deny", "Needs more information")
    assert result["scenario"] in (1, 2, 7)
    assert result["cited_guidelines"]
    assert set(result["cited_guidelines"]) <= valid_ids
