"""Tests for the parts of agent-researcher that don't need API calls.

Run with: python -m pytest tests/
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agent_researcher.code_reader import load_target_agent
from agent_researcher.eval_analyzer import load_eval_result, select_failure


def _make_target_agent(root: Path) -> Path:
    """Create a minimal target-agent directory for testing."""
    agent_dir = root / "fake_agent"
    (agent_dir / "prompts").mkdir(parents=True)
    (agent_dir / "tools").mkdir()

    (agent_dir / "agent.yaml").write_text("name: fake_agent\nintents: [bug, feature]\n")
    (agent_dir / "prompts" / "system.j2").write_text("You are a fake agent.")
    (agent_dir / "prompts" / "classification.j2").write_text("Classify into one of: bug, feature.")
    (agent_dir / "prompts" / "bug_flow.j2").write_text("Handle the bug report.")
    (agent_dir / "tools" / "lookup_issue.py").write_text("def call(issue_id): pass\n")
    (agent_dir / "tools" / "__init__.py").write_text("")
    (agent_dir / "runner.py").write_text("def main(): pass\n")
    return agent_dir


def test_load_target_agent_finds_all_pieces(tmp_path: Path) -> None:
    agent_dir = _make_target_agent(tmp_path)
    target = load_target_agent(agent_dir)

    assert target.name == "fake_agent"
    assert target.agent_yaml is not None
    assert "fake_agent" in target.agent_yaml
    assert target.system_prompt == "You are a fake agent."
    assert target.classification_prompt is not None
    assert "bug" in target.flow_prompts
    assert "lookup_issue.py" in target.tool_sources
    assert "__init__.py" not in target.tool_sources  # skipped by design
    assert target.runner_source is not None


def test_load_target_agent_tolerates_missing_files(tmp_path: Path) -> None:
    agent_dir = tmp_path / "minimal_agent"
    agent_dir.mkdir()
    (agent_dir / "agent.yaml").write_text("name: minimal\n")

    target = load_target_agent(agent_dir)
    assert target.agent_yaml is not None
    assert target.system_prompt is None
    assert target.flow_prompts == {}
    assert target.tool_sources == {}


def test_load_target_agent_rejects_unrelated_dir(tmp_path: Path) -> None:
    empty = tmp_path / "not_an_agent"
    empty.mkdir()
    (empty / "readme.txt").write_text("not an agent")

    with pytest.raises(ValueError, match="does not look like an agent"):
        load_target_agent(empty)


def test_load_target_agent_rejects_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_target_agent(tmp_path / "does_not_exist")


def test_eval_analyzer_parses_routing_eval_shape(tmp_path: Path) -> None:
    eval_path = tmp_path / "eval.json"
    eval_path.write_text(json.dumps({
        "total": 3,
        "passed": 2,
        "pass_rate": 0.667,
        "threshold": 0.9,
        "meets_threshold": False,
        "results": [
            {
                "issue_number": 101,
                "expected_intent": "bug",
                "predicted_intent": "bug",
                "predicted_confidence": 0.95,
                "passed": True,
                "notes": "Clear case.",
            },
            {
                "issue_number": 107,
                "expected_intent": "unknown",
                "predicted_intent": "bug",
                "predicted_confidence": 0.75,
                "passed": False,
                "notes": "Ambiguous.",
            },
            {
                "issue_number": 200,
                "expected_intent": "feature",
                "predicted_intent": "bug",
                "predicted_confidence": 0.6,
                "passed": False,
                "notes": "Mislabeled.",
            },
        ],
    }))

    summary = load_eval_result(eval_path)
    assert summary.total == 3
    assert summary.passed == 2
    assert len(summary.failures) == 2

    failure = select_failure(summary, scenario_id="107")
    assert failure.scenario_id == "107"
    assert failure.expected == "unknown"
    assert failure.predicted == "bug"
    assert failure.predicted_confidence == 0.75


def test_select_failure_no_failures(tmp_path: Path) -> None:
    eval_path = tmp_path / "all_pass.json"
    eval_path.write_text(json.dumps({
        "total": 1,
        "passed": 1,
        "results": [
            {"issue_number": 1, "expected_intent": "bug", "predicted_intent": "bug", "passed": True},
        ],
    }))

    summary = load_eval_result(eval_path)
    with pytest.raises(ValueError, match="No failures"):
        select_failure(summary)


def test_select_failure_unknown_id(tmp_path: Path) -> None:
    eval_path = tmp_path / "eval.json"
    eval_path.write_text(json.dumps({
        "results": [
            {"issue_number": 1, "expected_intent": "x", "predicted_intent": "y", "passed": False, "notes": ""},
        ],
    }))

    summary = load_eval_result(eval_path)
    with pytest.raises(ValueError, match="No failure found"):
        select_failure(summary, scenario_id="99")
