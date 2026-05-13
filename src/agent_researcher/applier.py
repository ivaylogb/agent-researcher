"""Apply the structured edits from a Phase 1 hypothesis report to a target agent.

A hypothesis report (Phase 1 output) contains one or more hypotheses, each with
a fenced ```json block describing either a list of mechanical edits or an
explicit `applyable: false` opt-out. This module:

1. Parses the report and pulls the spec for a chosen hypothesis.
2. Applies the edits to files inside the target-agent directory, with a
   verbatim pre-image check on every `expected_content` string.
3. Reports which files actually changed (with before/after hashes).

The applier is deliberately strict: if any `expected_content` does not match
the file byte-for-byte, no file is written. Phase 2 must never apply a half-
broken edit.

Edits are applied using a per-original-line plan (not a sequential bottom-up
loop). Each original line is independently marked "keep" or "drop", and each
inter-line slot may collect inserted content. This makes overlapping or
interleaved edits detectable rather than silently mis-applied.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ---------- Data shapes ----------


@dataclass
class Edit:
    """One mechanical edit. Field meaning depends on `action`."""

    action: str  # "replace" | "insert_after" | "delete" | "move"
    file: str
    # Range edits (replace, delete, move source range)
    from_line_start: Optional[int] = None
    from_line_end: Optional[int] = None
    # Insert position (insert_after, move destination)
    at_line: Optional[int] = None
    to_line: Optional[int] = None
    # Content
    expected_content: Optional[str] = None
    new_content: Optional[str] = None


@dataclass
class EditSpec:
    """A hypothesis's structured edit spec — applyable or not."""

    applyable: bool
    edits: list[Edit] = field(default_factory=list)
    reason: Optional[str] = None  # only set when applyable=False


@dataclass
class FileChange:
    """The before/after fingerprint of a single file that the applier modified."""

    path: Path
    before_sha256: str
    after_sha256: str

    @property
    def changed(self) -> bool:
        return self.before_sha256 != self.after_sha256


# ---------- Parsing the hypothesis report ----------


# Match "### Hypothesis N:" — case-insensitive on the word, N is captured.
_HYPOTHESIS_HEADER_RE = re.compile(
    r"^###\s+Hypothesis\s+(\d+)\b.*$", re.MULTILINE | re.IGNORECASE
)
_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def parse_hypothesis_report(report_path: Path, hypothesis_id: int) -> EditSpec:
    """Read a Phase 1 hypothesis report and extract the spec for one hypothesis.

    Args:
        report_path: path to the markdown report.
        hypothesis_id: 1-indexed hypothesis number (matches "### Hypothesis N:").

    Returns:
        EditSpec. If the hypothesis declared `applyable: false`, the EditSpec
        carries that flag and the supplied reason — the caller decides what to
        do with it (the CLI exits with an error; tests may inspect it).

    Raises:
        FileNotFoundError: report doesn't exist.
        ValueError: hypothesis not found, no structured block, or invalid JSON.
    """
    report_path = Path(report_path)
    if not report_path.is_file():
        raise FileNotFoundError(f"Hypothesis report not found: {report_path}")

    text = report_path.read_text()
    section = _extract_hypothesis_section(text, hypothesis_id)
    json_blob = _extract_first_json_block(section, hypothesis_id)
    return _parse_edit_spec(json_blob, hypothesis_id)


def _extract_hypothesis_section(text: str, hypothesis_id: int) -> str:
    """Slice the markdown text down to one hypothesis's content.

    The section starts at "### Hypothesis N:" and ends at the next "###" header
    or end-of-document, whichever comes first.
    """
    matches = list(_HYPOTHESIS_HEADER_RE.finditer(text))
    if not matches:
        raise ValueError(
            "Report contains no '### Hypothesis N:' headers — does not look "
            "like a Phase 1 hypothesis report."
        )

    target_match = next((m for m in matches if int(m.group(1)) == hypothesis_id), None)
    if target_match is None:
        available = [int(m.group(1)) for m in matches]
        raise ValueError(
            f"Hypothesis {hypothesis_id} not found in report. "
            f"Available hypothesis IDs: {available}"
        )

    start = target_match.start()
    # Find the next '###' header strictly after this one.
    next_header = re.search(r"^###\s+", text[target_match.end():], re.MULTILINE)
    if next_header is None:
        # Also stop at the next '##' or end-of-document — the "What this
        # report is NOT" section uses '## ' not '###', so a missing '###'
        # successor doesn't mean the hypothesis runs to EOF.
        next_h2 = re.search(r"^##\s+", text[target_match.end():], re.MULTILINE)
        end = (target_match.end() + next_h2.start()) if next_h2 else len(text)
    else:
        end = target_match.end() + next_header.start()

    return text[start:end]


