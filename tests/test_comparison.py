"""Tests for the comparison renderer: best-result picking, table rendering,
per-hypothesis detail blocks, and the no-best fallback summary.
"""

from __future__ import annotations

from agent_researcher.comparison import pick_best, render_iteration_report
from agent_researcher.delta import DeltaReport, ScenarioDelta
from agent_researcher.eval_analyzer import EvalSummary
from agent_researcher.orchestrator import HypothesisResult, IterationReport


# ---------- Builders ----------


def _scenario(sid: str, before: bool, after: bool) -> ScenarioDelta:
    return ScenarioDelta(
        scenario_id=sid,
        before_passed=before,
        after_passed=after,
        before_record={"passed": before},
        after_record={"passed": after},
    )


def _delta(
    *,
    target_id: str,
    before_rate: float,
    after_rate: float,
    target_direction: str,
    other_flips: list[ScenarioDelta] = None,
) -> DeltaReport:
    """Build a DeltaReport with explicit target direction and optional other flips."""
    other_flips = other_flips or []
    if target_direction == "fixed":
        target = _scenario(target_id, before=False, after=True)
    elif target_direction == "broken":
        target = _scenario(target_id, before=True, after=False)
    elif target_direction == "unchanged":
        target = _scenario(target_id, before=False, after=False)
    else:
        target = None

    flipped = list(other_flips)
    if target is not None and target.flipped:
        flipped.append(target)

    per_scenario = list(flipped)
    if target is not None and not target.flipped:
        per_scenario.append(target)

    return DeltaReport(
        target_scenario_id=target_id,
        before_pass_rate=before_rate,
        after_pass_rate=after_rate,
        before_passed=int(before_rate * 10),
        after_passed=int(after_rate * 10),
        total=10,
        target_delta=target,
        flipped=flipped,
        per_scenario=per_scenario,
    )


def _baseline_summary(pass_rate: float = 0.857, passed: int = 6, total: int = 7) -> EvalSummary:
    return EvalSummary(
        total=total,
        passed=passed,
        pass_rate=pass_rate,
        threshold=0.9,
        meets_threshold=False,
        failures=[],
        all_results=[],
    )


def _result(
    hid: int,
    *,
    status: str,
    layer: str = "Layer 1",
    claim: str = "A claim.",
    delta: DeltaReport = None,
    error: str = None,
    skip_reason: str = None,
    edit_count: int = 1,
    files_modified: list[str] = None,
) -> HypothesisResult:
    return HypothesisResult(
        hypothesis_id=hid,
        layer=layer,
        claim=claim,
        status=status,
        delta=delta,
        error=error,
        skip_reason=skip_reason,
        edit_count=edit_count,
        files_modified=files_modified or [],
        duration_seconds=1.5,
    )


def _report(results: list[HypothesisResult], target_id: str = "107") -> IterationReport:
    return IterationReport(
        target_scenario_id=target_id,
        baseline=_baseline_summary(),
        baseline_duration_seconds=2.5,
        results=results,
        total_duration_seconds=12.0,
    )


# ---------- pick_best ----------


def test_pick_best_chooses_improvement_without_regression():
    h1 = _result(1, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=1.0, target_direction="fixed",
    ))
    h2 = _result(2, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=0.857, target_direction="unchanged",
    ))
    best = pick_best(_report([h1, h2]))
    assert best is h1


def test_pick_best_rejects_hypothesis_with_passing_to_failing_flip():
    """A hypothesis that improves the target but breaks another scenario must not win."""
    h1 = _result(1, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=0.857, target_direction="fixed",
        other_flips=[_scenario("103", before=True, after=False)],
    ))
    h2 = _result(2, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=1.0, target_direction="fixed",
    ))
    best = pick_best(_report([h1, h2]))
    assert best is h2


def test_pick_best_ties_break_by_lowest_id():
    """Equal pass_rate_change with no regression → lowest hypothesis_id wins."""
    h1 = _result(2, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=1.0, target_direction="fixed",
    ))
    h2 = _result(1, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=1.0, target_direction="fixed",
    ))
    best = pick_best(_report([h1, h2]))
    assert best.hypothesis_id == 1


def test_pick_best_returns_none_when_no_improvement():
    """If nothing improved the pass rate, no best is declared."""
    h1 = _result(1, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=0.857, target_direction="unchanged",
    ))
    h2 = _result(2, status="apply_failed", error="bad spec")
    h3 = _result(3, status="skipped", skip_reason="needs new file")
    assert pick_best(_report([h1, h2, h3])) is None


def test_pick_best_returns_none_when_only_regressions():
    """A hypothesis that improves but introduces a passing→failing flip is rejected."""
    h1 = _result(1, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=1.0, target_direction="fixed",
        other_flips=[_scenario("103", before=True, after=False)],
    ))
    assert pick_best(_report([h1])) is None


# ---------- render_iteration_report ----------


