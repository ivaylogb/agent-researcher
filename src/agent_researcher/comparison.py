"""Render an IterationReport into a markdown comparison report.

The output has three parts: a one-line "best result" summary, a comparison
table over every hypothesis, and per-hypothesis detail blocks. The summary
declares a "best" only when at least one hypothesis improved the pass rate
without causing any passing scenario to fail; otherwise it says so plainly.
"""

from __future__ import annotations

from typing import Optional

from .delta import DeltaReport, ScenarioDelta
from .orchestrator import HypothesisResult, IterationReport


_STATUS_DISPLAY = {
    "applied": "applied",
    "skipped": "skipped",
    "apply_failed": "apply failed",
    "eval_failed": "eval failed",
}


# ---------- Entry point ----------


def render_iteration_report(report: IterationReport) -> str:
    """Render an IterationReport into a markdown document."""
    parts: list[str] = []
    parts.append(f"# Iteration report: scenario {report.target_scenario_id}")
    parts.append("")
    parts.append(_summary_block(report))
    parts.append("")
    parts.append(_baseline_block(report))
    parts.append("")
    parts.append(_comparison_table(report))
    parts.append("")
    parts.append(_per_hypothesis_blocks(report))
    parts.append("")
    parts.append(_runtime_block(report))
    return "\n".join(parts).rstrip() + "\n"


# ---------- Best-result picking ----------


def pick_best(report: IterationReport) -> Optional[HypothesisResult]:
    """Return the best hypothesis, or None if no applied hypothesis qualifies.

    A hypothesis qualifies if it was applied successfully, improved the pass
    rate strictly (pass_rate_change > 0), and caused no passing scenario to
    flip to failing. Among qualifying hypotheses the one with the largest
    pass_rate_change wins; ties go to the lowest hypothesis_id.
    """
    candidates: list[HypothesisResult] = []
    for r in report.results:
        if r.status != "applied" or r.delta is None:
            continue
        if r.delta.pass_rate_change <= 0:
            continue
        if any(d.direction == "broken" for d in r.delta.flipped):
            continue
        candidates.append(r)

    if not candidates:
        return None

    candidates.sort(key=lambda r: (-r.delta.pass_rate_change, r.hypothesis_id))
    return candidates[0]


# ---------- Section renderers ----------


def _summary_block(report: IterationReport) -> str:
    best = pick_best(report)
    if best is None:
        applied_count = sum(1 for r in report.results if r.status == "applied")
        if applied_count == 0:
            return (
                "## Summary\n\n"
                "No applyable hypotheses ran successfully — see per-hypothesis "
                "detail for the failure or skip reasons."
            )
        return (
            "## Summary\n\n"
            "No hypothesis improved the eval without regressions. "
            f"{applied_count} hypothesis(es) ran; see the table below for what each one did."
        )

    delta = best.delta
    assert delta is not None  # pick_best filters for non-None delta
    target_str = _target_clause(delta)
    other_flips = _non_target_flips(delta)
    if not other_flips:
        regression_clause = "no regressions"
    else:
        regression_clause = f"{len(other_flips)} other flip(s)"
    return (
        "## Summary\n\n"
        f"Best result: H{best.hypothesis_id} "
        f"(pass_rate {delta.before_pass_rate:.3f} → {delta.after_pass_rate:.3f}, "
        f"{target_str}, {regression_clause})."
    )


def _baseline_block(report: IterationReport) -> str:
    b = report.baseline
    return (
        "## Baseline\n\n"
        f"- Pass rate: {b.pass_rate:.3f} ({b.passed}/{b.total})\n"
        f"- Target scenario: `{report.target_scenario_id}`\n"
        f"- Baseline eval runtime: {report.baseline_duration_seconds:.1f}s"
    )


def _comparison_table(report: IterationReport) -> str:
    lines = [
        "## Hypothesis comparison",
        "",
        "| Hypothesis | Layer | Status | Pass-rate Δ | Target | Other flips |",
        "|---|---|---|---|---|---|",
    ]
    for r in report.results:
        lines.append(_table_row(r))
    return "\n".join(lines)


