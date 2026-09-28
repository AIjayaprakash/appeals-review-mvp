"""Unit tests for the summarization_agent LLM node, with the Azure OpenAI client
mocked -- same pattern as test_nodes_intake_agent.py. One opt-in test at the
bottom hits the real model and is skipped unless Azure OpenAI credentials are
configured.
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from app.core.nodes import SUMMARIZATION_MODEL_DEPLOYMENT, SummarizationError, summarization_agent


def _fake_response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.choices[0].message.content = json.dumps(payload)
    return response


def test_summarization_agent_sends_redacted_text_to_the_model():
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response({"summary": "A brief summary."})

    redacted_text = "[[PERSON_1]] appealed on behalf of member [[MEMBER_ID_1]]."
    summarization_agent(redacted_text, client=client)

    _, kwargs = client.chat.completions.create.call_args
    user_message = next(m["content"] for m in kwargs["messages"] if m["role"] == "user")
    assert user_message == redacted_text
    assert "Eleanor" not in user_message
    assert kwargs["model"] == SUMMARIZATION_MODEL_DEPLOYMENT


def test_summarization_agent_echoes_tokens_back_unchanged():
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response(
        {"summary": "Member [[MEMBER_ID_1]] ([[PERSON_1]]) appeals a denied CRT-D implant."}
    )

    result = summarization_agent("irrelevant redacted text", client=client)

    # summarization_agent itself never rehydrates -- that's the caller's job.
    assert "[[MEMBER_ID_1]]" in result
    assert "[[PERSON_1]]" in result


def test_summarization_agent_raises_on_empty_summary():
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response({"summary": ""})

    with pytest.raises(SummarizationError):
        summarization_agent("some redacted text", client=client)


def test_summarization_agent_raises_when_summary_key_missing():
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_response({"not_summary": "oops"})

    with pytest.raises(SummarizationError):
        summarization_agent("some redacted text", client=client)


@pytest.mark.skipif(
    not (os.environ.get("AZURE_OPENAI_ENDPOINT") and os.environ.get("AZURE_OPENAI_API_KEY")),
    reason="opt-in: requires real AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY",
)
def test_summarization_agent_real_azure_openai_call():
    redacted_text = (
        "[[PERSON_1]] appeals the denial of a CRT-D implant (CPT 33249) for member "
        "[[MEMBER_ID_1]]. The denial cited insufficient GDMT documentation; the appeal "
        "now includes sacubitril/valsartan initiated 2026-05-11."
    )

    result = summarization_agent(redacted_text)

    assert isinstance(result, str)
    assert result.strip()
    assert "[[MEMBER_ID_1]]" in result or "[[PERSON_1]]" in result
