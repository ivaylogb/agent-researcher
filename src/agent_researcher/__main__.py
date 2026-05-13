"""Command-line entry point.

Two subcommands:

    agent-researcher diagnose --target-agent ... --eval-result ... [...]
        Phase 1: generate a hypothesis report for a single failure.

    agent-researcher apply --hypothesis-report ... --hypothesis-id N \\
        --target-agent ... --eval-command "..."
        Phase 2: apply one hypothesis's structured edits and re-run the eval.

For backward compatibility with the Phase 1 invocation form (no subcommand),
running the module with no subcommand defaults to `diagnose`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .applier import EditSpec, apply_edits, parse_hypothesis_report
from .code_reader import load_target_agent
from .delta import compute_delta, render_delta_markdown
from .eval_analyzer import load_eval_result, select_failure
from .eval_runner import EvalRunError, run_eval
from .hypothesis_agent import generate_hypotheses


_KNOWN_SUBCOMMANDS = {"diagnose", "apply"}


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Backward compat with the Phase 1 invocation form (no subcommand). If the
    # caller used a known diagnose-specific flag as the first arg, prepend
    # "diagnose". Leave -h/--help alone so the top-level help reveals both
    # subcommands.
    _DIAGNOSE_FLAGS = {
        "--target-agent", "--eval-result", "--scenario-id",
        "--scenario-input", "--scenario-input-file", "--model", "--output-file",
    }
    if raw and raw[0] in _DIAGNOSE_FLAGS:
        raw = ["diagnose", *raw]

    parser = argparse.ArgumentParser(
        prog="agent-researcher",
        description="Diagnose and apply fixes for failing agent evals.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    _add_diagnose_parser(subparsers)
    _add_apply_parser(subparsers)

    args = parser.parse_args(raw)
    if args.subcommand == "diagnose":
        return _run_diagnose(args)
    if args.subcommand == "apply":
        return _run_apply(args)
    parser.error(f"Unknown subcommand: {args.subcommand}")
    return 2  # unreachable; argparse.error exits.


# ---------- diagnose ----------


def _add_diagnose_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "diagnose",
        help="Phase 1: generate a hypothesis report for one failing scenario.",
    )
    p.add_argument(
        "--target-agent",
        type=Path,
        required=True,
        help="Path to the target agent's directory.",
    )
    p.add_argument(
        "--eval-result",
        type=Path,
        required=True,
        help="Path to the eval result JSON.",
    )
    p.add_argument(
        "--scenario-id",
        type=str,
        default=None,
        help="Specific scenario_id to investigate. If omitted, picks the first failure.",
    )
    p.add_argument(
        "--scenario-input",
        type=str,
        default=None,
        help="The actual user message for the failing scenario.",
    )
    p.add_argument(
        "--scenario-input-file",
        type=Path,
        default=None,
        help="File containing the user message (alternative to --scenario-input).",
    )
    p.add_argument(
        "--model",
        type=str,
        default="claude-sonnet-4-5",
        help="Claude model to use.",
    )
    p.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="If set, write the hypothesis report to this file (in addition to stdout).",
    )


def _run_diagnose(args: argparse.Namespace) -> int:
    try:
        target = load_target_agent(args.target_agent)
    except (FileNotFoundError, ValueError) as e:
        print(f"Failed to load target agent: {e}", file=sys.stderr)
        return 2

    try:
        eval_summary = load_eval_result(args.eval_result)
    except (FileNotFoundError, ValueError) as e:
        print(f"Failed to load eval result: {e}", file=sys.stderr)
        return 2

    try:
        failure = select_failure(eval_summary, scenario_id=args.scenario_id)
    except ValueError as e:
        print(f"Failed to select failure: {e}", file=sys.stderr)
        return 2

    scenario_input = args.scenario_input
    if scenario_input is None and args.scenario_input_file is not None:
        scenario_input = args.scenario_input_file.read_text().strip()

    if scenario_input is None:
        print(
            "[warning] No --scenario-input provided. The report will be weaker.",
            file=sys.stderr,
        )

    try:
        report = generate_hypotheses(
            target=target,
            failure=failure,
            scenario_input=scenario_input,
            model=args.model,
        )
    except RuntimeError as e:
        print(f"Hypothesis generation failed: {e}", file=sys.stderr)
        return 3

    print(report.markdown)

    if args.output_file is not None:
        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        args.output_file.write_text(report.markdown + "\n")
        print(f"\n[wrote report to {args.output_file}]", file=sys.stderr)

    print(
        f"\n[input_tokens={report.input_tokens}, output_tokens={report.output_tokens}]",
        file=sys.stderr,
    )
    return 0


# ---------- apply ----------


def _add_apply_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "apply",
        help="Phase 2: apply a hypothesis's edits and re-run the eval.",
    )
    p.add_argument(
        "--hypothesis-report",
        type=Path,
        required=True,
        help="Path to a Phase 1 hypothesis report (containing structured edit specs).",
    )
    p.add_argument(
        "--hypothesis-id",
        type=int,
        required=True,
        help="Which hypothesis (1, 2, 3, ...) to apply.",
    )
    p.add_argument(
        "--target-agent",
        type=Path,
        required=True,
        help="Path to the target agent's directory.",
    )
    p.add_argument(
        "--eval-command",
        type=str,
        required=True,
        help='Shell-style command to run the eval, e.g. '
             '"python -m reference_agent.evals.routing.run_eval".',
    )
    p.add_argument(
        "--scenario-id",
        type=str,
        default=None,
        help="Scenario the hypothesis is meant to fix. Defaults to the scenario "
             "referenced in the report's 'Failure summary' or the first failing "
             "baseline scenario.",
    )
    p.add_argument(
        "--eval-cwd",
        type=Path,
        default=None,
        help="Working directory for the eval subprocess. Defaults to the parent "
             "of --target-agent (so the package is importable).",
    )
    p.add_argument(
        "--eval-result-path",
        type=Path,
        default=None,
        help="If set, read the eval's result JSON from this path instead of stdout.",
    )
    p.add_argument(
        "--eval-timeout",
        type=int,
        default=300,
        help="Eval subprocess timeout in seconds (default 300).",
    )
    p.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Write the delta report to this file (in addition to stdout).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan the edits and skip the re-eval. Does not write any files.",
    )


def _run_apply(args: argparse.Namespace) -> int:
    # 1. Parse hypothesis report and extract the spec.
    try:
        spec = parse_hypothesis_report(args.hypothesis_report, args.hypothesis_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"Failed to parse hypothesis report: {e}", file=sys.stderr)
        return 2

    if not spec.applyable:
        print(
            f"Hypothesis {args.hypothesis_id} is not applyable.\n"
            f"Reason: {spec.reason}\n"
            "Aborting — no edits or eval runs were performed.",
            file=sys.stderr,
        )
        return 4

    hypothesis_summary = _hypothesis_summary(args.hypothesis_report, args.hypothesis_id)

    # 2. Dry-run path: just plan and print, no eval invocation.
    if args.dry_run:
        try:
            planned = apply_edits(args.target_agent, spec, dry_run=True)
        except (FileNotFoundError, ValueError) as e:
            print(f"Dry-run failed: {e}", file=sys.stderr)
            return 5
        print(f"[dry-run] Would touch {len(planned)} file(s):")
        for change in planned:
            mark = "modified" if change.changed else "unchanged"
            print(f"  {mark}: {change.path}")
        return 0

    # 3. Baseline eval — capture before state.
    print("[apply] Running baseline eval...", file=sys.stderr)
    try:
        before = run_eval(
            args.target_agent,
            args.eval_command,
            timeout=args.eval_timeout,
            cwd=args.eval_cwd,
            result_path=args.eval_result_path,
        )
    except (FileNotFoundError, EvalRunError) as e:
        print(f"Baseline eval failed: {e}", file=sys.stderr)
        return 5

    # 4. Apply edits.
    print(
        f"[apply] Applying hypothesis {args.hypothesis_id} ({len(spec.edits)} edit(s))...",
        file=sys.stderr,
    )
    try:
        changes = apply_edits(args.target_agent, spec, dry_run=False)
    except (FileNotFoundError, ValueError) as e:
        print(
            f"Edit application failed: {e}\n"
            "No re-eval ran. Files may have been left unchanged "
            "(applier validates everything before writing).",
            file=sys.stderr,
        )
        return 6

    files_modified = [str(c.path) for c in changes if c.changed]
    print(f"[apply] Wrote {len(files_modified)} file(s).", file=sys.stderr)

    # 5. Re-eval — capture after state.
    print("[apply] Running re-eval...", file=sys.stderr)
    try:
        after = run_eval(
            args.target_agent,
            args.eval_command,
            timeout=args.eval_timeout,
            cwd=args.eval_cwd,
            result_path=args.eval_result_path,
        )
    except (FileNotFoundError, EvalRunError) as e:
        print(
            f"Re-eval failed: {e}\n"
            "Edits are still applied on disk. Revert manually with git checkout.",
            file=sys.stderr,
        )
        return 7

    # 6. Compute delta.
    target_id = args.scenario_id or _infer_target_scenario_id(
        args.hypothesis_report, before.summary
    )
    delta = compute_delta(before.summary, after.summary, target_id)
    markdown = render_delta_markdown(
        delta,
        hypothesis_summary=hypothesis_summary,
        files_modified=files_modified,
    )

    print(markdown)

    if args.output_file is not None:
        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        args.output_file.write_text(markdown)
        print(f"\n[wrote delta report to {args.output_file}]", file=sys.stderr)

    return 0


def _hypothesis_summary(report_path: Path, hypothesis_id: int) -> str:
    """Extract the H{id} header + claim line for the delta report.

    Best-effort: returns a short string identifying which hypothesis was
    applied. Falls back to a generic label if the report's structure isn't
    recognizable.
    """
    try:
        text = Path(report_path).read_text()
    except OSError:
        return f"Hypothesis {hypothesis_id} from {report_path}"

    import re as _re
    header_re = _re.compile(
        rf"^###\s+Hypothesis\s+{hypothesis_id}\b.*$",
        _re.MULTILINE | _re.IGNORECASE,
    )
    match = header_re.search(text)
    if not match:
        return f"Hypothesis {hypothesis_id} from {report_path}"
    header = match.group(0).lstrip("# ").strip()

    after = text[match.end():]
    claim_match = _re.search(r"\*\*Claim:\*\*\s*(.+?)(?:\n\n|\n\*\*)", after, _re.DOTALL)
    claim = claim_match.group(1).strip() if claim_match else "(claim text not found)"
    return f"**{header}**\n\n{claim}"


def _infer_target_scenario_id(report_path: Path, summary) -> str:
    """Guess the target scenario_id from the report or the baseline failures."""
    try:
        text = Path(report_path).read_text()
        import re as _re
        m = _re.search(r"Scenario:\s*(?:issue\s+)?([\w.-]+)", text)
        if m:
            return m.group(1)
    except OSError:
        pass
    if summary.failures:
        return summary.failures[0].scenario_id
    return "unknown"


if __name__ == "__main__":
    sys.exit(main())
