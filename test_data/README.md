# Intake Test Data — MVP1

Synthetic test fixtures for the two MVP1 intake channels (email and medical-history
upload). All member names, IDs, dates, and case references are fictional — safe to
commit to a repo and run through the pipeline as-is.

## What's in here

```
emails/
  EMAIL-001_typical_with_attachment.eml       happy path — well-formed, 1 PDF attachment
  EMAIL-002_minimal_missing_member_id.eml      edge case — informal, no member ID, no attachment
  EMAIL-003_forwarded_multi_attachment.eml     edge case — forwarded thread, 2 attachments (PDF + .txt)

medical_history/
  MEDHIST-001_structured_case_record.pdf       happy path — clean typed record, tables
  MEDHIST-002_appeal_letter_narrative.pdf      edge case — prose-only letter, no ICD-10 codes, no member ID
  MEDHIST-003_multipage_labs.pdf               edge case — 2 pages, lab-results table, page break mid-record

expected_extraction.json                       ground-truth fields for every file above
```

## How to use this

1. **Feed each file through its matching intake channel.** The `.eml` files go through
   the email connector path; the `.pdf` files go through the upload endpoint.
2. **Compare the Intake Agent's output against `expected_extraction.json`.** Each
   entry gives the fields a correct extraction should produce, the expected case
   status after intake, and a short note on what that specific file is meant to
   stress-test.
3. **Don't just check the happy-path files.** `EMAIL-002` and `MEDHIST-002` are
   deliberately missing fields (member ID, diagnosis codes) — the correct behavior
   is to leave those fields empty and route the case to `Needs Clarification`,
   not to guess a value or silently drop into `Structured`.

## Coverage this set gives you

| Concern | Covered by |
|---|---|
| Clean structured extraction | EMAIL-001, MEDHIST-001 |
| Missing required fields | EMAIL-002, MEDHIST-002 |
| Multi-attachment / mixed file types in one email | EMAIL-003 |
| Forwarded-thread sender identity (From header ≠ real requester) | EMAIL-003 |
| Table extraction (diagnosis, treatment history, labs) | MEDHIST-001, MEDHIST-003 |
| Free-text / prose-only clinical narrative | MEDHIST-002 |
| Multi-page documents, fields split across a page break | MEDHIST-003 |
| Same underlying case arriving via two different channels | EMAIL-001's attachment and MEDHIST-001 are the same case — results should match regardless of channel |

## Extending the set

Same shape works for adding more scenarios later — a scanned/handwritten-notes
case for OCR robustness, or a non-English intake email, are the two most useful
additions once the pipeline handles these six cleanly.

## EMAIL-004 (Phase 0.5 addition)

`emails/EMAIL-004_duplicate_of_email_001.eml` is a near-identical follow-up to
EMAIL-001, sent 3 days later by the same office, referencing the same
`case_reference` (APL-2026-00312) and the same member. It backs Scenario 9
(duplicate submission, merge) from `docs/scenarios-operational-plan.pdf` — see
`reference_data/scenario_manifest.json` for the full scenario-to-data mapping across
all ten outcomes, not just extraction. It is not in `expected_extraction.json`,
which is scoped to the original six intake-extraction fixtures.
