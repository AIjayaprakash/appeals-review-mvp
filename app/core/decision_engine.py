"""Checks 1-7: deterministic decision rules, run in the fixed order from CLAUDE.md,
stopping at the first conclusive result. Pure functions only -- no LLM calls, no
database access. Every fact these checks need (identity resolution, denial /
eligibility / utilization records, any open-case duplicate match) must already have
been fetched by app/services/lookup_service.py before evaluate() is called.

Outcome strings match CLAUDE.md's decision order exactly, not the shorter labels in
docs/scenarios-operational-plan.pdf.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

PROCEED_TO_EVALUATION = "Proceed to clinical evaluation"

_OVERRIDE_EVIDENCE_KEYS = (
    "functional_outcome_measure",
    "bounded_goal_stated",
    "visit_estimate_provided",
    "no_adherence_gap",
)


@dataclass
class AppealFacts:
    """The facts about the appeal being evaluated that checks 1-7 need.

    Distinct from app.models.Case: only case_reference is persisted on Case (needed
    so a later duplicate submission can be matched against it). The rest are
    evaluation-time facts a real system would get from the Intake Agent (Phase 2);
    Phase 1 tests supply them directly.
    """

    member_id: str | None = None
    member_name: str | None = None
    date_of_birth: date | None = None
    case_reference: str | None = None
    denial_reference: str | None = None
    date_received: date | None = None
    requested_units: int | None = None
    supporting_evidence: dict = field(default_factory=dict)
    good_cause_for_late_filing: bool = False


@dataclass
class DecisionResult:
    outcome: str
    scenario: int | None
    triggering_check: int
    reason: str
    missing_items: list[str] = field(default_factory=list)


def evaluate(
    facts: AppealFacts,
    *,
    resolved_member_id: str | None,
    is_duplicate: bool,
    denial: dict | None,
    eligibility: dict | None,
    utilization: dict | None,
) -> DecisionResult:
    for check in (
        lambda: _check_1_identified(resolved_member_id),
        lambda: _check_2_duplicate(is_duplicate),
        lambda: _check_3_prior_appeal_upheld(facts, utilization),
        lambda: _check_4_filed_on_time(facts, denial),
        lambda: _check_5_eligible_on_date_of_service(denial, eligibility),
        lambda: _check_6_covered_benefit(facts, eligibility),
        lambda: _check_7_within_annual_limit(facts, eligibility),
    ):
        result = check()
        if result is not None:
            return result

    return DecisionResult(
        outcome=PROCEED_TO_EVALUATION,
        scenario=None,
        triggering_check=8,
        reason="All deterministic checks (1-7) passed; clinical policy criteria (check 8) must still be evaluated.",
    )


def _parse_date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _check_1_identified(resolved_member_id: str | None) -> DecisionResult | None:
    if resolved_member_id is not None:
        return None
    return DecisionResult(
        outcome="Cannot process (hold)",
        scenario=8,
        triggering_check=1,
        reason="Not enough information to safely identify the member -- a member_id, or a full name plus date of birth, is required.",
        missing_items=["member_id, or full name and date of birth"],
    )


def _check_2_duplicate(is_duplicate: bool) -> DecisionResult | None:
    if not is_duplicate:
        return None
    return DecisionResult(
        outcome="Duplicate (merge)",
        scenario=9,
        triggering_check=2,
        reason="An open case already exists for this case reference; merge into it rather than evaluating separately.",
    )


def _check_3_prior_appeal_upheld(facts: AppealFacts, utilization: dict | None) -> DecisionResult | None:
    if utilization is None:
        return None
    for entry in utilization.get("history", []):
        if (
            entry.get("type") == "appeal"
            and entry.get("outcome") == "upheld"
            and entry.get("denial_reference") == facts.denial_reference
        ):
            return DecisionResult(
                outcome="Escalate",
                scenario=10,
                triggering_check=3,
                reason=(
                    f"A prior internal appeal on denial {facts.denial_reference} was "
                    f"already upheld on {entry.get('date')}; route to external review "
                    "instead of a repeat internal look."
                ),
            )
    return None


def _check_4_filed_on_time(facts: AppealFacts, denial: dict | None) -> DecisionResult | None:
    if denial is None or facts.date_received is None:
        return None
    deadline_raw = denial.get("appeal_deadline")
    if deadline_raw is None:
        return None

    deadline = _parse_date(deadline_raw)
    if facts.date_received <= deadline or facts.good_cause_for_late_filing:
        return None

    return DecisionResult(
        outcome="Deny",
        scenario=6,
        triggering_check=4,
        reason=(
            f"Appeal received {facts.date_received.isoformat()}, after the "
            f"{deadline.isoformat()} filing deadline, with no good-cause reason provided."
        ),
    )


def _check_5_eligible_on_date_of_service(
    denial: dict | None, eligibility: dict | None
) -> DecisionResult | None:
    if denial is None or eligibility is None:
        return None

    date_of_service_raw = denial.get("date_of_service") or denial.get("date_denied")
    if date_of_service_raw is None:
        return None
    date_of_service = _parse_date(date_of_service_raw)

    effective_date_raw = eligibility.get("effective_date")
    if effective_date_raw is not None and date_of_service < _parse_date(effective_date_raw):
        return DecisionResult(
            outcome="Deny",
            scenario=5,
            triggering_check=5,
            reason=f"Coverage was not yet effective on the date of service ({date_of_service.isoformat()}).",
        )

    for gap in eligibility.get("coverage_gaps", []):
        start = _parse_date(gap["start_date"])
        end = _parse_date(gap["end_date"])
        if start <= date_of_service <= end:
            return DecisionResult(
                outcome="Deny",
                scenario=5,
                triggering_check=5,
                reason=(
                    f"Coverage lapsed from {gap['start_date']} to {gap['end_date']} "
                    f"({gap.get('reason', 'reason not specified')}); the date of "
                    f"service ({date_of_service.isoformat()}) falls within that gap."
                ),
            )

    return None


def _check_6_covered_benefit(facts: AppealFacts, eligibility: dict | None) -> DecisionResult | None:
    if eligibility is None:
        return None

    benefit = eligibility.get("relevant_benefit", {})
    exclusion = benefit.get("coverage_exclusion")
    if not exclusion:
        return None

    # Only known exception modeled so far: a visual field test showing >=30%
    # obstruction overrides the cosmetic exclusion (CP-OPHTH-005). New exclusion
    # types get their own named exception check here, not a generic rule.
    obstruction_pct = facts.supporting_evidence.get("visual_field_obstruction_pct")
    if obstruction_pct is not None and obstruction_pct >= 30:
        return None

    return DecisionResult(
        outcome="Deny",
        scenario=3,
        triggering_check=6,
        reason=(
            f"{benefit.get('category', 'This service')} is excluded unless a "
            f"specific condition is documented: {exclusion}. No qualifying evidence "
            "was submitted."
        ),
        missing_items=[exclusion],
    )


def _check_7_within_annual_limit(facts: AppealFacts, eligibility: dict | None) -> DecisionResult | None:
    if eligibility is None:
        return None

    benefit = eligibility.get("relevant_benefit", {})
    annual_limit = benefit.get("annual_limit")
    if annual_limit is None:
        return None

    units_used = benefit.get("units_used_ytd") or 0
    requested = facts.requested_units or 0
    projected_total = units_used + requested
    if projected_total <= annual_limit:
        return None

    evidence = facts.supporting_evidence
    met = [key for key in _OVERRIDE_EVIDENCE_KEYS if evidence.get(key)]
    missing = [key for key in _OVERRIDE_EVIDENCE_KEYS if not evidence.get(key)]

    if not met:
        return DecisionResult(
            outcome="Deny",
            scenario=4,
            triggering_check=7,
            reason=(
                f"Request ({requested} additional units) would bring year-to-date "
                f"usage to {projected_total}, exceeding the annual limit of "
                f"{annual_limit}, and no override evidence was submitted."
            ),
            missing_items=missing,
        )

    if not missing:
        # Full override evidence satisfies the exception -- not conclusive, proceed.
        return None

    return DecisionResult(
        outcome="Needs more information",
        scenario=7,
        triggering_check=7,
        reason=(
            f"Request would exceed the annual limit of {annual_limit} by "
            f"{projected_total - annual_limit}; some override evidence is "
            "documented, but not all of it."
        ),
        missing_items=missing,
    )
