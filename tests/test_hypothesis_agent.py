"""Tests for hypothesis_agent. Uses a stub client to exercise the call path
without burning API tokens. Covers what the review flagged as untested:
- message construction
- token-count surfacing
- _extract_text on multi-block content
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agent_researcher.code_reader import TargetAgentSource
from agent_researcher.eval_analyzer import EvalFailure
from agent_researcher.hypothesis_agent import _extract_text, generate_hypotheses


# ---------- Stub client infrastructure ----------


@dataclass
class _StubTextBlock:
    text: str
    type: str = "text"


@dataclass
class _StubToolUseBlock:
    """A non-text block, included to verify _extract_text filters correctly."""
    type: str = "tool_use"
    name: str = "ignored"
    input: dict = None


@dataclass
class _StubUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _StubMessage:
    content: list
    usage: _StubUsage


class _StubMessages:
    def __init__(self, response: _StubMessage):
        self._response = response
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs) -> _StubMessage:
        self.last_kwargs = kwargs
        return self._response


class _StubClient:
    def __init__(self, response: _StubMessage):
        self.messages = _StubMessages(response)


# ---------- Fixtures ----------


def _target() -> TargetAgentSource:
    return TargetAgentSource(
        name="test_agent",
        agent_yaml="name: test_agent\n",
        system_prompt="You are a test agent.",
        classification_prompt="Classify.",
        handoff_prompt=None,
        flow_prompts={},
        tool_sources={},
        runner_source=None,
    )


def _failure() -> EvalFailure:
    return EvalFailure(
        scenario_id="107",
        expected="unknown",
        predicted="bug",
        predicted_confidence=0.75,
        notes="Mixed signals.",
        raw={"issue_number": 107, "passed": False, "notes": "Mixed signals."},
    )


# ---------- Tests ----------


def test_extract_text_single_block() -> None:
    msg = _StubMessage(
        content=[_StubTextBlock(text="hello world")],
        usage=_StubUsage(input_tokens=10, output_tokens=2),
    )
    assert _extract_text(msg) == "hello world"


def test_extract_text_multi_block_filters_non_text() -> None:
    """Tool-use blocks and other non-text content must be filtered out."""
    msg = _StubMessage(
        content=[
            _StubTextBlock(text="first part"),
            _StubToolUseBlock(),
            _StubTextBlock(text="second part"),
        ],
        usage=_StubUsage(input_tokens=10, output_tokens=4),
    )
    result = _extract_text(msg)
    assert "first part" in result
    assert "second part" in result
    assert "ignored" not in result  # the stub tool_use name


def test_extract_text_strips_whitespace() -> None:
    msg = _StubMessage(
        content=[_StubTextBlock(text="\n\n  body  \n\n")],
        usage=_StubUsage(input_tokens=1, output_tokens=1),
    )
    assert _extract_text(msg) == "body"


def test_generate_hypotheses_constructs_message_correctly() -> None:
    stub_response = _StubMessage(
        content=[_StubTextBlock(text="# Hypothesis report\n\nbody")],
        usage=_StubUsage(input_tokens=5000, output_tokens=1500),
    )
    client = _StubClient(stub_response)

    report = generate_hypotheses(
        target=_target(),
        failure=_failure(),
        scenario_input="The docs say X but the code does Y.",
        client=client,
    )

    # The call was made with the right structure
    kwargs = client.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["max_tokens"] > 0
    assert "system" in kwargs
    assert len(kwargs["system"]) > 500  # the system prompt is substantial
    assert kwargs["messages"][0]["role"] == "user"
    # Target agent + scenario input made it into the user message
    user_content = kwargs["messages"][0]["content"]
    assert "test_agent" in user_content
    assert "The docs say X but the code does Y." in user_content


def test_generate_hypotheses_surfaces_tokens_and_model() -> None:
    stub_response = _StubMessage(
        content=[_StubTextBlock(text="report body")],
        usage=_StubUsage(input_tokens=12345, output_tokens=678),
    )
    client = _StubClient(stub_response)

    report = generate_hypotheses(
        target=_target(),
        failure=_failure(),
        scenario_input="x",
        model="claude-opus-4-7",
        client=client,
    )

    assert report.markdown == "report body"
    assert report.input_tokens == 12345
    assert report.output_tokens == 678
    assert report.model == "claude-opus-4-7"


def test_generate_hypotheses_warns_when_required_section_missing() -> None:
    """If the model returns prose without 'Hypothesis 1', the output may still
    be returned but should be detectable. Today's behavior: returns whatever
    the model produced. This test pins that behavior so a future change
    (e.g., adding structural validation per review item #6) is intentional."""
    stub_response = _StubMessage(
        content=[_StubTextBlock(text="Sorry, I can't help with that.")],
        usage=_StubUsage(input_tokens=100, output_tokens=10),
    )
    client = _StubClient(stub_response)

    report = generate_hypotheses(
        target=_target(),
        failure=_failure(),
        scenario_input="x",
        client=client,
    )

    # Today: no validation — output is returned regardless.
    assert "Sorry, I can't help" in report.markdown
    # This test exists to make future structural validation a conscious change.


def test_generate_hypotheses_requires_api_key_or_client() -> None:
    """If no client is passed and no env var is set, we raise — not silently
    construct a broken client."""
    import os

    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="Anthropic API key"):
            generate_hypotheses(
                target=_target(),
                failure=_failure(),
                scenario_input="x",
            )
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved
