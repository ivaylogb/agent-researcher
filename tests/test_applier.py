"""Tests for the applier: report parsing, all four edit actions, verbatim
verification, multi-edit composition, dry-run, and the applyable:false path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_researcher.applier import (
    EditSpec,
    apply_edits,
    parse_hypothesis_report,
)


# ---------- Helpers ----------


def _write_report(path: Path, hypotheses: list[tuple[int, str, str]]) -> None:
    """Write a minimal hypothesis report containing the given (id, header_suffix, json_block) tuples."""
    parts = ["# Hypothesis report: fixture\n\n## Hypotheses\n\n"]
    for hid, suffix, json_block in hypotheses:
        parts.append(f"### Hypothesis {hid}: {suffix}\n\n")
        parts.append("**Claim:** A test claim.\n\n")
        parts.append("**Evidence:**\n- foo.py:1: trivial\n\n")
        parts.append("**Proposed change:** Apply the edits below.\n\n")
        parts.append(f"```json\n{json_block}\n```\n\n")
        parts.append("**How to verify:** trivially.\n\n")
    parts.append("## What this report is NOT\n\n- Not actionable yet.\n")
    path.write_text("".join(parts))


# ---------- parse_hypothesis_report ----------


def test_parse_hypothesis_report_extracts_correct_id(tmp_path: Path) -> None:
    """The parser must pick the json block belonging to the requested id, not the first one in the file."""
    report = tmp_path / "report.md"
    _write_report(
        report,
        [
            (
                1,
                "H1 (Layer 3)",
                '{"applyable": true, "edits": ['
                '{"file":"f.txt","action":"delete","from_line_start":1,'
                '"from_line_end":1,"expected_content":"line one"}]}',
            ),
            (
                2,
                "H2 (Layer 1)",
                '{"applyable": false, "reason": "not in-place"}',
            ),
        ],
    )

    spec1 = parse_hypothesis_report(report, 1)
    spec2 = parse_hypothesis_report(report, 2)

    assert spec1.applyable is True
    assert len(spec1.edits) == 1
    assert spec1.edits[0].action == "delete"

    assert spec2.applyable is False
    assert spec2.reason == "not in-place"


def test_parse_hypothesis_report_raises_for_missing_id(tmp_path: Path) -> None:
    report = tmp_path / "r.md"
    _write_report(
        report,
        [(1, "only one", '{"applyable": false, "reason": "x"}')],
    )
    with pytest.raises(ValueError, match="not found"):
        parse_hypothesis_report(report, 9)


def test_parse_hypothesis_report_raises_for_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_hypothesis_report(tmp_path / "nope.md", 1)


def test_parse_hypothesis_report_raises_when_no_json_block(tmp_path: Path) -> None:
    """A v3-style report (no structured block) must be detected, not silently parsed."""
    report = tmp_path / "v3.md"
    report.write_text(
        "# Hypothesis report\n\n"
        "### Hypothesis 1: name\n\n"
        "**Claim:** something.\n\n"
        "**Proposed change:** no structured block here.\n"
    )
    with pytest.raises(ValueError, match="no fenced"):
        parse_hypothesis_report(report, 1)


def test_parse_hypothesis_report_raises_on_invalid_json(tmp_path: Path) -> None:
    report = tmp_path / "bad.md"
    _write_report(report, [(1, "bad", "{not valid json")])
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_hypothesis_report(report, 1)


def test_parse_hypothesis_report_validates_required_fields(tmp_path: Path) -> None:
    """An edit missing a required field for its action must fail at parse time, not at apply time."""
    report = tmp_path / "r.md"
    _write_report(
        report,
        [(1, "missing-fields", '{"applyable": true, "edits": [{"action":"replace","file":"a.txt"}]}')],
    )
    with pytest.raises(ValueError, match="missing required field"):
        parse_hypothesis_report(report, 1)


# ---------- apply_edits: individual actions ----------


def _agent_with_file(root: Path, name: str, content: str) -> Path:
    agent = root / "agent"
    (agent / "prompts").mkdir(parents=True)
    (agent / "prompts" / name).write_text(content)
    return agent


def test_apply_edits_replace(tmp_path: Path) -> None:
    agent = _agent_with_file(tmp_path, "x.j2", "line one\nline two\nline three\n")
    spec = EditSpec(
        applyable=True,
        edits=[
            _make_edit(
                action="replace",
                file="x.j2",
                from_line_start=2,
                from_line_end=2,
                expected_content="line two",
                new_content="REPLACED TWO",
            )
        ],
    )
    changes = apply_edits(agent, spec)
    assert len(changes) == 1 and changes[0].changed
    assert (agent / "prompts" / "x.j2").read_text() == "line one\nREPLACED TWO\nline three\n"


def test_apply_edits_insert_after(tmp_path: Path) -> None:
    agent = _agent_with_file(tmp_path, "x.j2", "a\nb\nc\n")
    spec = EditSpec(
        applyable=True,
        edits=[
            _make_edit(
                action="insert_after",
                file="x.j2",
                at_line=2,
                new_content="INSERTED",
            )
        ],
    )
    apply_edits(agent, spec)
    assert (agent / "prompts" / "x.j2").read_text() == "a\nb\nINSERTED\nc\n"


def test_apply_edits_delete(tmp_path: Path) -> None:
    agent = _agent_with_file(tmp_path, "x.j2", "a\nb\nc\n")
    spec = EditSpec(
        applyable=True,
        edits=[
            _make_edit(
                action="delete",
                file="x.j2",
                from_line_start=2,
                from_line_end=2,
                expected_content="b",
            )
        ],
    )
    apply_edits(agent, spec)
    assert (agent / "prompts" / "x.j2").read_text() == "a\nc\n"


def test_apply_edits_move_up(tmp_path: Path) -> None:
    """Move line 4 to after line 1 — exercises the H1-style move pattern."""
    agent = _agent_with_file(tmp_path, "x.j2", "a\nb\nc\nd\ne\n")
    spec = EditSpec(
        applyable=True,
        edits=[
            _make_edit(
                action="move",
                file="x.j2",
                from_line_start=4,
                from_line_end=4,
                to_line=1,
                expected_content="d",
            )
        ],
    )
    apply_edits(agent, spec)
    assert (agent / "prompts" / "x.j2").read_text() == "a\nd\nb\nc\ne\n"


def test_apply_edits_move_down(tmp_path: Path) -> None:
    """Move line 2 to after line 4 — verifies to_line semantics for moves down."""
    agent = _agent_with_file(tmp_path, "x.j2", "a\nb\nc\nd\ne\n")
    spec = EditSpec(
        applyable=True,
        edits=[
            _make_edit(
                action="move",
                file="x.j2",
                from_line_start=2,
                from_line_end=2,
                to_line=4,
                expected_content="b",
            )
        ],
    )
    apply_edits(agent, spec)
    # Original line 2 ('b') moves to right after original line 4 ('d').
    assert (agent / "prompts" / "x.j2").read_text() == "a\nc\nd\nb\ne\n"


# ---------- apply_edits: verification + composition ----------


def test_apply_edits_raises_on_expected_content_mismatch(tmp_path: Path) -> None:
    """The applier must refuse if the file drifted from what the model saw."""
    agent = _agent_with_file(tmp_path, "x.j2", "real one\nreal two\n")
    spec = EditSpec(
        applyable=True,
        edits=[
            _make_edit(
                action="delete",
                file="x.j2",
                from_line_start=1,
                from_line_end=1,
                expected_content="STALE EXPECTATION",
            )
        ],
    )
    with pytest.raises(ValueError, match="expected_content"):
        apply_edits(agent, spec)
    # No write happened.
    assert (agent / "prompts" / "x.j2").read_text() == "real one\nreal two\n"


def test_apply_edits_multi_edit_bottom_up(tmp_path: Path) -> None:
    """The H1-style pattern: delete at the bottom, insert near the top.

    Both edits address ORIGINAL line numbers; the applier must compose them
    correctly without shifting the second edit's line numbers.
    """
    agent = _agent_with_file(
        tmp_path,
        "classification.j2",
        # 10 lines, where line 10 is the rule to move up.
        "intent: bug\n"           # 1
        "intent: feature\n"       # 2
        "intent: docs\n"          # 3
        "\n"                      # 4
        "Confidence:\n"           # 5
        "  0.9\n"                 # 6
        "  0.7\n"                 # 7
        "\n"                      # 8
        "Notes:\n"                # 9
        "- mixed signals rule\n", # 10
    )
    spec = EditSpec(
        applyable=True,
        edits=[
            _make_edit(
                action="delete",
                file="classification.j2",
                from_line_start=10,
                from_line_end=10,
                expected_content="- mixed signals rule",
            ),
            _make_edit(
                action="insert_after",
                file="classification.j2",
                at_line=3,
                new_content="\n**Before scoring:** check mixed-signals rule.",
            ),
        ],
    )
    apply_edits(agent, spec)
    result = (agent / "prompts" / "classification.j2").read_text()
    assert "**Before scoring:**" in result
    assert "- mixed signals rule" not in result
    # The inserted content should appear after line 3, before the original line 4.
    lines = result.split("\n")
    assert lines[3] == ""
    assert lines[4] == "**Before scoring:** check mixed-signals rule."


def test_apply_edits_dry_run_does_not_write(tmp_path: Path) -> None:
    agent = _agent_with_file(tmp_path, "x.j2", "alpha\nbeta\n")
    original = (agent / "prompts" / "x.j2").read_text()
    spec = EditSpec(
        applyable=True,
        edits=[
            _make_edit(
                action="delete",
                file="x.j2",
                from_line_start=1,
                from_line_end=1,
                expected_content="alpha",
            )
        ],
    )
    changes = apply_edits(agent, spec, dry_run=True)
    assert len(changes) == 1
    assert changes[0].changed  # plan says the file would change
    assert (agent / "prompts" / "x.j2").read_text() == original


def test_apply_edits_overlapping_edits_rejected(tmp_path: Path) -> None:
    """Two edits that try to delete or replace the same line must be rejected."""
    agent = _agent_with_file(tmp_path, "x.j2", "a\nb\nc\n")
    spec = EditSpec(
        applyable=True,
        edits=[
            _make_edit(
                action="delete",
                file="x.j2",
                from_line_start=2,
                from_line_end=2,
                expected_content="b",
            ),
            _make_edit(
                action="replace",
                file="x.j2",
                from_line_start=2,
                from_line_end=2,
                expected_content="b",
                new_content="B",
            ),
        ],
    )
    with pytest.raises(ValueError, match="overlap"):
        apply_edits(agent, spec)


def test_apply_edits_resolves_bare_basename(tmp_path: Path) -> None:
    """Edit `file: "classification.j2"` should resolve to prompts/classification.j2."""
    agent = _agent_with_file(tmp_path, "classification.j2", "x\ny\n")
    spec = EditSpec(
        applyable=True,
        edits=[
            _make_edit(
                action="delete",
                file="classification.j2",  # no directory prefix
                from_line_start=1,
                from_line_end=1,
                expected_content="x",
            )
        ],
    )
    apply_edits(agent, spec)
    assert (agent / "prompts" / "classification.j2").read_text() == "y\n"


def test_apply_edits_raises_for_unknown_file(tmp_path: Path) -> None:
    agent = _agent_with_file(tmp_path, "x.j2", "a\n")
    spec = EditSpec(
        applyable=True,
        edits=[
            _make_edit(
                action="delete",
                file="does-not-exist.j2",
                from_line_start=1,
                from_line_end=1,
                expected_content="a",
            )
        ],
    )
    with pytest.raises(ValueError, match="no such file"):
        apply_edits(agent, spec)


def test_apply_edits_non_applyable_raises(tmp_path: Path) -> None:
    """The applier must not silently no-op on a non-applyable spec — the
    caller has to handle that case explicitly."""
    agent = _agent_with_file(tmp_path, "x.j2", "a\n")
    spec = EditSpec(applyable=False, reason="needs a new file")
    with pytest.raises(ValueError, match="non-applyable"):
        apply_edits(agent, spec)


# ---------- helpers ----------


def _make_edit(**kwargs):
    """Build an Edit while staying tolerant of None defaults."""
    from agent_researcher.applier import Edit
    return Edit(**kwargs)
