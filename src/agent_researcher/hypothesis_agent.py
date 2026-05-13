"""Hypothesis-generator agent.

Calls Claude with the system prompt + user message, returns the markdown
hypothesis report.

Designed to be a single Claude call, not a multi-turn conversation. The
system prompt does the structuring; the user message provides evidence.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import anthropic

from .code_reader import TargetAgentSource
from .eval_analyzer import EvalFailure
from .prompt_assembler import build_user_message, load_system_prompt


DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 4000


@dataclass
class HypothesisReport:
    """A hypothesis report from the agent, plus metadata about the call."""

    markdown: str
    model: str
    input_tokens: int
    output_tokens: int


def generate_hypotheses(
    target: TargetAgentSource,
    failure: EvalFailure,
    scenario_input: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    client: Optional[anthropic.Anthropic] = None,
) -> HypothesisReport:
    """Generate a hypothesis report for a target agent's failure.

    Args:
        target: The target agent's loaded source.
        failure: The specific failure to investigate.
        scenario_input: The actual user message that the agent saw (highly
            recommended; the report is weaker without it).
        model: Claude model to use.
        max_tokens: Output cap. 4000 is comfortable for 2-3 hypotheses.
        client: Optional anthropic.Anthropic client. If None, constructed
            with ANTHROPIC_API_KEY from the environment.

    Returns:
        HypothesisReport with the markdown output.

    Raises:
        RuntimeError: if ANTHROPIC_API_KEY is not set and no client is given.
    """
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "Set the Anthropic API key in the environment before running."
            )
        client = anthropic.Anthropic()

    system_prompt = load_system_prompt()
    user_message = build_user_message(target, failure, scenario_input=scenario_input)

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    markdown = _extract_text(response)
    return HypothesisReport(
        markdown=markdown,
        model=model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


def _extract_text(response: anthropic.types.Message) -> str:
    """Pull text out of a Message response. Tolerant of multi-block content."""
    parts: list[str] = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()
