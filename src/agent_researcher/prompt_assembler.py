"""Assemble the messages sent to the hypothesis-generator model.

Loads the system prompt and user template from prompts/, fills the template
with the target agent's source and the failing scenario's data, returns the
two messages ready for the API.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Optional

from .code_reader import TargetAgentSource
from .eval_analyzer import EvalFailure


def load_system_prompt() -> str:
    """Read the hypothesis-generator system prompt bundled with the package."""
    return resources.files("agent_researcher.prompts").joinpath(
        "hypothesis_system.md"
    ).read_text()


def load_user_template() -> str:
    """Read the user-message template bundled with the package."""
    return resources.files("agent_researcher.prompts").joinpath(
        "hypothesis_user.md"
    ).read_text()


def build_user_message(
    target: TargetAgentSource,
    failure: EvalFailure,
    scenario_input: Optional[str] = None,
) -> str:
    """Fill the user-message template with the target agent's code + failure.

    Args:
        target: The loaded target agent.
        failure: The specific failure to investigate.
        scenario_input: The actual input text for the failing scenario.
            (Required for the model to reason about what the agent saw.
            If None, a placeholder is used and a warning is included.)
    """
    template = load_user_template()

    if scenario_input is None:
        scenario_input = (
            "[NOTE: scenario input text not provided. Hypotheses may be "
            "weakened by absence of the actual user message.]"
        )

    additional_files = _format_additional_files(target)

    filled = template.format(
        target_agent_name=target.name,
        eval_failure_json=json.dumps(failure.raw, indent=2),
        scenario_input=scenario_input,
        agent_yaml=_number_lines(target.agent_yaml) if target.agent_yaml else "[no agent.yaml found]",
        system_prompt=_number_lines(target.system_prompt) if target.system_prompt else "[no system.j2 found]",
        classification_prompt=_number_lines(target.classification_prompt) if target.classification_prompt else "[no classification.j2 found]",
        additional_files_section=additional_files,
    )
    return filled


def _number_lines(content: str) -> str:
    """Prefix every line with its 1-indexed line number.

    Format: 4-char right-aligned gutter, two-space separator, then the line.
    Blank lines are still numbered. Output: "{N:4d}  {line}".
    Used so the hypothesis-generator model can cite verifiable file:line
    references — without this, the model has to count lines in a long code
    block and tends to fabricate line numbers.
    """
    lines = content.splitlines()
    return "\n".join(f"{i + 1:4d}  {line}" for i, line in enumerate(lines))


def _format_additional_files(target: TargetAgentSource) -> str:
    """Format any optional target-agent files into a markdown section."""
    sections: list[str] = []

    if target.handoff_prompt:
        sections.append(_code_block("handoff.j2", target.handoff_prompt, lang=""))

    for intent, prompt in sorted(target.flow_prompts.items()):
        sections.append(_code_block(f"{intent}_flow.j2", prompt, lang=""))

    for filename, source in sorted(target.tool_sources.items()):
        sections.append(_code_block(f"tools/{filename}", source, lang="python"))

    if target.runner_source:
        sections.append(_code_block("runner.py", target.runner_source, lang="python"))

    if not sections:
        return ""

    return "### Additional files\n\n" + "\n\n".join(sections)


def _code_block(label: str, content: str, lang: str = "") -> str:
    """Format a file as a markdown header + fenced code block, line-numbered."""
    return f"#### {label}\n\n```{lang}\n{_number_lines(content)}\n```"
