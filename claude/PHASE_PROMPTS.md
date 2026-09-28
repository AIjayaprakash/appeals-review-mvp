# Phase Prompts for Claude Code

Run one phase per session. Commit after each phase passes its tests.
Tip: start each session with `Read CLAUDE.md, then do the task below.`

---

## Phase 0 — Skeleton

```
Set up the project skeleton per the layout in CLAUDE.md.
- pyproject.toml with FastAPI, uvicorn, pydantic, pytest, psycopg, chromadb
- docker-compose.yml with PostgreSQL and ChromaDB
- app/models.py: Pydantic models for Case, Denial, Benefits, Utilization,
  matching the schema in docs/ (all statuses from CLAUDE.md)
- A loader that reads reference_data/*.json into Postgres tables
- GET /health and GET /cases/{id} returning a stored Case
Acceptance: `docker compose up -d`, `pytest -q` passes, and I can create and read
back a bare Case row. No agents and no LLM calls yet.
```

## Phase 0.5 — Fill the test-data gaps (do before Phase 1)

```
Extend reference_data/ and test_data/ so every scenario has backing data.
Add records for: Robert Yeung (Scenario 2), Priya Anand (3), David Okafor (4),
Helena Brooks (5), Thomas Reyes (6), a duplicate-submission email pair (9), and an
already-upheld first appeal for Angela Ruiz (10). Use the examples in
docs/scenarios-operational-plan.pdf. Keep the same JSON structure as the existing
files. Add matching entries to a new scenario_manifest.json listing expected outcome
per case. Synthetic data only.
```

## Phase 1 — Rules engine (checks 1–7, no AI)

```
Implement app/core/decision_engine.py: checks 1-7 as pure functions, run in the
fixed order from CLAUDE.md, stopping at the first conclusive result. Input is a Case
plus looked-up denial/benefits/utilization; output is (outcome, scenario_number,
reason, missing_items). Add lookup_service.py for exact-match reads.
Write pytest tests first, one per scenario 3, 4, 5, 6, 7 (partial override), 8, 9, 10,
using scenario_manifest.json. Include the name+DOB fallback identity match and a test
that first-name-only does NOT match.
Acceptance: all tests pass; grep confirms no LLM/openai imports in decision_engine.py.
```

## Phase 2 — Intake Agent

```
Add ingestion + redaction + the Intake Agent.
- ocr_service.py (Azure AI Vision Read; a local text-extraction fallback for typed PDFs)
- security/redaction.py using Presidio: tokenize identifiers before any LLM call and
  re-hydrate only when assembling the Case for the reviewer
- app/core/nodes.py: intake_agent (GPT-4o-mini) returns structured fields, null for
  anything missing, never guesses
- POST /intake/upload and the .eml parser for POST /intake/email/poll (read from
  test_data/emails for now, no live mailbox)
Test against test_data/ using expected_extraction.json. EMAIL-002 must end at
Needs Clarification with member_id null. Mock the LLM in unit tests; add one opt-in
integration test that hits the real model.
```

## Phase 3 — Evaluation Agent (check 8)

```
Build policy retrieval and check 8.
- Chunk and embed reference_data/policy_bulletins/ into ChromaDB
- policy_retrieval_service.py: retrieve by denial.cited_policy_id plus a semantic query
  built from the case's clinical evidence
- evaluation_agent node (GPT-4o): check each numbered criterion as Met / Not met /
  Not documented, produce decision, rationale, cited_guidelines
- Enforce the citation rule: reject and retry any claim without a retrieved passage
- Only invoke it when checks 1-7 all pass
Validate on EMAIL-001/MEDHIST-001 (expect Approve, one item flagged), MEDHIST-002
(expect Needs more information), and the Yeung case (expect Deny, Scenario 2).
```

## Phase 4 — Summarization + reviewer flow

```
Add summarization_agent (GPT-4o-mini) and wire the full LangGraph in core/graph.py:
intake -> lookup -> rules_engine -> (evaluation_agent) with summarization in
parallel, then finalize. Add review endpoints (GET /cases, POST /cases/{id}/review,
GET /cases/{id}/audit), an audit trail entry on every status change, and a minimal
React reviewer view: queue, case detail with summary + recommendation + citations,
approve/edit/reject. Chat endpoint can be a stub.
```

## Phase 5 — Full regression

```
Create tests/test_regression.py that runs every case in scenario_manifest.json and
expected_extraction.json through the full graph (LLM mocked with recorded fixtures,
plus one opt-in live run). Assert the outcome and scenario number for all 10
scenarios, including every negative one. Print a pass/fail table. Fix any failures
without loosening a rule in CLAUDE.md.
```
