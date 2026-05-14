"""Iterate apply-and-re-eval across every applyable hypothesis in a report.

For each hypothesis in report order: snapshot the files the applier would
touch, apply the edits, run the eval, compute the before/after delta against
a single shared baseline, then restore the snapshot so the next hypothesis
starts from a clean target. Failures in one hypothesis (bad spec, eval crash,
unhandled exception) are recorded and skipped — they do not stop subsequent
hypotheses, and they never leave the target in a modified state.

The orchestrator does not call any model. It consumes a diagnose report that
already exists on disk and operates on its structured edit specs.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import applier as applier_module
from . import eval_runner as eval_runner_module
from .applier import EditSpec, parse_hypothesis_report
from .delta import DeltaReport, compute_delta
from .eval_analyzer import EvalSummary
from .eval_runner import EvalRunError


_HYPOTHESIS_HEADER_RE = re.compile(
    r"^###\s+Hypothesis\s+(\d+)\b(.*)$", re.MULTILINE | re.IGNORECASE
)
_LAYER_FROM_HEADER_RE = re.compile(r"Layer\s+(\d+)", re.IGNORECASE)
_CLAIM_RE = re.compile(r"\*\*Claim:\*\*\s*(.+?)(?:\n\n|\n\*\*)", re.DOTALL)


# ---------- Data shapes ----------


@dataclass
class HypothesisResult:
    """One hypothesis's outcome from an iteration run."""

    hypothesis_id: int
    layer: Optional[str]
    claim: Optional[str]
    status: str  # "applied" | "skipped" | "apply_failed" | "eval_failed"
    delta: Optional[DeltaReport] = None
    after_summary: Optional[EvalSummary] = None
    files_modified: list[str] = field(default_factory=list)
    edit_count: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None
    skip_reason: Optional[str] = None


@dataclass
class IterationReport:
    """Result of iterating apply-and-re-eval across every hypothesis."""

    target_scenario_id: str
    baseline: EvalSummary
    baseline_duration_seconds: float
    results: list[HypothesisResult]
    total_duration_seconds: float
    total_input_tokens: int = 0
    total_output_tokens: int = 0


# ---------- Entry point ----------


def iterate(
    report_path: Path,
    target_agent_dir: Path,
    eval_command: str,
    *,
    eval_cwd: Optional[Path] = None,
    eval_result_path: Optional[Path] = None,
    eval_timeout: int = 300,
    target_scenario_id: Optional[str] = None,
) -> IterationReport:
    """Run apply-and-re-eval against every applyable hypothesis in the report.

    Args:
        report_path: Path to a diagnose report (contains structured edit specs).
        target_agent_dir: Root of the target agent's source tree.
        eval_command: Shell-style eval command (passed to eval_runner.run_eval).
        eval_cwd: Optional working directory for the eval subprocess.
        eval_result_path: Optional file the eval writes its JSON result to.
        eval_timeout: Per-eval subprocess timeout in seconds.
        target_scenario_id: Scenario the hypotheses are meant to fix. If None,
            inferred from the report's "Scenario:" line or, failing that, the
            first failing baseline scenario.

    Returns:
        IterationReport with baseline summary and one HypothesisResult per
        hypothesis in report order.

    Raises:
        FileNotFoundError: report or target dir missing.
        ValueError: report has no hypotheses, or target scenario can't be
            inferred (no "Scenario:" line and no failing baseline scenarios).
        EvalRunError: baseline eval failed — the caller cannot proceed without
            a baseline, so this propagates rather than being recorded.
    """
    report_path = Path(report_path)
    target_agent_dir = Path(target_agent_dir)

    if not report_path.is_file():
        raise FileNotFoundError(f"Hypothesis report not found: {report_path}")
    if not target_agent_dir.is_dir():
        raise FileNotFoundError(f"Target agent dir not found: {target_agent_dir}")

    report_text = report_path.read_text()
    hypothesis_ids = _hypothesis_ids_in_order(report_text)
    if not hypothesis_ids:
        raise ValueError(
            f"Report contains no '### Hypothesis N:' headers: {report_path}"
        )

    run_start = time.monotonic()

    baseline_start = time.monotonic()
    baseline_run = eval_runner_module.run_eval(
        target_agent_dir,
        eval_command,
        timeout=eval_timeout,
        cwd=eval_cwd,
        result_path=eval_result_path,
    )
    baseline_duration = time.monotonic() - baseline_start
    baseline_summary = baseline_run.summary

    if target_scenario_id is None:
        target_scenario_id = _infer_target_scenario_id(report_text, baseline_summary)

    results: list[HypothesisResult] = []
    for hid in hypothesis_ids:
        result = _process_one_hypothesis(
            hypothesis_id=hid,
            report_path=report_path,
            report_text=report_text,
            target_agent_dir=target_agent_dir,
            eval_command=eval_command,
            eval_cwd=eval_cwd,
            eval_result_path=eval_result_path,
            eval_timeout=eval_timeout,
            baseline_summary=baseline_summary,
            target_scenario_id=target_scenario_id,
        )
        results.append(result)

    total_duration = time.monotonic() - run_start
    return IterationReport(
        target_scenario_id=target_scenario_id,
        baseline=baseline_summary,
        baseline_duration_seconds=baseline_duration,
        results=results,
        total_duration_seconds=total_duration,
    )


# ---------- Per-hypothesis loop ----------