def _extract_first_json_block(section: str, hypothesis_id: int) -> str:
    """Return the first fenced ```json block inside the hypothesis section."""
    match = _JSON_FENCE_RE.search(section)
    if match is None:
        raise ValueError(
            f"Hypothesis {hypothesis_id} has no fenced ```json block. "
            "The report may be from Phase 1 pre-retrofit (v3 or earlier)."
        )
    return match.group(1)


def _parse_edit_spec(json_blob: str, hypothesis_id: int) -> EditSpec:
    """Parse the JSON blob into an EditSpec, validating shape."""
    try:
        data = json.loads(json_blob)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Hypothesis {hypothesis_id}'s structured block is not valid JSON: {e}"
        ) from e

    if not isinstance(data, dict):
        raise ValueError(
            f"Hypothesis {hypothesis_id}'s structured block must be a JSON object, "
            f"got {type(data).__name__}."
        )

    if "applyable" not in data:
        raise ValueError(
            f"Hypothesis {hypothesis_id}'s structured block is missing the "
            "'applyable' field."
        )

    if data["applyable"] is False:
        return EditSpec(
            applyable=False,
            reason=str(data.get("reason", "(no reason given)")),
        )

    if data["applyable"] is not True:
        raise ValueError(
            f"Hypothesis {hypothesis_id}'s 'applyable' field must be true or false."
        )

    raw_edits = data.get("edits")
    if not isinstance(raw_edits, list) or not raw_edits:
        raise ValueError(
            f"Hypothesis {hypothesis_id} declares applyable:true but has no "
            "non-empty 'edits' list."
        )

    edits = [_parse_one_edit(e, hypothesis_id, i) for i, e in enumerate(raw_edits)]
    return EditSpec(applyable=True, edits=edits)


_REQUIRED_FIELDS_BY_ACTION: dict[str, tuple[str, ...]] = {
    "replace": ("file", "from_line_start", "from_line_end", "expected_content", "new_content"),
    "insert_after": ("file", "at_line", "new_content"),
    "delete": ("file", "from_line_start", "from_line_end", "expected_content"),
    "move": ("file", "from_line_start", "from_line_end", "to_line", "expected_content"),
}


def _parse_one_edit(raw: Any, hypothesis_id: int, index: int) -> Edit:
    if not isinstance(raw, dict):
        raise ValueError(
            f"Hypothesis {hypothesis_id} edit #{index}: must be a JSON object."
        )
    action = raw.get("action")
    if action not in _REQUIRED_FIELDS_BY_ACTION:
        raise ValueError(
            f"Hypothesis {hypothesis_id} edit #{index}: unknown action {action!r}. "
            f"Expected one of {sorted(_REQUIRED_FIELDS_BY_ACTION)}."
        )

    required = _REQUIRED_FIELDS_BY_ACTION[action]
    missing = [f for f in required if f not in raw]
    if missing:
        raise ValueError(
            f"Hypothesis {hypothesis_id} edit #{index} (action={action}): "
            f"missing required field(s) {missing}."
        )

    return Edit(
        action=action,
        file=str(raw["file"]),
        from_line_start=raw.get("from_line_start"),
        from_line_end=raw.get("from_line_end"),
        at_line=raw.get("at_line"),
        to_line=raw.get("to_line"),
        expected_content=raw.get("expected_content"),
        new_content=raw.get("new_content"),
    )


# ---------- Applying edits ----------


