"""Command-line entry: investigate a single failure end to end.

Usage:
    python -m agent_researcher \\
        --target-agent /path/to/reference_agent \\
        --eval-result /path/to/eval_output.json \\
        --scenario-id 107 \\
        --scenario-input "the actual user message text"

Output: prints the hypothesis report markdown to stdout. Optionally writes
to --output-file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .code_reader import load_target_agent
from .eval_analyzer import load_eval_result, select_failure
from .hypothesis_agent import generate_hypotheses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate hypotheses about why an agent failed an eval."
    )
    parser.add_argument(
        "--target-agent",
        type=Path,
        required=True,
        help="Path to the target agent's directory (containing agent.yaml, prompts/, tools/).",
    )
    parser.add_argument(
        "--eval-result",
        type=Path,
        required=True,
        help="Path to the eval result JSON.",
    )
    parser.add_argument(
        "--scenario-id",
        type=str,
        default=None,
        help="Specific scenario_id to investigate. If omitted, picks the first failure.",
    )
    parser.add_argument(
        "--scenario-input",
        type=str,
        default=None,
        help="The actual user message for the failing scenario. Strongly recommended.",
    )
    parser.add_argument(
        "--scenario-input-file",
        type=Path,
        default=None,
        help="File containing the user message (alternative to --scenario-input).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="claude-sonnet-4-5",
        help="Claude model to use.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="If set, write the hypothesis report to this file (in addition to stdout).",
    )
    args = parser.parse_args(argv)

    # Load everything before making the API call (fail fast).
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

    # Make the call.
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

    # Output.
    print(report.markdown)

    if args.output_file is not None:
        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        args.output_file.write_text(report.markdown + "\n")
        print(
            f"\n[wrote report to {args.output_file}]",
            file=sys.stderr,
        )

    print(
        f"\n[input_tokens={report.input_tokens}, output_tokens={report.output_tokens}]",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
