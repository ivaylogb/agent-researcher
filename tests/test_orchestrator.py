"""Tests for the orchestrator: report-order iteration, skip path, failure
isolation, transactional revert, single baseline eval, and IterationReport
structure.

These tests use real on-disk fixture files for the snapshot/revert path
(verifies the orchestrator actually restores byte-for-byte) and stub out the
eval subprocess via monkeypatch (no real subprocess invocation).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_researcher import orchestrator
from agent_researcher.eval_analyzer import EvalSummary
from agent_researcher.eval_runner import EvalRunError, EvalRunOutput


# ---------- Helpers ----------


def _write_target_file(target_dir: Path, relpath: str, content: str) -> Path:
    """Create a file under target_dir at relpath with the given content."""
    full = target_dir / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    return full


def _make_report(report_path: Path, hypotheses: list[tuple[int, str, str]]) -> None:
    """Write a minimal hypothesis report. Each tuple = (id, header_suffix, json_block)."""
    parts = ["# Hypothesis report: fixture\n\n", "Scenario: issue 42\n\n", "## Hypotheses\n\n"]
    for hid, suffix, json_block in hypotheses:
        parts.append(f"### Hypothesis {hid}: {suffix}\n\n")
        parts.append(f"**Claim:** Claim for H{hid}.\n\n")
        parts.append("**Evidence:**\n- foo.txt:1: trivial\n\n")
        parts.append("**Proposed change:** apply edits below.\n\n")
        parts.append(f"```json\n{json_block}\n```\n\n")
        parts.append("**How to verify:** trivially.\n\n")
    parts.append("## What this report is NOT\n\n- placeholder\n")
    report_path.write_text("".join(parts))


def _summary(results: list[dict]) -> EvalSummary:
    """Build an EvalSummary that matches what eval_analyzer would produce."""
    passed = sum(1 for r in results if r.get("passed"))
    total = len(results)
    return EvalSummary(
        total=total,
        passed=passed,
        pass_rate=(passed / total) if total else 0.0,
        threshold=0.9,
        meets_threshold=False,
        failures=[],
        all_results=results,
    )


def _record(scenario: int, expected: str, predicted: str, passed: bool) -> dict:
    return {
        "issue_number": scenario,
        "expected_intent": expected,
        "predicted_intent": predicted,
        "predicted_confidence": 0.8,
        "passed": passed,
        "notes": "",
    }


@dataclass
class _StubEval:
    """Programmable replacement for eval_runner_module.run_eval."""

    queue: list  # each entry is either an EvalRunOutput, an Exception, or callable
    calls: list = None

    def __post_init__(self):
        self.calls = []

    def __call__(self, target_agent_dir, eval_command, *, timeout=300, cwd=None, result_path=None):
        self.calls.append({
            "target_agent_dir": target_agent_dir,
            "eval_command": eval_command,
            "timeout": timeout,
            "cwd": cwd,
            "result_path": result_path,
        })
        if not self.queue:
            raise AssertionError("Stub run_eval called more times than queued responses.")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        return item


def _eval_output(summary: EvalSummary) -> EvalRunOutput:
    return EvalRunOutput(summary=summary, stdout="", stderr="", returncode=0)


# ---------- Tests ----------


def test_iterate_processes_hypotheses_in_report_order(tmp_path, monkeypatch):
    """Even when IDs aren't sequential, results follow report order."""
    target = tmp_path / "target"
    target.mkdir()
    _write_target_file(target, "f.txt", "alpha\nbeta\ngamma\n")

    report = tmp_path / "report.md"
    _make_report(report, [
        (2, "H2 (Layer 1)",
         '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":1,"from_line_end":1,"expected_content":"alpha",'
         '"new_content":"ALPHA"}]}'),
        (1, "H1 (Layer 2)",
         '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":2,"from_line_end":2,"expected_content":"beta",'
         '"new_content":"BETA"}]}'),
    ])

    baseline = _eval_output(_summary([_record(42, "x", "y", False)]))
    after = _eval_output(_summary([_record(42, "x", "x", True)]))
    stub = _StubEval(queue=[baseline, after, after])
    monkeypatch.setattr(orchestrator.eval_runner_module, "run_eval", stub)

    report_obj = orchestrator.iterate(report, target, "cmd")

    assert [r.hypothesis_id for r in report_obj.results] == [2, 1]


