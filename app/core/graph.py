"""Compiles the full appeals pipeline as a LangGraph.

Node order: intake -> lookup -> rules_engine -> (evaluation_agent, only when
checks 1-7 all pass) with summarization running in parallel, then finalize. Per
CLAUDE.md, app/models.py's Case is the single shared state -- GraphState just
wraps the in-flight Case plus the intermediate values each node needs. This
module owns no decision logic of its own beyond routing between nodes; see
app/core/decision_engine.py for checks 1-8's rules and app/core/nodes.py for what
each LLM-backed node does and doesn't invent.

The evaluation and summarization branches reconverge on finalize. LangGraph only
runs a fan-in node once all of its *concurrently scheduled* predecessors have
completed in the same superstep -- if the two branches reach finalize after a
different number of hops, finalize runs once per hop count (first with partial
state, then again once complete). skip_evaluation and summarization_done exist
solely to keep both branches the same length so finalize runs exactly once;
verified empirically against langgraph 1.0 before relying on it here.
"""

from __future__ import annotations

from datetime import date
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app import db
from app.core.decision_engine import PROCEED_TO_EVALUATION, AppealFacts, DecisionResult, evaluate
from app.core.nodes import evaluation_agent, intake_agent, summarization_agent
from app.models import Case, CaseStatus, Member, Provider, Recommendation
from app.security.audit import transition_status
from app.security.redaction import redact, rehydrate
from app.services import lookup_service, policy_retrieval_service

REQUIRED_FIELDS = [
    "member_id",
    "member_name",
    "date_of_birth",
    "case_reference",
    "denial_reference",
    "requesting_provider",
    "sender_org",
    "procedure_requested",
]
ARRAY_FIELDS = ["diagnosis_codes", "attachments"]


class GraphState(TypedDict, total=False):
    case: Case
    working_text: str
    received_date: date
    redacted_clinical_text: str
    token_map: dict[str, str]
    resolved_member_id: str | None
    is_duplicate: bool
    requested_units: int | None
    good_cause_for_late_filing: bool
    supporting_evidence: dict
    denial: dict | None
    eligibility: dict | None
    utilization: dict | None
    facts: AppealFacts
    decision: DecisionResult
    evaluation: dict | None
    summary: str | None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _rehydrate_extraction(extraction: dict, token_map: dict[str, str]) -> dict:
    rehydrated: dict = {}
    for key, value in extraction.items():
        if isinstance(value, str):
            rehydrated[key] = rehydrate(value, token_map)
        elif isinstance(value, list):
            rehydrated[key] = [rehydrate(v, token_map) if isinstance(v, str) else v for v in value]
        else:
            rehydrated[key] = value
    return rehydrated


def intake_node(state: GraphState) -> dict:
    case = state["case"]
    redaction_result = redact(state["working_text"])
    extraction = intake_agent(redaction_result.redacted_text)
    fields = _rehydrate_extraction(extraction, redaction_result.token_map)
    date_of_birth = _parse_date(fields.get("date_of_birth"))

    case.member = Member(member_id=fields.get("member_id"), name=fields.get("member_name"), date_of_birth=date_of_birth)
    case.provider = Provider(name=fields.get("requesting_provider"), organization=fields.get("sender_org"))
    case.request.procedure = fields.get("procedure_requested")
    case.request.denial_reference = fields.get("denial_reference")
    case.request.case_reference = fields.get("case_reference")
    case.diagnosis_codes = fields.get("diagnosis_codes") or []

    sufficient = lookup_service.has_sufficient_identity(fields.get("member_id"), fields.get("member_name"), date_of_birth)
    if sufficient:
        transition_status(case, CaseStatus.STRUCTURED, actor="intake_agent", action="Extracted intake fields")
    else:
        transition_status(
            case,
            CaseStatus.NEEDS_CLARIFICATION,
            actor="intake_agent",
            action="Extracted intake fields; insufficient identity data to proceed (member_id, or full name and date of birth, required)",
        )

    return {
        "case": case,
        "redacted_clinical_text": redaction_result.redacted_text,
        "token_map": redaction_result.token_map,
        "requested_units": fields.get("requested_units"),
        "good_cause_for_late_filing": bool(fields.get("good_cause_for_late_filing")),
        "supporting_evidence": fields.get("supporting_evidence") or {},
    }


def _route_after_intake(state: GraphState) -> str:
    return "finalize" if state["case"].status == CaseStatus.NEEDS_CLARIFICATION else "lookup"


