"""Load a target agent's code into structured form for hypothesis generation.

Designed for agents built with the agent-skill-kit methodology (Layer 4):
    target_agent/
      agent.yaml
      prompts/
        system.j2
        classification.j2
        (optionally) <intent>_flow.j2 for each in-scope intent
        handoff.j2
      tools/
        <tool_name>.py
      runner.py

The loader is tolerant. Missing files surface as None in the returned dict
rather than raising. The hypothesis agent uses what's present.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class TargetAgentSource:
    """A target agent's code, loaded for inspection."""

    name: str
    agent_yaml: Optional[str]
    system_prompt: Optional[str]
    classification_prompt: Optional[str]
    handoff_prompt: Optional[str]
    flow_prompts: dict[str, str]  # intent name -> prompt text
    tool_sources: dict[str, str]  # tool filename -> python source
    runner_source: Optional[str]


def load_target_agent(agent_dir: Path) -> TargetAgentSource:
    """Load a target agent from its directory.

    Args:
        agent_dir: Path to a directory containing agent.yaml, prompts/, tools/.

    Returns:
        TargetAgentSource with whatever was found. Missing files are None.

    Raises:
        FileNotFoundError: if agent_dir itself doesn't exist.
        ValueError: if neither agent.yaml nor any prompts/ are present
                   (looks like the wrong directory).
    """
    agent_dir = Path(agent_dir)
    if not agent_dir.is_dir():
        raise FileNotFoundError(f"Target agent directory not found: {agent_dir}")

    agent_yaml_path = agent_dir / "agent.yaml"
    agent_yaml = _read_if_exists(agent_yaml_path)

    prompts_dir = agent_dir / "prompts"
    system_prompt = _read_if_exists(prompts_dir / "system.j2")
    classification_prompt = _read_if_exists(prompts_dir / "classification.j2")
    handoff_prompt = _read_if_exists(prompts_dir / "handoff.j2")

    flow_prompts: dict[str, str] = {}
    if prompts_dir.is_dir():
        for path in prompts_dir.glob("*_flow.j2"):
            intent_name = path.stem.removesuffix("_flow")
            flow_prompts[intent_name] = path.read_text()

    tools_dir = agent_dir / "tools"
    tool_sources: dict[str, str] = {}
    if tools_dir.is_dir():
        for path in tools_dir.glob("*.py"):
            if path.name == "__init__.py":
                continue
            tool_sources[path.name] = path.read_text()

    runner_source = _read_if_exists(agent_dir / "runner.py")

    if agent_yaml is None and system_prompt is None and not tool_sources:
        raise ValueError(
            f"Directory does not look like an agent: {agent_dir}. "
            "Expected agent.yaml or prompts/system.j2 or tools/*.py."
        )

    return TargetAgentSource(
        name=agent_dir.name,
        agent_yaml=agent_yaml,
        system_prompt=system_prompt,
        classification_prompt=classification_prompt,
        handoff_prompt=handoff_prompt,
        flow_prompts=flow_prompts,
        tool_sources=tool_sources,
        runner_source=runner_source,
    )


def _read_if_exists(path: Path) -> Optional[str]:
    """Return file contents, or None if the file doesn't exist."""
    if not path.is_file():
        return None
    return path.read_text()
