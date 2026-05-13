"""Tests for prompt_assembler. Exercises template interpolation, file ordering,
and the resilience flagged in code review (Jinja braces, code-fence collisions).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_researcher.code_reader import TargetAgentSource
from agent_researcher.eval_analyzer import EvalFailure
from agent_researcher.prompt_assembler import (
    _format_additional_files,
    _number_lines,
    build_user_message,
    load_system_prompt,
    load_user_template,
)


def _failure() -> EvalFailure:
    return EvalFailure(
        scenario_id="107",
        expected="unknown",
        predicted="bug",
        predicted_confidence=0.75,
        notes="Mixed signals.",
        raw={
            "issue_number": 107,
            "expected_intent": "unknown",
            "predicted_intent": "bug",
            "predicted_confidence": 0.75,
            "passed": False,
            "notes": "Mixed signals.",
        },
    )


def _target(**overrides) -> TargetAgentSource:
    defaults = dict(
        name="test_agent",
        agent_yaml="name: test_agent\n",
        system_prompt="You are a test agent.",
        classification_prompt="Classify.",
        handoff_prompt=None,
        flow_prompts={},
        tool_sources={},
        runner_source=None,
    )
    defaults.update(overrides)
    return TargetAgentSource(**defaults)


def test_system_prompt_and_user_template_load() -> None:
    sys_prompt = load_system_prompt()
    user_tmpl = load_user_template()
    assert len(sys_prompt) > 500
    assert len(user_tmpl) > 200
    assert "{target_agent_name}" in user_tmpl
    assert "{system_prompt}" in user_tmpl


def test_system_prompt_documents_structured_edit_spec() -> None:
    """Phase 2 needs each hypothesis to ship with a machine-applyable edit spec.
    The system prompt must define the format, the four action types, the
    applyable:false opt-out, and the applier's line-number conventions.
    """
    sys_prompt = load_system_prompt()

    # The dedicated spec section exists.
    assert "Structured edit spec" in sys_prompt

    # All four v1 actions are documented.
    assert '"replace"' in sys_prompt
    assert '"insert_after"' in sys_prompt
    assert '"delete"' in sys_prompt
    assert '"move"' in sys_prompt

    # The required edit-object fields are named.
    assert "from_line_start" in sys_prompt
    assert "from_line_end" in sys_prompt
    assert "at_line" in sys_prompt
    assert "to_line" in sys_prompt
    assert "expected_content" in sys_prompt
    assert "new_content" in sys_prompt

    # The non-applyable escape hatch is documented.
    assert '"applyable": false' in sys_prompt
    assert '"applyable": true' in sys_prompt
    assert "reason" in sys_prompt

    # The applier-facing conventions are spelled out.
    assert "ORIGINAL file" in sys_prompt  # line-number semantics
    assert "VERBATIM" in sys_prompt       # exact-match requirement


def test_system_prompt_proposed_change_requires_structured_block() -> None:
    """The 'Proposed change' section must require both prose AND a json block,
    in that order — not prose alone (Phase 1) and not json alone (would lose
    human-readable rationale)."""
    sys_prompt = load_system_prompt()
    # The prose+structured pairing is described under Proposed change.
    proposed_idx = sys_prompt.index("**Proposed change.**")
    # Look in a window after that header for both halves.
    window = sys_prompt[proposed_idx : proposed_idx + 1000]
    assert "Prose" in window
    assert "Structured edit spec" in window


def test_system_prompt_adds_eighth_self_check() -> None:
    """A new self-check must verify expected_content matches the file verbatim
    and that file paths/line numbers in the structured block match the prose."""
    sys_prompt = load_system_prompt()
    # Self-checks are a numbered list; the new one is #8.
    assert "\n8. " in sys_prompt
    eighth_idx = sys_prompt.index("\n8. ")
    eighth = sys_prompt[eighth_idx : eighth_idx + 800]
    assert "expected_content" in eighth
    assert "VERBATIM" in eighth
    # Falling back to applyable:false is the prescribed escape route.
    assert "applyable" in eighth and "false" in eighth


def test_user_template_mentions_structured_block_requirement() -> None:
    """The user template should reinforce that each hypothesis must ship the
    structured JSON block, so the downstream applier has something to consume."""
    user_tmpl = load_user_template()
    assert "applyable" in user_tmpl
    assert "structured edit spec" in user_tmpl.lower()


def test_build_user_message_substitutes_all_fields() -> None:
    target = _target()
    msg = build_user_message(
        target=target,
        failure=_failure(),
        scenario_input="The docs say X but the code does Y.",
    )

    # No unsubstituted placeholders
    assert "{target_agent_name}" not in msg
    assert "{system_prompt}" not in msg
    assert "{eval_failure_json}" not in msg

    # All key fields appear in the output
    assert "test_agent" in msg
    assert "You are a test agent." in msg
    assert "Classify." in msg
    assert "The docs say X but the code does Y." in msg
    assert '"issue_number": 107' in msg


def test_build_user_message_warns_when_input_missing() -> None:
    target = _target()
    msg = build_user_message(target=target, failure=_failure(), scenario_input=None)
    assert "scenario input text not provided" in msg


def test_build_user_message_survives_jinja_braces_in_source() -> None:
    """If a target's classification.j2 has {{ }} or {# #}, str.format must not break."""
    target = _target(
        classification_prompt="{# version: 1 #}\nClassify: {{ user_message }}",
        system_prompt="System with {{ braces }} that should survive.",
    )
    # This must not raise KeyError
    msg = build_user_message(
        target=target, failure=_failure(), scenario_input="test",
    )
    assert "{# version: 1 #}" in msg
    assert "{{ user_message }}" in msg
    assert "{{ braces }}" in msg