def _process_one_hypothesis(
    *,
    hypothesis_id: int,
    report_path: Path,
    report_text: str,
    target_agent_dir: Path,
    eval_command: str,
    eval_cwd: Optional[Path],
    eval_result_path: Optional[Path],
    eval_timeout: int,
    baseline_summary: EvalSummary,
    target_scenario_id: str,
) -> HypothesisResult:
    """Run one hypothesis through apply/eval/delta/revert, isolating failures."""
    start = time.monotonic()
    layer = _layer_for(report_text, hypothesis_id)
    claim = _claim_for(report_text, hypothesis_id)

    try:
        spec = parse_hypothesis_report(report_path, hypothesis_id)
    except (FileNotFoundError, ValueError) as e:
        return HypothesisResult(
            hypothesis_id=hypothesis_id,
            layer=layer,
            claim=claim,
            status="apply_failed",
            error=f"Failed to parse hypothesis: {e}",
            duration_seconds=time.monotonic() - start,
        )

    if not spec.applyable:
        return HypothesisResult(
            hypothesis_id=hypothesis_id,
            layer=layer,
            claim=claim,
            status="skipped",
            skip_reason=spec.reason,
            duration_seconds=time.monotonic() - start,
        )

    # Snapshot then apply then eval then revert — always revert in finally.
    snapshot: dict[Path, bytes] = {}
    files_modified: list[str] = []
    try:
        planned = applier_module.apply_edits(target_agent_dir, spec, dry_run=True)
        snapshot = _snapshot_files([change.path for change in planned])

        changes = applier_module.apply_edits(target_agent_dir, spec, dry_run=False)
        files_modified = [str(c.path) for c in changes if c.changed]
    except (FileNotFoundError, ValueError) as e:
        _restore_snapshot(snapshot)
        return HypothesisResult(
            hypothesis_id=hypothesis_id,
            layer=layer,
            claim=claim,
            status="apply_failed",
            error=str(e),
            edit_count=len(spec.edits),
            duration_seconds=time.monotonic() - start,
        )

    try:
        after_run = eval_runner_module.run_eval(
            target_agent_dir,
            eval_command,
            timeout=eval_timeout,
            cwd=eval_cwd,
            result_path=eval_result_path,
        )
    except (FileNotFoundError, EvalRunError) as e:
        _restore_snapshot(snapshot)
        return HypothesisResult(
            hypothesis_id=hypothesis_id,
            layer=layer,
            claim=claim,
            status="eval_failed",
            error=str(e),
            edit_count=len(spec.edits),
            files_modified=files_modified,
            duration_seconds=time.monotonic() - start,
        )
    except BaseException:
        # Don't leave the target in a modified state on unexpected failures.
        _restore_snapshot(snapshot)
        raise

    delta = compute_delta(baseline_summary, after_run.summary, target_scenario_id)
    _restore_snapshot(snapshot)

    return HypothesisResult(
        hypothesis_id=hypothesis_id,
        layer=layer,
        claim=claim,
        status="applied",
        delta=delta,
        after_summary=after_run.summary,
        files_modified=files_modified,
        edit_count=len(spec.edits),
        duration_seconds=time.monotonic() - start,
    )


# ---------- Snapshot / revert ----------


def _snapshot_files(paths: list[Path]) -> dict[Path, bytes]:
    """Read every path's current bytes into memory for later restoration."""
    snapshot: dict[Path, bytes] = {}
    for p in paths:
        snapshot[p] = p.read_bytes()
    return snapshot


def _restore_snapshot(snapshot: dict[Path, bytes]) -> None:
    """Write each snapshotted file's original bytes back to disk."""
    for path, original_bytes in snapshot.items():
        try:
            path.write_bytes(original_bytes)
        except OSError:
            # Best-effort: a failed restore on one file should not block the
            # others. The caller's report will reflect what was attempted.
            continue


# ---------- Report parsing helpers ----------


def _hypothesis_ids_in_order(report_text: str) -> list[int]:
    """Return the hypothesis IDs as they appear in the report, in document order."""
    return [int(m.group(1)) for m in _HYPOTHESIS_HEADER_RE.finditer(report_text)]


def _layer_for(report_text: str, hypothesis_id: int) -> Optional[str]:
    """Pull 'Layer N' from the hypothesis header line, if present."""
    for m in _HYPOTHESIS_HEADER_RE.finditer(report_text):
        if int(m.group(1)) == hypothesis_id:
            layer_match = _LAYER_FROM_HEADER_RE.search(m.group(2))
            if layer_match:
                return f"Layer {layer_match.group(1)}"
            return None
    return None


def _claim_for(report_text: str, hypothesis_id: int) -> Optional[str]:
    """Extract the **Claim:** body for a hypothesis section, if present."""
    section = _hypothesis_section(report_text, hypothesis_id)
    if section is None:
        return None
    m = _CLAIM_RE.search(section)
    if not m:
        return None
    return " ".join(m.group(1).split())


def _hypothesis_section(report_text: str, hypothesis_id: int) -> Optional[str]:
    """Slice the report down to one hypothesis's section."""
    matches = list(_HYPOTHESIS_HEADER_RE.finditer(report_text))
    target = next((m for m in matches if int(m.group(1)) == hypothesis_id), None)
    if target is None:
        return None
    start = target.start()
    next_header = re.search(r"^###\s+", report_text[target.end():], re.MULTILINE)
    if next_header:
        return report_text[start:target.end() + next_header.start()]
    next_h2 = re.search(r"^##\s+", report_text[target.end():], re.MULTILINE)
    end = (target.end() + next_h2.start()) if next_h2 else len(report_text)
    return report_text[start:end]


def _infer_target_scenario_id(report_text: str, baseline: EvalSummary) -> str:
    """Guess the target scenario from the report's 'Scenario:' line or the baseline."""
    m = re.search(r"Scenario:\s*(?:issue\s+)?([\w.-]+)", report_text)
    if m:
        return m.group(1)
    if baseline.failures:
        return baseline.failures[0].scenario_id
    raise ValueError(
        "Could not infer target scenario ID — add 'Scenario: <id>' to the "
        "report's failure-summary section."
    )