def lookup_node(state: GraphState) -> dict:
    case = state["case"]
    conn = db.get_connection()
    try:
        resolved_member_id = lookup_service.resolve_member_id(
            conn, case.member.member_id, case.member.name, case.member.date_of_birth
        )
        denial = (
            lookup_service.get_denial(conn, case.request.denial_reference)
            if case.request.denial_reference
            else None
        )
        eligibility = lookup_service.get_eligibility(conn, resolved_member_id) if resolved_member_id else None
        utilization = lookup_service.get_utilization(conn, resolved_member_id) if resolved_member_id else None
        is_duplicate = lookup_service.find_open_case_by_reference(conn, case.request.case_reference) is not None
    finally:
        conn.close()

    facts = AppealFacts(
        member_id=resolved_member_id,
        member_name=case.member.name,
        date_of_birth=case.member.date_of_birth,
        case_reference=case.request.case_reference,
        denial_reference=case.request.denial_reference,
        date_received=state.get("received_date") or date.today(),
        requested_units=state.get("requested_units"),
        supporting_evidence=state.get("supporting_evidence") or {},
        good_cause_for_late_filing=bool(state.get("good_cause_for_late_filing")),
    )

    return {
        "resolved_member_id": resolved_member_id,
        "denial": denial,
        "eligibility": eligibility,
        "utilization": utilization,
        "is_duplicate": is_duplicate,
        "facts": facts,
    }


def rules_engine_node(state: GraphState) -> dict:
    result = evaluate(
        state["facts"],
        resolved_member_id=state["resolved_member_id"],
        is_duplicate=state["is_duplicate"],
        denial=state["denial"],
        eligibility=state["eligibility"],
        utilization=state["utilization"],
    )
    return {"decision": result}


def _route_after_rules_engine(state: GraphState) -> str:
    return "evaluate" if state["decision"].outcome == PROCEED_TO_EVALUATION else "skip"


def evaluation_node(state: GraphState) -> dict:
    denial = state["denial"] or {}
    policy_id = denial.get("cited_policy_id")
    if not policy_id:
        return {
            "evaluation": {
                "decision": "Needs more information",
                "scenario": 7,
                "overall_rationale": "No cited policy on the denial to evaluate clinical criteria against.",
                "cited_guidelines": [],
                "missing_items": ["cited_policy_id"],
            }
        }

    client = policy_retrieval_service.get_chroma_client()
    criteria = policy_retrieval_service.get_criteria(client, policy_id)
    semantic_query = policy_retrieval_service.build_semantic_query(denial, state["redacted_clinical_text"])
    context = policy_retrieval_service.retrieve_context(client, policy_id, semantic_query)
    result = evaluation_agent(criteria, context, state["redacted_clinical_text"])
    return {"evaluation": result}


def skip_evaluation_node(state: GraphState) -> dict:
    return {}


def summarization_node(state: GraphState) -> dict:
    summary = summarization_agent(state["redacted_clinical_text"])
    return {"summary": rehydrate(summary, state["token_map"])}


def summarization_done_node(state: GraphState) -> dict:
    return {}


def finalize_node(state: GraphState) -> dict:
    case = state["case"]
    if case.status == CaseStatus.NEEDS_CLARIFICATION:
        return {"case": case}

    decision = state["decision"]
    evaluation = state.get("evaluation")

    if evaluation is not None:
        recommendation = Recommendation(
            decision=evaluation["decision"],
            rationale=evaluation.get("overall_rationale") or decision.reason,
            cited_guidelines=evaluation.get("cited_guidelines") or [],
        )
        scenario = evaluation["scenario"]
    else:
        recommendation = Recommendation(decision=decision.outcome, rationale=decision.reason, cited_guidelines=[])
        scenario = decision.scenario

    case.recommendation = recommendation
    case.clinical_summary = state.get("summary")

    transition_status(case, CaseStatus.SUMMARIZED, actor="graph", action="Clinical summary and decision compiled")
    final_status = CaseStatus.NEEDS_CLARIFICATION if scenario == 8 else CaseStatus.DRAFTED
    transition_status(
        case,
        final_status,
        actor="graph",
        action=f"Recommendation drafted: {recommendation.decision} (scenario {scenario})",
    )
    return {"case": case}


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("intake", intake_node)
    graph.add_node("lookup", lookup_node)
    graph.add_node("rules_engine", rules_engine_node)
    graph.add_node("evaluation_agent", evaluation_node)
    graph.add_node("skip_evaluation", skip_evaluation_node)
    graph.add_node("summarization", summarization_node)
    graph.add_node("summarization_done", summarization_done_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("intake")
    graph.add_conditional_edges("intake", _route_after_intake, {"lookup": "lookup", "finalize": "finalize"})
    graph.add_edge("lookup", "rules_engine")
    graph.add_edge("lookup", "summarization")
    graph.add_conditional_edges(
        "rules_engine", _route_after_rules_engine, {"evaluate": "evaluation_agent", "skip": "skip_evaluation"}
    )
    graph.add_edge("evaluation_agent", "finalize")
    graph.add_edge("skip_evaluation", "finalize")
    graph.add_edge("summarization", "summarization_done")
    graph.add_edge("summarization_done", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


_compiled_graph = None


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def run_pipeline(case: Case, working_text: str, *, received_date: date | None = None) -> Case:
    compiled = get_compiled_graph()
    result = compiled.invoke({"case": case, "working_text": working_text, "received_date": received_date or date.today()})
    return result["case"]