def test_render_summary_mentions_best_when_one_exists():
    h1 = _result(1, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=1.0, target_direction="fixed",
    ))
    out = render_iteration_report(_report([h1]))
    assert "Best result: H1" in out
    assert "0.857" in out and "1.000" in out
    assert "target scenario fixed" in out
    assert "no regressions" in out


def test_render_summary_says_no_best_when_none_qualify():
    h1 = _result(1, status="apply_failed", error="spec mismatch")
    h2 = _result(2, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=0.857, target_direction="unchanged",
    ))
    out = render_iteration_report(_report([h1, h2]))
    assert "No hypothesis improved the eval" in out
    # No "Best result:" line should appear when no best is declared.
    assert "Best result:" not in out


def test_render_summary_says_no_applyable_when_all_skipped_or_failed():
    h1 = _result(1, status="skipped", skip_reason="needs new file")
    h2 = _result(2, status="apply_failed", error="bad")
    out = render_iteration_report(_report([h1, h2]))
    assert "No applyable hypotheses ran successfully" in out


def test_render_comparison_table_has_one_row_per_hypothesis():
    h1 = _result(1, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=1.0, target_direction="fixed",
    ))
    h2 = _result(2, status="apply_failed", error="bad")
    h3 = _result(3, status="skipped", skip_reason="needs new file")
    h4 = _result(4, status="eval_failed", error="subprocess crashed")
    out = render_iteration_report(_report([h1, h2, h3, h4]))

    # The header row plus one row per hypothesis.
    table_lines = [
        line for line in out.splitlines()
        if line.startswith("| H") or line.startswith("| Hypothesis")
    ]
    assert len(table_lines) == 5  # header + 4 hypotheses

    assert "| H1 | Layer 1 | applied |" in out
    assert "| H2 | Layer 1 | apply failed |" in out
    assert "| H3 | Layer 1 | skipped |" in out
    assert "| H4 | Layer 1 | eval failed |" in out


def test_render_table_pass_rate_change_only_for_applied():
    """Non-applied rows show '—' in the pass-rate-Δ column."""
    h_apply = _result(1, status="applied", delta=_delta(
        target_id="107", before_rate=0.5, after_rate=0.5, target_direction="unchanged",
    ))
    h_skip = _result(2, status="skipped", skip_reason="needs new tool")
    h_fail = _result(3, status="apply_failed", error="mismatch")
    out = render_iteration_report(_report([h_apply, h_skip, h_fail]))
    lines = out.splitlines()
    h2_row = next(line for line in lines if line.startswith("| H2 |"))
    h3_row = next(line for line in lines if line.startswith("| H3 |"))
    # Non-applied rows show '—' for pass-rate-Δ, target, and other-flips.
    assert h2_row.count("—") >= 3
    assert h3_row.count("—") >= 3


def test_render_per_hypothesis_block_for_applied():
    h1 = _result(
        1,
        status="applied",
        delta=_delta(
            target_id="107", before_rate=0.857, after_rate=1.0, target_direction="fixed",
        ),
        files_modified=["prompts/classification.j2"],
    )
    out = render_iteration_report(_report([h1]))
    assert "### H1 (Layer 1) — applied" in out
    assert "Claim:" in out
    assert "Pass rate: 0.857 → 1.000" in out
    assert "target scenario fixed" in out
    assert "Edits applied: 1" in out
    assert "classification.j2" in out


def test_render_per_hypothesis_block_for_skipped():
    h = _result(1, status="skipped", skip_reason="requires a new tool file")
    out = render_iteration_report(_report([h]))
    assert "### H1 (Layer 1) — skipped" in out
    assert "Skipped." in out
    assert "requires a new tool file" in out


def test_render_per_hypothesis_block_for_apply_failed():
    h = _result(1, status="apply_failed", error="expected_content does not match")
    out = render_iteration_report(_report([h]))
    assert "### H1 (Layer 1) — apply failed" in out
    assert "Apply failed." in out
    assert "Files were not left modified" in out
    assert "expected_content does not match" in out


def test_render_per_hypothesis_block_for_eval_failed():
    h = _result(1, status="eval_failed", error="subprocess timed out after 300s")
    out = render_iteration_report(_report([h]))
    assert "### H1 (Layer 1) — eval failed" in out
    assert "Eval failed." in out
    assert "reverted from snapshot" in out
    assert "subprocess timed out" in out


def test_render_other_flips_listed_when_present():
    """If an applied hypothesis caused other scenarios to flip, those are shown."""
    h = _result(1, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=0.857, target_direction="fixed",
        other_flips=[
            _scenario("103", before=True, after=False),
            _scenario("104", before=False, after=True),
        ],
    ))
    out = render_iteration_report(_report([h]))
    assert "`103` (broken)" in out
    assert "`104` (fixed)" in out


def test_render_other_flips_says_none_when_clean():
    h = _result(1, status="applied", delta=_delta(
        target_id="107", before_rate=0.857, after_rate=1.0, target_direction="fixed",
    ))
    out = render_iteration_report(_report([h]))
    assert "Other flips: none" in out
