"""Tests for delta computation and markdown rendering.

Build EvalSummary objects in-process; no eval subprocess is invoked here.
"""

from __future__ import annotations

from agent_researcher.delta import compute_delta, render_delta_markdown
from agent_researcher.eval_analyzer import EvalSummary


def _summary(results: list[dict]) -> EvalSummary:
    """Build an EvalSummary that matches what eval_analyzer would produce."""
    passed = sum(1 for r in results if r.get("passed"))
    total = len(results)
    failures: list = []  # delta only reads `all_results` — fine to leave empty.
    return EvalSummary(
        total=total,
        passed=passed,
        pass_rate=(passed / total) if total else 0.0,
        threshold=0.9,
        meets_threshold=(passed / total) >= 0.9 if total else False,
        failures=failures,
        all_results=results,
    )


def _record(issue: int, expected: str, predicted: str, passed: bool, conf: float = 0.9) -> dict:
    return {
        "issue_number": issue,
        "expected_intent": expected,
        "predicted_intent": predicted,
        "predicted_confidence": conf,
        "passed": passed,
        "notes": "",
    }


# ---------- compute_delta ----------


def test_compute_delta_detects_pass_rate_change() -> None:
    before = _summary([
        _record(101, "bug", "bug", True),
        _record(107, "unknown", "bug", False),
    ])
    after = _summary([
        _record(101, "bug", "bug", True),
        _record(107, "unknown", "unknown", True),
    ])
    delta = compute_delta(before, after, target_scenario_id="107")
    assert delta.before_pass_rate == 0.5
    assert delta.after_pass_rate == 1.0
    assert delta.pass_rate_change == 0.5
    assert delta.before_passed == 1
    assert delta.after_passed == 2


def test_compute_delta_identifies_target_scenario_flip() -> None:
    before = _summary([
        _record(107, "unknown", "bug", False),
    ])
    after = _summary([
        _record(107, "unknown", "unknown", True),
    ])
    delta = compute_delta(before, after, target_scenario_id="107")
    assert delta.target_delta is not None
    assert delta.target_delta.direction == "fixed"
    assert delta.target_delta.before_passed is False
    assert delta.target_delta.after_passed is True


def test_compute_delta_target_scenario_unchanged() -> None:
    """If the target scenario didn't move, direction='unchanged' and the
    report should still be coherent — the delta doesn't editorialize."""
    before = _summary([_record(107, "unknown", "bug", False)])
    after = _summary([_record(107, "unknown", "bug", False)])
    delta = compute_delta(before, after, target_scenario_id="107")
    assert delta.target_delta.direction == "unchanged"
    assert delta.pass_rate_change == 0.0


def test_compute_delta_surfaces_other_scenario_flips() -> None:
    """A hypothesis that fixes the target but breaks another scenario must be
    visible — that's the whole point of running the full eval."""
    before = _summary([
        _record(101, "bug", "bug", True),       # passing
        _record(104, "security", "security", True),  # passing
        _record(107, "unknown", "bug", False),  # target failure
    ])
    after = _summary([
        _record(101, "bug", "bug", True),       # still passing
        _record(104, "security", "bug", False), # BROKEN — collateral damage
        _record(107, "unknown", "unknown", True),  # target fixed
    ])
    delta = compute_delta(before, after, target_scenario_id="107")
    flipped_ids = sorted(d.scenario_id for d in delta.flipped)
    assert flipped_ids == ["104", "107"]
    sec_delta = next(d for d in delta.flipped if d.scenario_id == "104")
    assert sec_delta.direction == "broken"


def test_compute_delta_per_scenario_covers_all_scenarios() -> None:
    before = _summary([
        _record(101, "bug", "bug", True),
        _record(107, "unknown", "bug", False),
    ])
    after = _summary([
        _record(101, "bug", "bug", True),
        _record(107, "unknown", "unknown", True),
    ])
    delta = compute_delta(before, after, target_scenario_id="107")
    ids = [d.scenario_id for d in delta.per_scenario]
    # Numeric-aware sort: 101 before 107.
    assert ids == ["101", "107"]


def test_compute_delta_handles_added_or_removed_scenarios() -> None:
    """Golden set drift between runs is rare but must not crash the delta."""
    before = _summary([_record(101, "bug", "bug", True)])
    after = _summary([
        _record(101, "bug", "bug", True),
        _record(200, "feature", "feature", True),  # added
    ])
    delta = compute_delta(before, after, target_scenario_id="101")
    added = [d for d in delta.per_scenario if d.direction == "added"]
    assert len(added) == 1 and added[0].scenario_id == "200"


def test_compute_delta_missing_target_scenario() -> None:
    """If the target scenario isn't in either run, target_delta is None — but
    the rest of the report must still be valid."""
    before = _summary([_record(101, "bug", "bug", True)])
    after = _summary([_record(101, "bug", "bug", True)])
    delta = compute_delta(before, after, target_scenario_id="999")
    assert delta.target_delta is None
    assert delta.pass_rate_change == 0.0


# ---------- render_delta_markdown ----------


def test_render_delta_markdown_is_parseable() -> None:
    """The rendered output must include the headers operators expect."""
    before = _summary([
        _record(101, "bug", "bug", True),
        _record(107, "unknown", "bug", False),
    ])
    after = _summary([
        _record(101, "bug", "bug", True),
        _record(107, "unknown", "unknown", True),
    ])
    delta = compute_delta(before, after, target_scenario_id="107")

    md = render_delta_markdown(
        delta,
        hypothesis_summary="**H1**\n\nA test claim.",
        files_modified=["/fake/agent/prompts/classification.j2"],
    )

    # Required sections
    assert "# Apply-and-re-eval delta" in md
    assert "## Hypothesis applied" in md
    assert "## Summary" in md
    assert "## Target scenario" in md
    assert "## Other flips" in md
    assert "## Per-scenario state" in md
    assert "## How to revert" in md

    # Hypothesis summary appears
    assert "A test claim." in md

    # Summary table shows the delta
    assert "0.500" in md
    assert "1.000" in md
    assert "+0.500" in md

    # Target scenario fixed
    assert "FIXED" in md

    # Revert section lists the file
    assert "/fake/agent/prompts/classification.j2" in md


def test_render_delta_markdown_target_broken_shows_clearly() -> None:
    """If the hypothesis BROKE the target instead of fixing it, the report
    must say so unambiguously — operators rely on this verdict."""
    before = _summary([_record(107, "unknown", "unknown", True)])
    after = _summary([_record(107, "unknown", "bug", False)])
    delta = compute_delta(before, after, target_scenario_id="107")
    md = render_delta_markdown(delta, hypothesis_summary="**H1**\n\nClaim.")
    assert "BROKEN" in md


def test_render_delta_markdown_omits_revert_section_when_no_files() -> None:
    """Dry-run / no-write scenarios should not pretend the operator has files
    to revert."""
    before = _summary([_record(107, "unknown", "bug", False)])
    after = _summary([_record(107, "unknown", "unknown", True)])
    delta = compute_delta(before, after, target_scenario_id="107")
    md = render_delta_markdown(delta, hypothesis_summary="x", files_modified=None)
    assert "How to revert" not in md