def test_format_additional_files_orders_handoff_flows_tools_runner() -> None:
    target = _target(
        handoff_prompt="HANDOFF CONTENT",
        flow_prompts={"bug": "BUG FLOW", "feature": "FEATURE FLOW"},
        tool_sources={"lookup.py": "def lookup(): pass", "create.py": "def create(): pass"},
        runner_source="def main(): pass",
    )
    out = _format_additional_files(target)

    # All sections present
    assert "handoff.j2" in out
    assert "bug_flow.j2" in out
    assert "feature_flow.j2" in out
    assert "tools/create.py" in out
    assert "tools/lookup.py" in out
    assert "runner.py" in out

    # Order check: handoff before flows before tools before runner
    handoff_idx = out.index("handoff.j2")
    bug_flow_idx = out.index("bug_flow.j2")
    tools_idx = out.index("tools/create.py")
    runner_idx = out.index("runner.py")
    assert handoff_idx < bug_flow_idx < tools_idx < runner_idx

    # Flows alphabetized
    assert out.index("bug_flow.j2") < out.index("feature_flow.j2")

    # Tools alphabetized
    assert out.index("tools/create.py") < out.index("tools/lookup.py")


def test_format_additional_files_empty_when_nothing_optional() -> None:
    target = _target()  # no handoff, no flows, no tools, no runner
    assert _format_additional_files(target) == ""


def test_format_additional_files_omits_section_header_when_empty() -> None:
    """No '### Additional files' header should appear if there are no files."""
    target = _target()
    msg = build_user_message(target=target, failure=_failure(), scenario_input="x")
    assert "### Additional files" not in msg


# ---------- Line-numbering tests ----------
#
# These exist because the v2 re-review showed the hypothesis-generator model
# fabricates file:line citations when source is shown unnumbered. The fix
# prefixes every line with its 1-indexed number ("{N:4d}  {line}") so the
# model can read the number off the prefix instead of counting.


def test_number_lines_format_and_blanks() -> None:
    """4-char right-aligned gutter, two-space separator, blank lines numbered."""
    content = "first\n\nthird"
    out = _number_lines(content)
    assert out == "   1  first\n   2  \n   3  third"


def test_number_lines_single_line_no_trailing_newline() -> None:
    assert _number_lines("only") == "   1  only"


def test_number_lines_pads_into_four_char_gutter_past_999() -> None:
    """At line 1000 the gutter widens to 4 digits, no leading space."""
    content = "\n".join(["x"] * 1000)
    out = _number_lines(content)
    assert out.startswith("   1  x\n")
    assert "\n   9  x\n" in out
    assert "\n  10  x\n" in out
    assert "\n 100  x\n" in out
    assert out.endswith("\n1000  x")


def test_build_user_message_numbers_main_files() -> None:
    """system_prompt, classification_prompt, agent_yaml must appear line-numbered."""
    target = _target(
        agent_yaml="name: test_agent\nversion: 1",
        system_prompt="line one\nline two",
        classification_prompt="Classify.\n\nSecond para.",
    )
    msg = build_user_message(target=target, failure=_failure(), scenario_input="x")

    # agent.yaml
    assert "   1  name: test_agent" in msg
    assert "   2  version: 1" in msg

    # system.j2
    assert "   1  line one" in msg
    assert "   2  line two" in msg

    # classification.j2 (with a blank line that must still be numbered)
    assert "   1  Classify." in msg
    # The blank line gets its own numbered prefix — assert the exact
    # transition from line 2 (blank) to line 3 (content).
    assert "   2  \n   3  Second para." in msg


def test_build_user_message_does_not_number_placeholder_when_file_missing() -> None:
    """If a file is absent, the placeholder shouldn't be passed through _number_lines."""
    target = _target(agent_yaml="", system_prompt="", classification_prompt="")
    msg = build_user_message(target=target, failure=_failure(), scenario_input="x")
    assert "[no agent.yaml found]" in msg
    assert "[no system.j2 found]" in msg
    assert "[no classification.j2 found]" in msg
    # The placeholder should NOT be prefixed with a line number.
    assert "   1  [no agent.yaml found]" not in msg


def test_format_additional_files_numbers_every_section() -> None:
    """Handoff, flows, tools, and runner.py must all appear line-numbered."""
    target = _target(
        handoff_prompt="handoff line 1\nhandoff line 2",
        flow_prompts={"bug": "bug line 1\nbug line 2"},
        tool_sources={"lookup.py": "def lookup():\n    pass"},
        runner_source="def main():\n    pass",
    )
    out = _format_additional_files(target)

    assert "   1  handoff line 1" in out
    assert "   2  handoff line 2" in out
    assert "   1  bug line 1" in out
    assert "   2  bug line 2" in out
    assert "   1  def lookup():" in out
    assert "   2      pass" in out
    assert "   1  def main():" in out
    assert "   2      pass" in out


def test_numbered_content_preserves_jinja_braces() -> None:
    """Numbering must not break the brace-survival behavior."""
    target = _target(
        classification_prompt="{# version: 1 #}\n{{ user_message }}",
    )
    msg = build_user_message(target=target, failure=_failure(), scenario_input="x")
    assert "   1  {# version: 1 #}" in msg
    assert "   2  {{ user_message }}" in msg
