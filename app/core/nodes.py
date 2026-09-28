"""LangGraph nodes: intake_agent, summarization_agent, evaluation_agent.

Phase 2 implements intake_agent; Phase 3 adds evaluation_agent (check 8). Both
receive already-redacted text (see app/security/redaction.py) -- redaction tokens
(e.g. [[PERSON_1]]) must be echoed back verbatim wherever they appear in a
response, never decoded; the caller re-hydrates them afterwards. Neither node
invents a value that isn't in its input: intake_agent returns null for anything
not explicitly present in the source text, and evaluation_agent returns "Not
documented" (never guesses "Met") for any criterion the evidence doesn't address.

evaluation_agent's own decision/scenario is compiled by the pure
decision_engine.check_8_from_criteria() from the per-criterion judgments below --
this file makes the LLM call, decision_engine.py never does.
"""

from __future__ import annotations

import json
import os

from openai import AzureOpenAI

from app.core.decision_engine import CriterionResult, check_8_from_criteria
from app.services.policy_retrieval_service import PolicyPassage

AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
INTAKE_MODEL_DEPLOYMENT = os.environ.get("AZURE_OPENAI_INTAKE_DEPLOYMENT", "gpt-4o-mini")
EVALUATION_MODEL_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EVALUATION_DEPLOYMENT", "gpt-4o")
VALID_CRITERION_STATUSES = ("Met", "Not met", "Not documented")

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

_SYSTEM_PROMPT = """You are the Intake Agent for a health-plan appeals review system.

You will be given REDACTED text extracted from an appeal email and/or its
attachments. Some identifiers have been replaced with opaque tokens that look like
[[PERSON_1]], [[MEMBER_ID_1]], [[DATE_TIME_1]], [[DENIAL_REFERENCE_1]], or
[[CASE_REFERENCE_1]]. If a field's value in the source text is one of these tokens,
return that exact token string verbatim as the field's value. Never decode, guess,
or invent what a token stands for.

Extract exactly these fields as a JSON object:
- member_id (string or null)
- member_name (string or null)
- date_of_birth (string or null, normalized to YYYY-MM-DD -- the source may write it
  as MM/DD/YYYY or in words; convert it, but only if the source clearly states a
  full date. If date_of_birth is a redaction token, return the token unchanged and
  do not attempt to reformat it.)
- case_reference (string or null)
- denial_reference (string or null)
- requesting_provider (string or null)
- sender_org (string or null)
- procedure_requested (string or null)
- diagnosis_codes (array of strings, [] if none are stated)
- attachments (array of filenames, [] if none)

If a field is not explicitly present in the text, return null for it (or [] for
the two array fields). Never guess or infer a value that is not stated."""


class IntakeExtractionError(RuntimeError):
    pass


def get_client() -> AzureOpenAI:
    if not (AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY):
        raise IntakeExtractionError(
            "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set to call the Intake Agent."
        )
    return AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )


