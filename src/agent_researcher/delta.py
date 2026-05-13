"""Compute and render the before/after delta for an apply-and-re-eval run.

After Phase 2 applies a hypothesis's edits to the target agent and re-runs the
eval, it needs a concise structured comparison of the two runs. This module:

1. Diffs two EvalSummary objects on a per-scenario basis.
2. Highlights the target scenario the operator was investigating.
3. Surfaces collateral damage — scenarios that flipped in either direction.
4. Renders a markdown report consumable both as stdout and as a saved artifact.

The delta does not pass judgment ("this hypothesis is correct/wrong"). It
reports facts; the operator decides whether to keep the edits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .eval_analyzer import EvalSummary


@dataclass
class ScenarioDelta:
    """One scenario's before/after state."""

    scenario_id: str
    before_passed: Optional[bool]  # None if scenario was added between runs
    after_passed: Optional[bool]   # None if scenario was removed between runs
    before_record: Optional[dict[str, Any]]
    after_record: Optional[dict[str, Any]]

    @property
    def flipped(self) -> bool:
        """True if pass/fail status changed (and scenario exists in both runs)."""
        if self.before_passed is None or self.after_passed is None:
            return False
        return self.before_passed != self.after_passed

    @property
    def direction(self) -> str:
        """'fixed', 'broken', 'unchanged', or 'added'/'removed'."""
        if self.before_passed is None:
            return "added"
        if self.after_passed is None:
            return "removed"
        if self.before_passed == self.after_passed:
            return "unchanged"
        return "fixed" if (not self.before_passed and self.after_passed) else "broken"


@dataclass
class DeltaReport:
    """Structured result of comparing two eval runs."""

    target_scenario_id: str
    before_pass_rate: float
    after_pass_rate: float
    before_passed: int
    after_passed: int
    total: int
    target_delta: Optional[ScenarioDelta]
    flipped: list[ScenarioDelta] = field(default_factory=list)
    per_scenario: list[ScenarioDelta] = field(default_factory=list)

    @property
    def pass_rate_change(self) -> float:
        return self.after_pass_rate - self.before_pass_rate


def compute_delta(
    before: EvalSummary,
    after: EvalSummary,
    target_scenario_id: str,
) -> DeltaReport:
    """Diff two eval runs into a structured DeltaReport.

    Scenarios are matched by the same scenario_id derivation used by
    EvalFailure (issue_number → scenario_id → id → "unknown"). If a scenario
    appears in only one run, it shows up as 'added' or 'removed'.
    """
    before_by_id = {_scenario_id(r): r for r in before.all_results}
    after_by_id = {_scenario_id(r): r for r in after.all_results}

    all_ids = sorted(set(before_by_id) | set(after_by_id), key=_sort_key)

    per_scenario: list[ScenarioDelta] = []
    for sid in all_ids:
        b = before_by_id.get(sid)
        a = after_by_id.get(sid)
        per_scenario.append(
            ScenarioDelta(
                scenario_id=sid,
                before_passed=(b.get("passed") if b is not None else None),
                after_passed=(a.get("passed") if a is not None else None),
                before_record=b,
                after_record=a,
            )
        )

    flipped = [d for d in per_scenario if d.flipped]
    target_delta = next(
        (d for d in per_scenario if d.scenario_id == str(target_scenario_id)),
        None,
    )

    # Use the larger of the two totals as the displayed denominator — neither
    # run is strictly "the truth" if the golden set drifted.
    total = max(before.total, after.total)

    return DeltaReport(
        target_scenario_id=str(target_scenario_id),
        before_pass_rate=before.pass_rate,
        after_pass_rate=after.pass_rate,
        before_passed=before.passed,
        after_passed=after.passed,
        total=total,
        target_delta=target_delta,
        flipped=flipped,
        per_scenario=per_scenario,
    )


def _scenario_id(record: dict[str, Any]) -> str:
    """Match the convention in eval_analyzer._record_to_failure."""
    return str(
        record.get("issue_number")
        or record.get("scenario_id")
        or record.get("id")
        or "unknown"
    )


def _sort_key(sid: str) -> tuple[int, str]:
    """Numeric-aware sort so issue_number IDs come back in numeric order."""
    try:
        return (0, f"{int(sid):020d}")
    except ValueError:
        return (1, sid)


# ---------- Markdown rendering ----------


