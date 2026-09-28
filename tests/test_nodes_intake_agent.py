"""Unit tests for the intake_agent LLM node, with the Azure OpenAI client mocked.
One opt-in test at the bottom hits the real model and is skipped unless Azure
OpenAI credentials are configured.
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from app.core.nodes import ARRAY_FIELDS, REQUIRED_FIELDS, intake_agent


def _fake_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.choices[0].message.content = json.dumps(payload)
    return response


def test_intake_agent_sends_redacted_text_to_the_model():
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"member_id": "[[MEMBER_ID_1]]", "member_name": "[[PERSON_1]]"}
    )

    redacted_text = "[[PERSON_1]] appealed on behalf of member [[MEMBER_ID_1]]."
    intake_agent(redacted_text, client=client)

    _, kwargs = client.chat.completions.create.call_args
    user_message = next(m["content"] for m in kwargs["messages"] if m["role"] == "user")
    assert user_message == redacted_text
    assert "Eleanor" not in user_message


def test_intake_agent_echoes_tokens_back_unchanged():
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"member_id": "[[MEMBER_ID_1]]", "member_name": "[[PERSON_1]]", "date_of_birth": "[[DATE_TIME_1]]"}
    )

    result = intake_agent("irrelevant redacted text", client=client)

    # intake_agent itself never rehydrates -- that's the caller's job.
    assert result["member_id"] == "[[MEMBER_ID_1]]"
    assert result["member_name"] == "[[PERSON_1]]"
    assert result["date_of_birth"] == "[[DATE_TIME_1]]"


def test_intake_agent_fills_missing_fields_with_null_and_empty_lists():
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"member_name": "Marion Carter", "procedure_requested": "physical therapy"}
    )

    result = intake_agent("some redacted text", client=client)

    for field in REQUIRED_FIELDS:
        assert field in result
    assert result["member_id"] is None
    assert result["case_reference"] is None
    assert result["denial_reference"] is None
    for field in ARRAY_FIELDS:
        assert result[field] == []


def test_intake_agent_never_fabricates_extra_fields_the_model_did_not_return():
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"member_id": "MBR-778241", "some_unexpected_field": "should be ignored"}
    )

    result = intake_agent("some redacted text", client=client)

    assert "some_unexpected_field" not in result
    assert set(result.keys()) == set(REQUIRED_FIELDS) | set(ARRAY_FIELDS)


@pytest.mark.skipif(
    not (os.environ.get("AZURE_OPENAI_ENDPOINT") and os.environ.get("AZURE_OPENAI_API_KEY")),
    reason="opt-in: requires real AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY",
)
def test_intake_agent_real_azure_openai_call():
    redacted_text = (
        "[[PERSON_1]] appealed the denial of a CRT-D implant (CPT 33249) for member "
        "[[MEMBER_ID_1]], denial reference [[DENIAL_REFERENCE_1]], case reference "
        "[[CASE_REFERENCE_1]]. Requesting provider: Dr. Aisha Kapoor."
    )

    result = intake_agent(redacted_text)

    assert result["member_id"] == "[[MEMBER_ID_1]]"
    assert result["member_name"] == "[[PERSON_1]]"
    assert result["denial_reference"] == "[[DENIAL_REFERENCE_1]]"
    assert result["case_reference"] == "[[CASE_REFERENCE_1]]"
    assert "Kapoor" in (result["requesting_provider"] or "")
