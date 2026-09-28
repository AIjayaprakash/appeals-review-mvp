# Appeals & Grievances Review Copilot (MVP1)

Agentic AI system that evaluates member appeals of denied claims and drafts a
recommendation for a human reviewer. Stack: FastAPI, LangGraph, Azure OpenAI,
PostgreSQL, ChromaDB, Presidio, React (reviewer UI). Requirements live in `docs/`.

## Read first (only when relevant)
- `docs/appeals-grievances-technical-documentation.docx` — architecture, schema, API
- `docs/scenarios-operational-plan.pdf` — the 10 outcome scenarios
- `docs/agentic-implementation-plan.pdf` — decision logic and phases
- `reference_data/` — denials, eligibility_benefits, utilization_history, policy_bulletins/
- `test_data/` — synthetic .eml and PDF intake files + `expected_extraction.json`

## Core design rule
Do NOT make one big agent. Most steps are deterministic. Only three steps use an LLM:
Intake Agent (extract fields), Summarization Agent, Evaluation Agent (check 8 only).

## Decision order (fixed, stop at first conclusive check)
1. Member identified? No -> Scenario 8, Cannot process (hold)
2. Open case already exists for this reference? Yes -> Scenario 9, Duplicate (merge)
3. Prior internal appeal on this denial already upheld? Yes -> Scenario 10, Escalate
4. Filed within appeal window (no good cause)? No -> Scenario 6, Deny, late
5. Coverage active on date of service? No -> Scenario 5, Deny, not eligible
6. Service is a covered benefit (or exception met)? No -> Scenario 3, Deny, not covered
7. Within annual limit? No + no override evidence -> Scenario 4, Deny
   No + partial override evidence -> Scenario 7, Needs more information
8. (LLM) Meets every clinical policy criterion?
   clearly not met -> Scenario 2, Deny | one identifiable gap -> Scenario 7 | met -> Scenario 1, Approve
Checks 1-7 are pure functions with no LLM calls. Never reorder them.

## Data rules
- Structured reference data (denials, eligibility, utilization) = exact-match lookups
  by member_id / denial_reference. Never embed these in the vector store.
- Only `policy_bulletins/` goes into ChromaDB, retrieved by cited_policy_id + semantic query.
- The `Case` record is the single shared state. Statuses: New, Extracted, Redacted,
  Structured, Needs Clarification, Summarized, Drafted, Reviewed, Closed.

## Hard rules
- Every document passes through redaction before any LLM call or log line.
- Agents never invent a missing field: return null and route to Needs Clarification.
- Never guess identity from a first name; require member_id, or full name + DOB.
- Every claim in a recommendation must cite a retrieved policy passage.
- A recommendation is a draft. Only a human reviewer closes a case.
- Synthetic data only. No real member data, no secrets committed. Use `.env` (gitignored).

## Don't
- Don't call an LLM from `decision_engine.py`.
- Don't change the `Case` schema without updating `models.py`, tests, and docs together.
- Don't log raw OCR text or email bodies above DEBUG.
- Don't add auth-less endpoints to anything beyond local dev (reviewer UI has no auth in MVP1).

## Layout
```
app/api/        intake.py cases.py review.py chat.py
app/core/       graph.py nodes.py decision_engine.py
app/security/   redaction.py audit.py
app/services/   ocr_service.py lookup_service.py policy_retrieval_service.py email_connector.py
app/models.py   app/main.py
reference_data/ test_data/ tests/ docs/
```

## Commands
- `docker compose up -d` — Postgres + ChromaDB
- `uv pip install -e '.[dev]'` — install
- `python -m spacy download en_core_web_sm` — one-time, needed by Presidio for redaction (app/security/redaction.py)
- `pytest -q` — run tests (must pass before finishing any task)
- `uvicorn app.main:app --reload` — run API

## Working style
- One phase per session. Finish with passing tests, then stop and summarise.
- Write the test first for each scenario; each scenario in `docs/` needs a test.
- Keep changes small; explain what you changed and why in the summary.
- When corrected on a mistake, propose a one-line rule to add to this file.