def test_iterate_skips_non_applyable_hypotheses(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    _write_target_file(target, "f.txt", "alpha\n")
    report = tmp_path / "report.md"
    _make_report(report, [
        (1, "H1", '{"applyable": false, "reason": "needs a new tool file"}'),
    ])

    baseline = _eval_output(_summary([_record(42, "x", "y", False)]))
    stub = _StubEval(queue=[baseline])
    monkeypatch.setattr(orchestrator.eval_runner_module, "run_eval", stub)

    report_obj = orchestrator.iterate(report, target, "cmd")

    assert len(report_obj.results) == 1
    r = report_obj.results[0]
    assert r.status == "skipped"
    assert r.skip_reason == "needs a new tool file"
    assert r.delta is None
    # Only the baseline eval should have run — no re-eval for a skipped hypothesis.
    assert len(stub.calls) == 1


def test_iterate_isolates_apply_failure(tmp_path, monkeypatch):
    """Apply failure on H1 must not prevent H2 from running."""
    target = tmp_path / "target"
    target.mkdir()
    _write_target_file(target, "f.txt", "alpha\nbeta\n")
    report = tmp_path / "report.md"
    _make_report(report, [
        # H1: expected_content mismatch — applier will raise ValueError.
        (1, "H1",
         '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":1,"from_line_end":1,"expected_content":"WRONG",'
         '"new_content":"ALPHA"}]}'),
        # H2: valid edit.
        (2, "H2",
         '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":2,"from_line_end":2,"expected_content":"beta",'
         '"new_content":"BETA"}]}'),
    ])

    baseline = _eval_output(_summary([_record(42, "x", "y", False)]))
    after = _eval_output(_summary([_record(42, "x", "x", True)]))
    stub = _StubEval(queue=[baseline, after])
    monkeypatch.setattr(orchestrator.eval_runner_module, "run_eval", stub)

    report_obj = orchestrator.iterate(report, target, "cmd")

    assert report_obj.results[0].status == "apply_failed"
    assert "expected_content" in (report_obj.results[0].error or "").lower() or \
           "does not match" in (report_obj.results[0].error or "")
    assert report_obj.results[1].status == "applied"
    # baseline + H2 re-eval (H1 never reached the eval stage).
    assert len(stub.calls) == 2


def test_iterate_isolates_eval_failure(tmp_path, monkeypatch):
    """Eval failure on H1's re-eval must not prevent H2 from running."""
    target = tmp_path / "target"
    target.mkdir()
    fixture = _write_target_file(target, "f.txt", "alpha\nbeta\n")
    original_bytes = fixture.read_bytes()
    report = tmp_path / "report.md"
    _make_report(report, [
        (1, "H1",
         '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":1,"from_line_end":1,"expected_content":"alpha",'
         '"new_content":"ALPHA"}]}'),
        (2, "H2",
         '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":2,"from_line_end":2,"expected_content":"beta",'
         '"new_content":"BETA"}]}'),
    ])

    baseline = _eval_output(_summary([_record(42, "x", "y", False)]))
    after = _eval_output(_summary([_record(42, "x", "x", True)]))
    stub = _StubEval(queue=[baseline, EvalRunError("subprocess crashed"), after])
    monkeypatch.setattr(orchestrator.eval_runner_module, "run_eval", stub)

    report_obj = orchestrator.iterate(report, target, "cmd")

    assert report_obj.results[0].status == "eval_failed"
    assert "subprocess crashed" in (report_obj.results[0].error or "")
    assert report_obj.results[1].status == "applied"
    # After eval_failed on H1, the file should be reverted before H2 runs.
    # After H2 runs, the file should also be reverted.
    assert fixture.read_bytes() == original_bytes


def test_iterate_reverts_files_after_each_iteration(tmp_path, monkeypatch):
    """Target files must end byte-identical to their pre-iterate state."""
    target = tmp_path / "target"
    target.mkdir()
    fixture = _write_target_file(target, "f.txt", "alpha\nbeta\ngamma\n")
    original_bytes = fixture.read_bytes()
    report = tmp_path / "report.md"
    _make_report(report, [
        (1, "H1",
         '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":1,"from_line_end":1,"expected_content":"alpha",'
         '"new_content":"ALPHA"}]}'),
        (2, "H2",
         '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":2,"from_line_end":2,"expected_content":"beta",'
         '"new_content":"BETA"}]}'),
    ])

    baseline = _eval_output(_summary([_record(42, "x", "y", False)]))
    after = _eval_output(_summary([_record(42, "x", "x", True)]))
    stub = _StubEval(queue=[baseline, after, after])
    monkeypatch.setattr(orchestrator.eval_runner_module, "run_eval", stub)

    report_obj = orchestrator.iterate(report, target, "cmd")

    assert all(r.status == "applied" for r in report_obj.results)
    assert fixture.read_bytes() == original_bytes


def test_iterate_runs_baseline_eval_exactly_once(tmp_path, monkeypatch):
    """Baseline must NOT be re-measured per hypothesis."""
    target = tmp_path / "target"
    target.mkdir()
    _write_target_file(target, "f.txt", "alpha\nbeta\ngamma\n")
    report = tmp_path / "report.md"
    _make_report(report, [
        (1, "H1",
         '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":1,"from_line_end":1,"expected_content":"alpha",'
         '"new_content":"ALPHA"}]}'),
        (2, "H2",
         '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":2,"from_line_end":2,"expected_content":"beta",'
         '"new_content":"BETA"}]}'),
        (3, "H3", '{"applyable": false, "reason": "needs new file"}'),
    ])

    baseline = _eval_output(_summary([_record(42, "x", "y", False)]))
    after = _eval_output(_summary([_record(42, "x", "x", True)]))
    # 1 baseline + 2 re-evals (H3 skipped, no re-eval).
    stub = _StubEval(queue=[baseline, after, after])
    monkeypatch.setattr(orchestrator.eval_runner_module, "run_eval", stub)

    report_obj = orchestrator.iterate(report, target, "cmd")

    assert len(stub.calls) == 3
    # First call is the baseline. The orchestrator must not call run_eval again
    # to re-measure the baseline between hypotheses.
    assert report_obj.baseline is baseline.summary


def test_iterate_returns_well_formed_report(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    _write_target_file(target, "f.txt", "alpha\n")
    report = tmp_path / "report.md"
    _make_report(report, [
        (1, "H1 (Layer 3)",
         '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":1,"from_line_end":1,"expected_content":"alpha",'
         '"new_content":"ALPHA"}]}'),
    ])

    baseline = _eval_output(_summary([_record(42, "x", "y", False)]))
    after = _eval_output(_summary([_record(42, "x", "x", True)]))
    stub = _StubEval(queue=[baseline, after])
    monkeypatch.setattr(orchestrator.eval_runner_module, "run_eval", stub)

    report_obj = orchestrator.iterate(report, target, "cmd")

    assert report_obj.target_scenario_id == "42"
    assert report_obj.baseline.total == 1
    assert report_obj.baseline_duration_seconds >= 0
    assert report_obj.total_duration_seconds >= report_obj.baseline_duration_seconds
    assert len(report_obj.results) == 1
    r = report_obj.results[0]
    assert r.hypothesis_id == 1
    assert r.layer == "Layer 3"
    assert r.claim == "Claim for H1."
    assert r.status == "applied"
    assert r.delta is not None
    assert r.delta.pass_rate_change == 1.0
    assert r.edit_count == 1


def test_iterate_raises_on_missing_report(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(FileNotFoundError):
        orchestrator.iterate(tmp_path / "does_not_exist.md", target, "cmd")


def test_iterate_raises_on_empty_report(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    report = tmp_path / "report.md"
    report.write_text("# Report with no hypothesis headers.\n")
    with pytest.raises(ValueError, match="no '### Hypothesis"):
        orchestrator.iterate(report, target, "cmd")


def test_iterate_raises_when_target_scenario_cannot_be_inferred(tmp_path, monkeypatch):
    """No 'Scenario:' line in report AND no failing baseline scenarios → raise."""
    target = tmp_path / "target"
    target.mkdir()
    _write_target_file(target, "f.txt", "alpha\n")

    # Report without a "Scenario:" line.
    report = tmp_path / "report.md"
    json_block = (
        '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
        '"from_line_start":1,"from_line_end":1,"expected_content":"alpha",'
        '"new_content":"ALPHA"}]}'
    )
    report.write_text(
        "# Hypothesis report: no scenario line\n\n"
        "## Hypotheses\n\n"
        "### Hypothesis 1: H1\n\n"
        "**Claim:** trivial.\n\n"
        f"```json\n{json_block}\n```\n\n"
        "## What this report is NOT\n\n- placeholder\n"
    )

    # Baseline with no failures.
    baseline = _eval_output(_summary([_record(42, "x", "x", True)]))
    stub = _StubEval(queue=[baseline])
    monkeypatch.setattr(orchestrator.eval_runner_module, "run_eval", stub)

    with pytest.raises(ValueError, match="Could not infer target scenario ID"):
        orchestrator.iterate(report, target, "cmd")


def test_iterate_propagates_baseline_eval_failure(tmp_path, monkeypatch):
    """If the baseline eval fails, iterate raises rather than recording it.

    There is no defined behavior without a baseline — the caller has to handle it.
    """
    target = tmp_path / "target"
    target.mkdir()
    _write_target_file(target, "f.txt", "alpha\n")
    report = tmp_path / "report.md"
    _make_report(report, [
        (1, "H1", '{"applyable": true, "edits": [{"file":"f.txt","action":"replace",'
         '"from_line_start":1,"from_line_end":1,"expected_content":"alpha",'
         '"new_content":"ALPHA"}]}'),
    ])

    stub = _StubEval(queue=[EvalRunError("baseline crashed")])
    monkeypatch.setattr(orchestrator.eval_runner_module, "run_eval", stub)

    with pytest.raises(EvalRunError, match="baseline crashed"):
        orchestrator.iterate(report, target, "cmd")