def apply_edits(
    target_agent_dir: Path,
    spec: EditSpec,
    *,
    dry_run: bool = False,
) -> list[FileChange]:
    """Apply a hypothesis's edits to files inside `target_agent_dir`.

    Files are addressed in two passes:
    1. Resolve every edit's file path under target_agent_dir, load the
       current content, verify the `expected_content` strings match.
    2. If all checks pass, compute the new content and (unless dry_run) write
       it back.

    Args:
        target_agent_dir: root of the target agent's source tree. Edit `file`
            fields are resolved relative to this directory; the applier walks
            the tree to find a matching basename if the path isn't relative-
            ready (so "classification.j2" finds "prompts/classification.j2").
        spec: the parsed EditSpec. Must be applyable; otherwise we raise.
        dry_run: if True, run all checks and compute changes but don't write.

    Returns:
        A list of FileChange — one per file touched. The before/after hashes
        differ exactly when the new content differs from the original.

    Raises:
        ValueError: spec is not applyable; expected_content mismatch; overlap;
            invalid line numbers; resolver can't find a referenced file.
    """
    if not spec.applyable:
        raise ValueError(
            f"Cannot apply a non-applyable hypothesis (reason: {spec.reason})."
        )

    target_agent_dir = Path(target_agent_dir)
    if not target_agent_dir.is_dir():
        raise FileNotFoundError(f"Target agent dir not found: {target_agent_dir}")

    # Group edits by resolved file path, since plan-construction is per-file.
    by_file: dict[Path, list[Edit]] = {}
    for edit in spec.edits:
        resolved = _resolve_file(target_agent_dir, edit.file)
        by_file.setdefault(resolved, []).append(edit)

    changes: list[FileChange] = []
    for path, edits in by_file.items():
        original_text = path.read_text()
        new_text = _apply_edits_to_text(original_text, edits)
        before_hash = _sha256(original_text)
        after_hash = _sha256(new_text)
        changes.append(FileChange(path=path, before_sha256=before_hash, after_sha256=after_hash))
        if not dry_run and before_hash != after_hash:
            path.write_text(new_text)

    return changes


def _resolve_file(root: Path, name: str) -> Path:
    """Resolve a file reference inside the target agent directory.

    Edit `file` fields tend to be bare basenames ("classification.j2") rather
    than directory-qualified paths ("prompts/classification.j2"). We try, in
    order: (1) literal join, (2) recursive search for a unique basename match.
    """
    direct = root / name
    if direct.is_file():
        return direct

    candidates = [p for p in root.rglob(name) if p.is_file()]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(
            f"Edit references file {name!r}, but no such file exists under {root}."
        )
    raise ValueError(
        f"Edit references file {name!r}, which is ambiguous under {root}. "
        f"Candidates: {[str(p.relative_to(root)) for p in candidates]}. "
        "Qualify the path (e.g., 'prompts/classification.j2')."
    )


def _apply_edits_to_text(original_text: str, edits: list[Edit]) -> str:
    """Compute the post-edit text for one file.

    Uses splitlines()-based line addressing (matches _number_lines in
    prompt_assembler), preserves the original's trailing-newline state.
    """
    original_lines = original_text.splitlines()
    n = len(original_lines)
    had_trailing_newline = original_text.endswith("\n") or original_text == ""

    # Per-line plan: which lines to drop, what content to emit after each line.
    drop = [False] * n
    emit_after: list[list[str]] = [[] for _ in range(n)]
    # Inserts at the very top (before line 1) — used when replace's range
    # starts at line 1.
    emit_at_top: list[str] = []

    _validate_and_record(original_lines, edits, drop, emit_after, emit_at_top)

    result_lines: list[str] = []
    for chunk in emit_at_top:
        result_lines.extend(chunk.split("\n"))
    for i in range(n):
        if not drop[i]:
            result_lines.append(original_lines[i])
        for chunk in emit_after[i]:
            result_lines.extend(chunk.split("\n"))

    new_text = "\n".join(result_lines)
    if had_trailing_newline and not new_text.endswith("\n"):
        new_text += "\n"
    return new_text


