"""Load an eval result JSON and identify a failure to investigate.

The expected shape (from agent-eval-loop's routing eval output):

    {
      "total": 7,
      "passed": 6,
      "pass_rate": 0.857,
      "threshold": 0.9,
      "meets_threshold": false,
      "results": [
        {
          "issue_number": 101,
          "expected_intent": "bug",
          "predicted_intent": "bug",
          "predicted_confidence": 0.95,
          "passed": true,
          "notes": "..."
        },
        ...
      ]
    }

This loader is shape-tolerant. If the eval output uses slightly different
field names, callers can pass field aliases or pre-process the JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class EvalFailure:
    """A single failing eval result, ready for hypothesis investigation."""

    scenario_id: str
    expected: str
    predicted: str
    predicted_confidence: Optional[float]
    notes: str
    raw: dict[str, Any]  # the original dict, for prompt context


@dataclass
class EvalSummary:
    """The overall eval result summary."""

    total: int
    passed: int
    pass_rate: float
    threshold: Optional[float]
    meets_threshold: Optional[bool]
    failures: list[EvalFailure]


def load_eval_result(path: Path) -> EvalSummary:
    """Load an eval result JSON file.

    Args:
        path: Path to the JSON file produced by an eval run.

    Returns:
        EvalSummary with the failing scenarios extracted.

    Raises:
        FileNotFoundError: if path doesn't exist.
        ValueError: if the JSON shape isn't recognizable as an eval result.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Eval result file not found: {path}")

    data = json.loads(path.read_text())
    return _parse_eval_result(data)


def _parse_eval_result(data: dict[str, Any]) -> EvalSummary:
    """Parse a parsed JSON dict into an EvalSummary.

    Tolerant of minor field-name variation. Recognizes 'results' or 'cases'
    as the array of per-scenario records.
    """
    results = data.get("results") or data.get("cases")
    if results is None:
        raise ValueError(
            "Eval result JSON has no 'results' or 'cases' array. "
            f"Top-level keys: {list(data.keys())}"
        )

    failures: list[EvalFailure] = []
    for record in results:
        if record.get("passed") is True:
            continue
        failures.append(_record_to_failure(record))

    return EvalSummary(
        total=data.get("total", len(results)),
        passed=data.get("passed", sum(1 for r in results if r.get("passed"))),
        pass_rate=data.get("pass_rate", 0.0),
        threshold=data.get("threshold"),
        meets_threshold=data.get("meets_threshold"),
        failures=failures,
    )


def _record_to_failure(record: dict[str, Any]) -> EvalFailure:
    """Extract failure fields from a per-scenario record."""
    scenario_id = str(
        record.get("issue_number")
        or record.get("scenario_id")
        or record.get("id")
        or "unknown"
    )
    return EvalFailure(
        scenario_id=scenario_id,
        expected=str(record.get("expected_intent") or record.get("expected") or "unknown"),
        predicted=str(record.get("predicted_intent") or record.get("predicted") or "unknown"),
        predicted_confidence=record.get("predicted_confidence") or record.get("confidence"),
        notes=str(record.get("notes") or ""),
        raw=record,
    )


def select_failure(summary: EvalSummary, scenario_id: Optional[str] = None) -> EvalFailure:
    """Pick a failure to investigate.

    If scenario_id is given, return that specific failure.
    Otherwise, return the first failure. (Future: return the lowest-confidence
    failure, or the one with the most-ambiguous notes.)
    """
    if not summary.failures:
        raise ValueError("No failures in this eval result. Nothing to investigate.")

    if scenario_id is not None:
        for f in summary.failures:
            if f.scenario_id == scenario_id:
                return f
        raise ValueError(f"No failure found with scenario_id={scenario_id}")

    return summary.failures[0]
