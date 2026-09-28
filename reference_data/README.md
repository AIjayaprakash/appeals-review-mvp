# Evaluation Reference Data — MVP1

Mock "payer-side" data the Recommendation Agent needs to actually judge the six intake
test cases, not just extract them. All IDs and records are synthetic and keyed to
match the `intake-test-data` package exactly (`emails/`, `medical_history/`,
`expected_extraction.json`).

## Where each file goes — two different stores, on purpose

**Structured store (Postgres, or these JSON files as a stand-in for MVP1)** — needs
exact-match lookups by `member_id`, not semantic search:

```
denials.json               keyed by denial_reference (and member_id where known)
eligibility_benefits.json  keyed by member_id
utilization_history.json   keyed by member_id
```

**Vector store (ChromaDB)** — needs semantic retrieval, since the agent doesn't know
in advance which sentence of a policy bulletin is relevant:

```
policy_bulletins/CP-CARD-009_crt-d-criteria.md
policy_bulletins/CP-PT-002_outpatient-pt-criteria.md
policy_bulletins/CP-ONC-014_her2-second-line-criteria.md
```

Don't embed the three structured JSON files into the vector store — a similarity
search over "26 of 30 visits used" is the wrong tool for a number that needs to be
retrieved exactly. Chunk and embed only the three `.md` bulletins.

## How this plugs into the pipeline

The Intake Agent already extracts `member_id` (or fails to — see below). Once a case
has a `member_id`, three deterministic lookups run before the Recommendation Agent
does anything: `denials[member_id]`, `eligibility_benefits[member_id]`,
`utilization_history[member_id]`. Only the policy bulletin retrieval is a RAG call —
everything else is a keyed read.

This is also the concrete argument for extending the `Case` schema (from the
technical design doc) with a `denial` object and a `benefits` object alongside the
existing `recommendation` object, populated from these lookups rather than invented
by an LLM.

## `evaluation_manifest.json`

Maps each of the six intake test files to the lookups above, whether each lookup
should succeed, and a short note on what the Recommendation Agent's reasoning should
look like for that case. Use it as ground truth the same way `expected_extraction.json`
is used for the Intake Agent — except this one validates the *evaluation* step, not
extraction.

Two cases are the important negative tests:

- **`EMAIL-002`** — the intake email genuinely doesn't contain enough to identify a
  member. No lookup should succeed, and the case should stay at `Needs Clarification`.
  A matching payer record (`MBR-509912`) does exist in `eligibility_benefits.json` —
  it's there to prove a point: the data exists, but the pipeline must not guess its
  way to it from a first name alone.
- **`MEDHIST-002`** — the opposite case. `member_id` is still missing, but the letter
  gives a full name and DOB, which *is* enough for a safe identity match. This is the
  fallback path worth building deliberately (name+DOB lookup) rather than only
  supporting the happy path where `member_id` is already present.

## What's still missing for a real evaluation

This set covers denial reason, eligibility/benefit caps, utilization history, and the
matching clinical policy — the four sources discussed as primary. It does **not**
include provider network/credentialing data or ICD-10↔CPT necessity cross-reference
tables, which were named as lower-tier sources; add those the same way (structured,
keyed by provider NPI or code pair) if a case in development actually needs them.