def _validate_and_record(
    original_lines: list[str],
    edits: list[Edit],
    drop: list[bool],
    emit_after: list[list[str]],
    emit_at_top: list[str],
) -> None:
    """Walk each edit, verify `expected_content`, populate the plan in place.

    Raises on the first problem encountered. No partial mutation of the file
    happens because callers only consult the plan after this returns.
    """
    n = len(original_lines)

    for index, edit in enumerate(edits):
        if edit.action == "replace":
            _check_range(edit, n, index)
            _check_expected(edit, original_lines, index)
            for i in range(edit.from_line_start, edit.from_line_end + 1):
                _claim_drop(drop, i, index)
            _record_insert_before(edit.from_line_start, edit.new_content, drop, emit_after, emit_at_top, index)

        elif edit.action == "delete":
            _check_range(edit, n, index)
            _check_expected(edit, original_lines, index)
            for i in range(edit.from_line_start, edit.from_line_end + 1):
                _claim_drop(drop, i, index)

        elif edit.action == "insert_after":
            if not (1 <= edit.at_line <= n):
                raise ValueError(
                    f"Edit #{index} (insert_after): at_line={edit.at_line} out of "
                    f"range 1..{n}."
                )
            emit_after[edit.at_line - 1].append(edit.new_content)

        elif edit.action == "move":
            _check_range(edit, n, index)
            _check_expected(edit, original_lines, index)
            if not (1 <= edit.to_line <= n):
                raise ValueError(
                    f"Edit #{index} (move): to_line={edit.to_line} out of "
                    f"range 1..{n}."
                )
            if edit.from_line_start <= edit.to_line <= edit.from_line_end:
                raise ValueError(
                    f"Edit #{index} (move): to_line={edit.to_line} falls inside "
                    f"the source range [{edit.from_line_start}..{edit.from_line_end}]."
                )
            captured = "\n".join(
                original_lines[edit.from_line_start - 1 : edit.from_line_end]
            )
            for i in range(edit.from_line_start, edit.from_line_end + 1):
                _claim_drop(drop, i, index)
            emit_after[edit.to_line - 1].append(captured)

        else:
            raise ValueError(f"Edit #{index}: unknown action {edit.action!r}.")


def _check_range(edit: Edit, n: int, index: int) -> None:
    """Validate from_line_start <= from_line_end and both within file bounds."""
    if edit.from_line_start is None or edit.from_line_end is None:
        raise ValueError(f"Edit #{index} ({edit.action}): line range is missing.")
    if edit.from_line_start < 1 or edit.from_line_end > n:
        raise ValueError(
            f"Edit #{index} ({edit.action}): line range "
            f"[{edit.from_line_start}..{edit.from_line_end}] out of file bounds 1..{n}."
        )
    if edit.from_line_start > edit.from_line_end:
        raise ValueError(
            f"Edit #{index} ({edit.action}): from_line_start "
            f"({edit.from_line_start}) > from_line_end ({edit.from_line_end})."
        )


def _check_expected(edit: Edit, original_lines: list[str], index: int) -> None:
    """Verify `expected_content` matches the file's content at the cited range."""
    actual = "\n".join(original_lines[edit.from_line_start - 1 : edit.from_line_end])
    expected = edit.expected_content or ""
    if actual != expected:
        raise ValueError(
            f"Edit #{index} ({edit.action}) on {edit.file}: expected_content "
            f"does not match lines {edit.from_line_start}..{edit.from_line_end}.\n"
            f"--- expected ---\n{expected!r}\n"
            f"--- actual ---\n{actual!r}"
        )


def _claim_drop(drop: list[bool], one_indexed_line: int, edit_index: int) -> None:
    """Mark a line as dropped, raising if another edit already claimed it."""
    i = one_indexed_line - 1
    if drop[i]:
        raise ValueError(
            f"Edit #{edit_index} overlaps a prior edit at line {one_indexed_line} "
            "— two edits cannot delete or replace the same line."
        )
    drop[i] = True


def _record_insert_before(
    one_indexed_line: int,
    new_content: str,
    drop: list[bool],
    emit_after: list[list[str]],
    emit_at_top: list[str],
    edit_index: int,
) -> None:
    """Schedule new_content to appear before original line `one_indexed_line`."""
    if new_content is None:
        raise ValueError(f"Edit #{edit_index}: new_content is required but missing.")
    if one_indexed_line == 1:
        emit_at_top.append(new_content)
    else:
        emit_after[one_indexed_line - 2].append(new_content)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
