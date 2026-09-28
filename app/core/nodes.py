"""LangGraph nodes: intake_agent, summarization_agent, evaluation_agent.

Phase 2 implements intake_agent only. It receives already-redacted text (see
app/security/redaction.py) and extracts structured fields via Azure OpenAI
GPT-4o-mini -- nothing else. It never invents a missing field: anything not
explicitly present in the source text comes back null (or [] for the array
fields), never guessed. Redaction tokens (e.g. [[PERSON_1]]) in the source text
must be echoed back verbatim in the corresponding field, not decoded -- the caller
re-hydrates them afterwards.
"""

from __future__ import annotations

import json
import os

from openai import AzureOpenAI

AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
INTAKE_MODEL_DEPLOYMENT = os.environ.get("AZURE_OPENAI_INTAKE_DEPLOYMENT", "gpt-4o-mini")

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