def intake_agent(redacted_text: str, *, client: AzureOpenAI | None = None) -> dict:
    """Extracts structured fields from redacted intake text via GPT-4o-mini.

    Returns a dict with REQUIRED_FIELDS (str | None each) and ARRAY_FIELDS (list
    each). String values may still be redaction tokens -- the caller re-hydrates.
    """
    client = client or get_client()

    response = client.chat.completions.create(
        model=INTAKE_MODEL_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": redacted_text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    data = json.loads(response.choices[0].message.content)

    result = {name: data.get(name) for name in REQUIRED_FIELDS}
    for name in ARRAY_FIELDS:
        result[name] = data.get(name) or []
    return result


_EVALUATION_SYSTEM_PROMPT = """You are the Evaluation Agent for a health-plan appeals review system.

You check one appeal against one clinical policy's numbered coverage criteria. You
will be given, in the user message:
1. Every numbered criterion for this policy, each preceded by its citation id in
   brackets, e.g. "[CP-CARD-009#c1] Criterion 1: ...". You must assess every one --
   never skip a criterion and never assess one that wasn't given to you.
2. Zero or more additional retrieved context passages (denial reasons, appeal
   guidance), each with its own bracketed citation id, e.g. "[CP-CARD-009#appeal-guidance]".
3. REDACTED clinical evidence text for this specific appeal. Some identifiers are
   opaque tokens like [[PERSON_1]] or [[MEMBER_ID_1]] -- ignore them, they carry no
   clinical meaning.

For each numbered criterion, decide, using only the clinical evidence given:
- "Met" -- the evidence affirmatively satisfies the criterion.
- "Not met" -- the evidence contradicts the criterion (e.g. a value below a
  required threshold).
- "Not documented" -- the evidence is simply silent on this criterion. Use this,
  never "Met", whenever something is not stated -- do not infer or assume.

Every "Met" or "Not met" row must cite the criterion's own bracketed id, and may
add a second citation from the retrieved context if it supports the judgment.
Never write a citation id that was not given to you in this message, and never
assert a claim without at least one citation drawn from the given ids. A "Not
documented" row does not need a citation, since there is nothing to cite.

Respond with a JSON object of this exact shape:
{
  "criteria": [
    {"number": 1, "status": "Met", "rationale": "...", "cited_guidelines": ["CP-CARD-009#c1"]},
    ...
  ],
  "overall_rationale": "one to three sentences summarizing the clinical picture",
  "cited_guidelines": ["every citation id used anywhere above, deduplicated"]
}"""


class EvaluationError(RuntimeError):
    pass


class UncitedClaimError(EvaluationError):
    """A "Met" or "Not met" row cited an id that wasn't retrieved, or cited none."""


class IncompleteCriteriaError(EvaluationError):
    """The response didn't cover exactly the numbered criteria it was given."""


def _format_passage(passage: PolicyPassage) -> str:
    label = f"Criterion {passage.number}" if passage.kind == "criterion" else passage.kind.replace("_", " ")
    return f"[{passage.chunk_id}] {label}: {passage.text}"


def _build_evaluation_user_message(
    criteria: list[PolicyPassage], context_passages: list[PolicyPassage], clinical_evidence_text: str
) -> str:
    lines = ["Numbered coverage criteria (assess every one):"]
    lines.extend(_format_passage(c) for c in criteria)

    if context_passages:
        lines.append("\nAdditional retrieved context:")
        lines.extend(_format_passage(p) for p in context_passages)

    lines.append("\nRedacted clinical evidence for this appeal:\n" + clinical_evidence_text)
    return "\n".join(lines)


def _validate_evaluation_response(data: dict, criteria: list[PolicyPassage], valid_ids: set[str]) -> list[CriterionResult]:
    expected_numbers = {c.number for c in criteria}
    rows = data.get("criteria") or []
    actual_numbers = [row.get("number") for row in rows]
    if set(actual_numbers) != expected_numbers or len(set(actual_numbers)) != len(rows):
        raise IncompleteCriteriaError(
            f"expected exactly one assessment per criterion {sorted(expected_numbers)}, got {sorted(n for n in actual_numbers if n is not None)}"
        )

    results = []
    for row in rows:
        status = row.get("status")
        if status not in VALID_CRITERION_STATUSES:
            raise UncitedClaimError(f"criterion {row.get('number')}: invalid status {status!r}")

        cited = row.get("cited_guidelines") or []
        if status in ("Met", "Not met"):
            if not cited:
                raise UncitedClaimError(f"criterion {row.get('number')} ({status}) has no cited_guidelines")
            invalid = [cid for cid in cited if cid not in valid_ids]
            if invalid:
                raise UncitedClaimError(f"criterion {row.get('number')} cited unknown id(s): {invalid}")

        results.append(CriterionResult(number=row["number"], status=status))

    return sorted(results, key=lambda r: r.number)


def evaluation_agent(
    criteria: list[PolicyPassage],
    context_passages: list[PolicyPassage],
    clinical_evidence_text: str,
    *,
    client: AzureOpenAI | None = None,
    max_attempts: int = 2,
) -> dict:
    """Checks every numbered criterion (check 8) via GPT-4o and compiles the result.

    `criteria` and `context_passages` come from
    app/services/policy_retrieval_service.py (get_criteria() and retrieve_context()
    respectively) -- this function makes no ChromaDB calls itself. Returns a dict
    with the per-criterion assessments plus the compiled "decision"/"scenario" from
    decision_engine.check_8_from_criteria(), so this node's output already matches
    the shape app/models.py's Recommendation needs (decision, rationale,
    cited_guidelines).

    Any response that skips a criterion, or asserts "Met"/"Not met" without citing
    one of the ids actually retrieved, is rejected and the model is asked to
    correct it, up to max_attempts total; a claim without a citation is never
    passed through, per CLAUDE.md's citation rule.
    """
    client = client or get_client()
    valid_ids = {c.chunk_id for c in criteria} | {p.chunk_id for p in context_passages}
    messages = [
        {"role": "system", "content": _EVALUATION_SYSTEM_PROMPT},
        {"role": "user", "content": _build_evaluation_user_message(criteria, context_passages, clinical_evidence_text)},
    ]

    last_error: EvaluationError | None = None
    for _ in range(max_attempts):
        response = client.chat.completions.create(
            model=EVALUATION_MODEL_DEPLOYMENT,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw_content = response.choices[0].message.content
        data = json.loads(raw_content)
        try:
            criterion_results = _validate_evaluation_response(data, criteria, valid_ids)
        except EvaluationError as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": raw_content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Rejected: {exc}. Assess every criterion listed above exactly once, and cite only the "
                        "bracketed ids given to you. Return corrected JSON in the same shape."
                    ),
                }
            )
            continue

        decision_result = check_8_from_criteria(criterion_results)
        cited_guidelines = sorted({cid for row in data["criteria"] for cid in (row.get("cited_guidelines") or [])})
        return {
            "criteria": data["criteria"],
            "overall_rationale": data.get("overall_rationale"),
            "cited_guidelines": cited_guidelines,
            "decision": decision_result.outcome,
            "scenario": decision_result.scenario,
            "reason": decision_result.reason,
            "missing_items": decision_result.missing_items,
        }

    raise last_error