def _table_row(r: HypothesisResult) -> str:
    layer = r.layer or "—"
    status = _STATUS_DISPLAY.get(r.status, r.status)

    if r.status == "applied" and r.delta is not None:
        change = r.delta.pass_rate_change
        arrow = "↑" if change > 0 else ("↓" if change < 0 else "·")
        pass_rate_cell = f"{arrow} {change:+.3f}"
        target_cell = _target_cell(r.delta)
        other_flips = _non_target_flips(r.delta)
        other_cell = _other_flips_cell(other_flips)
    else:
        pass_rate_cell = "—"
        target_cell = "—"
        other_cell = "—"

    return f"| H{r.hypothesis_id} | {layer} | {status} | {pass_rate_cell} | {target_cell} | {other_cell} |"


def _per_hypothesis_blocks(report: IterationReport) -> str:
    parts = ["## Per-hypothesis detail"]
    for r in report.results:
        parts.append("")
        parts.append(_one_hypothesis_block(r))
    return "\n".join(parts)


def _one_hypothesis_block(r: HypothesisResult) -> str:
    header = f"### H{r.hypothesis_id}"
    if r.layer:
        header += f" ({r.layer})"
    header += f" — {_STATUS_DISPLAY.get(r.status, r.status)}"

    lines = [header, ""]
    if r.claim:
        lines.append(f"**Claim:** {r.claim}")
        lines.append("")

    if r.status == "applied" and r.delta is not None:
        lines.append(_applied_detail(r))
    elif r.status == "skipped":
        reason = r.skip_reason or "(no reason given)"
        lines.append(f"**Skipped.** {reason}")
    elif r.status == "apply_failed":
        lines.append("**Apply failed.** No eval ran. Files were not left modified.")
        if r.error:
            lines.append("")
            lines.append("```")
            lines.append(r.error)
            lines.append("```")
    elif r.status == "eval_failed":
        lines.append(
            "**Eval failed.** Edits were applied, then reverted from snapshot "
            "after the eval subprocess errored."
        )
        if r.error:
            lines.append("")
            lines.append("```")
            lines.append(r.error)
            lines.append("```")

    return "\n".join(lines).rstrip()


def _applied_detail(r: HypothesisResult) -> str:
    assert r.delta is not None
    d = r.delta
    arrow = "↑" if d.pass_rate_change > 0 else ("↓" if d.pass_rate_change < 0 else "·")
    target_str = _target_clause(d)
    other_flips = _non_target_flips(d)

    lines = [
        f"- Pass rate: {d.before_pass_rate:.3f} → {d.after_pass_rate:.3f} "
        f"({arrow} {d.pass_rate_change:+.3f})",
        f"- Target scenario `{d.target_scenario_id}`: {target_str}",
        f"- Edits applied: {r.edit_count}",
        f"- Duration: {r.duration_seconds:.1f}s",
    ]
    if other_flips:
        lines.append("- Other flips:")
        for f in other_flips:
            marker = "✓" if f.direction == "fixed" else "✗"
            lines.append(f"  - {marker} `{f.scenario_id}` ({f.direction})")
    else:
        lines.append("- Other flips: none")

    if r.files_modified:
        lines.append("- Files modified during this iteration:")
        for f in r.files_modified:
            lines.append(f"  - `{f}`")

    return "\n".join(lines)


def _runtime_block(report: IterationReport) -> str:
    return (
        "## Runtime\n\n"
        f"- Total: {report.total_duration_seconds:.1f}s\n"
        f"- Baseline: {report.baseline_duration_seconds:.1f}s\n"
        f"- Hypotheses processed: {len(report.results)}"
    )


# ---------- Helpers ----------


def _target_cell(delta: DeltaReport) -> str:
    if delta.target_delta is None:
        return "missing"
    return delta.target_delta.direction


def _target_clause(delta: DeltaReport) -> str:
    if delta.target_delta is None:
        return "target scenario not in eval"
    return f"target scenario {delta.target_delta.direction}"


def _non_target_flips(delta: DeltaReport) -> list[ScenarioDelta]:
    return [
        d for d in delta.flipped if d.scenario_id != delta.target_scenario_id
    ]


def _other_flips_cell(flips: list[ScenarioDelta]) -> str:
    if not flips:
        return "0"
    broken = sum(1 for f in flips if f.direction == "broken")
    fixed = sum(1 for f in flips if f.direction == "fixed")
    bits = []
    if fixed:
        bits.append(f"+{fixed}")
    if broken:
        bits.append(f"-{broken}")
    return ", ".join(bits) if bits else str(len(flips))