def render_delta_markdown(
    delta: DeltaReport,
    hypothesis_summary: str,
    *,
    files_modified: Optional[list[str]] = None,
) -> str:
    """Render a DeltaReport into a markdown report.

    Args:
        delta: the structured delta.
        hypothesis_summary: a one-paragraph summary of which hypothesis was
            applied (claim + layer). Goes near the top so the report is
            readable standalone.
        files_modified: optional list of file paths the applier wrote. Used to
            produce the "How to revert" section. If None, the section is
            omitted (operator knows what they applied).
    """
    parts: list[str] = []
    parts.append(f"# Apply-and-re-eval delta: scenario {delta.target_scenario_id}")
    parts.append("")
    parts.append("## Hypothesis applied")
    parts.append("")
    parts.append(hypothesis_summary.strip())
    parts.append("")
    parts.append("## Summary")
    parts.append("")
    parts.append(_summary_table(delta))
    parts.append("")
    parts.append(_target_section(delta))
    parts.append("")
    parts.append(_flips_section(delta))
    parts.append("")
    parts.append(_per_scenario_section(delta))
    if files_modified:
        parts.append("")
        parts.append(_revert_section(files_modified))
    return "\n".join(parts).rstrip() + "\n"


def _summary_table(delta: DeltaReport) -> str:
    change = delta.pass_rate_change
    arrow = "↑" if change > 0 else ("↓" if change < 0 else "·")
    return (
        "| Metric | Before | After | Δ |\n"
        "|---|---|---|---|\n"
        f"| Pass rate | {delta.before_pass_rate:.3f} | {delta.after_pass_rate:.3f} | {arrow} {change:+.3f} |\n"
        f"| Passed / total | {delta.before_passed} / {delta.total} | {delta.after_passed} / {delta.total} | {arrow} {delta.after_passed - delta.before_passed:+d} |"
    )


def _target_section(delta: DeltaReport) -> str:
    if delta.target_delta is None:
        return (
            "## Target scenario\n\n"
            f"Scenario `{delta.target_scenario_id}` is not present in either run "
            "— check the scenario_id, the eval may have skipped it."
        )
    td = delta.target_delta
    before_intent = _intent_str(td.before_record)
    after_intent = _intent_str(td.after_record)
    direction = td.direction
    verdict = {
        "fixed": "✓ FIXED — the target scenario now passes.",
        "broken": "✗ BROKEN — the target scenario was passing and now fails.",
        "unchanged": (
            "· UNCHANGED — the target scenario's pass/fail status did not change. "
            "The hypothesis did not move this specific case."
        ),
        "added": "? ADDED — the scenario appeared only in the after run.",
        "removed": "? REMOVED — the scenario appeared only in the before run.",
    }[direction]

    return (
        f"## Target scenario ({delta.target_scenario_id})\n\n"
        f"{verdict}\n\n"
        f"- Before: {before_intent}\n"
        f"- After:  {after_intent}"
    )


def _flips_section(delta: DeltaReport) -> str:
    if not delta.flipped:
        return "## Other flips\n\nNo other scenarios changed pass/fail status."
    lines = ["## Other flips", ""]
    for d in delta.flipped:
        if d.scenario_id == delta.target_scenario_id:
            continue
        marker = "✓" if d.direction == "fixed" else "✗"
        lines.append(
            f"- {marker} `{d.scenario_id}` ({d.direction}): "
            f"{_intent_str(d.before_record)} → {_intent_str(d.after_record)}"
        )
    # If the target scenario was the only flip, say so explicitly.
    if all(d.scenario_id == delta.target_scenario_id for d in delta.flipped):
        lines.append("No other scenarios flipped.")
    return "\n".join(lines)


def _per_scenario_section(delta: DeltaReport) -> str:
    lines = [
        "## Per-scenario state",
        "",
        "| Scenario | Before | After | Status |",
        "|---|---|---|---|",
    ]
    for d in delta.per_scenario:
        before = _intent_str(d.before_record)
        after = _intent_str(d.after_record)
        status = {
            "fixed": "fixed",
            "broken": "broken",
            "unchanged": "—",
            "added": "added",
            "removed": "removed",
        }[d.direction]
        lines.append(f"| `{d.scenario_id}` | {before} | {after} | {status} |")
    return "\n".join(lines)


def _revert_section(files_modified: list[str]) -> str:
    lines = ["## How to revert", ""]
    lines.append(
        "The applier did NOT revert these edits. The operator decides whether "
        "to keep them. Files written by this run:"
    )
    lines.append("")
    for f in files_modified:
        lines.append(f"- `{f}`")
    lines.append("")
    lines.append(
        "To revert, run `git checkout HEAD --` followed by the file paths "
        "above (inside the target agent's git repo, if any), or restore from "
        "your own backup."
    )
    return "\n".join(lines)


def _intent_str(record: Optional[dict[str, Any]]) -> str:
    """Compact one-line view of a scenario's intent + confidence + pass state."""
    if record is None:
        return "(missing)"
    expected = record.get("expected_intent") or record.get("expected") or "?"
    predicted = record.get("predicted_intent") or record.get("predicted") or "?"
    conf = record.get("predicted_confidence")
    passed = record.get("passed")
    pass_mark = "pass" if passed else "fail"
    conf_str = f" @ {conf:.2f}" if isinstance(conf, (int, float)) else ""
    return f"expected={expected}, predicted={predicted}{conf_str} ({pass_mark})"
