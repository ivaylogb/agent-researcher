"""Run a target agent's eval as a subprocess, parse its output into EvalSummary.

The applier needs to invoke an arbitrary eval command (the user passes it on
the CLI) and consume its result deterministically. Convention: the eval prints
its full result JSON to stdout (matching reference_agent's run_eval.py and
similar shapes). If the eval writes to a known file path instead, the caller
can point us at that file via `result_path`.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .eval_analyzer import EvalSummary, _parse_eval_result


@dataclass
class EvalRunOutput:
    """Captured artefacts from one eval subprocess invocation."""

    summary: EvalSummary
    stdout: str
    stderr: str
    returncode: int


class EvalRunError(RuntimeError):
    """The eval subprocess failed, or its output couldn't be parsed."""


def run_eval(
    target_agent_dir: Path,
    eval_command: str,
    *,
    timeout: int = 300,
    cwd: Optional[Path] = None,
    result_path: Optional[Path] = None,
) -> EvalRunOutput:
    """Execute `eval_command` and return the parsed EvalSummary.

    Args:
        target_agent_dir: the target agent's source directory. Used to derive a
            sensible default `cwd` (the directory above target_agent_dir, so
            "python -m reference_agent.evals.routing.run_eval" can import the
            package).
        eval_command: a shell-style command. Parsed with shlex; not passed
            through a shell, so wildcard expansion / shell features won't fire.
        timeout: subprocess timeout in seconds.
        cwd: working directory for the subprocess. Defaults to
            `target_agent_dir.parent`.
        result_path: if set, read JSON from this file rather than stdout. Use
            when the eval prints non-JSON noise but writes a known result file.

    Returns:
        EvalRunOutput with the parsed summary plus the raw stdout/stderr.

    Raises:
        EvalRunError: nonzero exit, timeout, or unparseable output.
        FileNotFoundError: target_agent_dir or `result_path` (if set) missing.
    """
    target_agent_dir = Path(target_agent_dir)
    if not target_agent_dir.is_dir():
        raise FileNotFoundError(f"Target agent dir not found: {target_agent_dir}")

    effective_cwd = Path(cwd) if cwd is not None else target_agent_dir.parent

    try:
        completed = subprocess.run(
            shlex.split(eval_command),
            cwd=str(effective_cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise EvalRunError(
            f"Eval command timed out after {timeout}s: {eval_command}"
        ) from e
    except FileNotFoundError as e:
        raise EvalRunError(
            f"Eval command not runnable (executable not found): {eval_command}"
        ) from e

    if completed.returncode != 0:
        raise EvalRunError(
            f"Eval command exited with code {completed.returncode}.\n"
            f"command: {eval_command}\n"
            f"cwd: {effective_cwd}\n"
            f"--- stderr ---\n{completed.stderr}\n"
            f"--- stdout ---\n{completed.stdout}"
        )

    json_text = _extract_json_payload(completed.stdout, result_path)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise EvalRunError(
            f"Eval output is not valid JSON: {e}\n"
            f"--- stdout ---\n{completed.stdout[:1000]}"
        ) from e

    try:
        summary = _parse_eval_result(data)
    except ValueError as e:
        raise EvalRunError(f"Eval output has wrong shape: {e}") from e

    return EvalRunOutput(
        summary=summary,
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
    )


def _extract_json_payload(stdout: str, result_path: Optional[Path]) -> str:
    """Return the JSON document to parse.

    If `result_path` is set, read from there. Otherwise, take stdout and trim
    any leading non-JSON lines so eval scripts that print a header line still
    work.
    """
    if result_path is not None:
        result_path = Path(result_path)
        if not result_path.is_file():
            raise FileNotFoundError(
                f"--result-path file does not exist after eval ran: {result_path}"
            )
        return result_path.read_text()

    stripped = stdout.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return stripped

    # Eval prints noise before the JSON. Find the first "{" or "[" that begins
    # what looks like a top-level document.
    for i, char in enumerate(stripped):
        if char in "{[":
            return stripped[i:]
    raise EvalRunError(
        "Eval stdout contains no JSON document. Pass --eval-result-path to "
        "read from the eval's results file instead."
    )
