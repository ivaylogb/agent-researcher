# agent-researcher

A failure-diagnosis agent for other agents. When a target agent fails an eval, this reads the failing scenario and the target agent's source, produces a small set of structured hypotheses about the cause, applies one of those hypotheses, and re-runs the eval to measure the delta.

Three subcommands:

- `diagnose`: produce 2–3 ranked hypotheses for one failing scenario. Each hypothesis is assigned to one of the four agent-engineering layers and cites specific `file:line` evidence from the target agent's code. Each hypothesis also ships a structured edit spec so the change can be applied mechanically.
- `apply`: apply one hypothesis's edits to the target, re-run the eval, and emit a before/after delta report. The applier verifies every edit's expected content matches the file verbatim before writing; no edit, no eval run on a half-applied state.
- `iterate`: apply every applyable hypothesis from a report against a shared baseline, re-run the eval after each one, and produce a comparison table that ranks hypotheses by measured outcome. Each hypothesis is applied, evaluated, and reverted before the next one runs, so every comparison is against the same starting point.

## The four layers

Hypotheses are categorized against the four-layer model from [agent-engineering](https://github.com/ivaylogb/agent-engineering):

1. **Evaluation** — the eval criterion itself
2. **Tools** — tool definitions, descriptions, schemas
3. **Context** — what reaches the model at decision time
4. **Workflow** — runtime architecture and orchestration

The system prompt requires every hypothesis to pick exactly one layer and cite evidence specific to that layer. Two hypotheses that would be fixed by the same edit get collapsed.

## Install

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

### Diagnose

```bash
python -m agent_researcher diagnose \
    --target-agent /path/to/reference_agent \
    --eval-result /path/to/eval_output.json \
    --scenario-id 107 \
    --scenario-input-file /path/to/scenario_input.txt \
    --output-file outputs/issue_107.md
```

Or via the installed entry point: `agent-researcher diagnose` accepts the same flags. The bare `python -m agent_researcher --target-agent ...` form (no subcommand) is preserved for backward compatibility.

The target agent's source files (agent.yaml, prompts, runner.py, tools/) are loaded and shown to the model with each line prefixed by its 1-indexed line number. This is what lets the hypothesis report cite `file:line` evidence the reader can actually verify.

### Apply

```bash
python -m agent_researcher apply \
    --hypothesis-report outputs/issue_107.md \
    --hypothesis-id 1 \
    --target-agent /path/to/reference_agent \
    --eval-command "/path/to/target-agent/.venv/bin/python -m target_agent.evals.routing.run_eval" \
    --output-file outputs/issue_107_h1_delta.md
```

`--eval-command` is run as a subprocess in the target agent's parent directory by default (override with `--eval-cwd`). The eval's stdout is parsed as JSON; if your eval writes to a known results file instead of stdout, point at it with `--eval-result-path`.

The interpreter in `--eval-command` matters. The eval subprocess inherits no environment from the `agent-researcher` venv, so it needs a Python that already has the *target agent's* dependencies (jinja2, anthropic, yaml, etc.) installed. Pointing at the target's own venv is the simplest fix; activating the target's venv before running `agent-researcher apply` works too.

`--dry-run` plans the edits and reports which files would change, without writing anything and without running the eval. Use it to verify a structured spec parses and matches the file before spending the eval cost.

The applier never reverts. If the re-eval shows the hypothesis was a regression, the operator decides whether to `git checkout` the target's modified files. The delta report's "How to revert" section lists exactly which files were touched.

### Iterate

```bash
python -m agent_researcher iterate \
    --hypothesis-report outputs/issue_107.md \
    --target-agent /path/to/reference_agent \
    --eval-command "/path/to/target-agent/.venv/bin/python -m target_agent.evals.routing.run_eval" \
    --output-file outputs/issue_107_iteration.md
```

`iterate` runs a baseline eval, then for each hypothesis in the report: snapshots the target's files, applies the hypothesis, runs the eval, reverts the snapshot, and records the delta. Hypotheses with `applyable: false` are skipped and listed in the comparison. The output is a ranked table (pass-rate delta, target-scenario flip, other flips, files modified) plus per-hypothesis detail. The hypothesis with the best measured outcome is highlighted in the summary.

The revert is what makes the comparison meaningful: every hypothesis is evaluated against the same baseline, not against the cumulative state of the previous hypothesis's edits.

`--dry-run` shows which hypotheses would apply and how many evals would run, without modifying anything.

## Report shape

Each report has:

- Failure summary (scenario, expected vs predicted)
- Layer categorization (primary + secondary candidates)
- 2–3 hypotheses, each with:
  - Claim (1–2 sentences)
  - Layer assignment
  - Evidence with `file:line` citations
  - Proposed change (applyable, not vague)
  - Verification step (which metric should move and by how much)

## Worked example

`examples/issue_107/` runs the agent against agent-skill-kit's reference_agent on the only failing scenario in its routing eval. It carries three artifacts: the diagnosis (`report.md`), the delta from applying hypothesis 1 in isolation (`delta_h1.md`), and the iteration comparing every applyable hypothesis head-to-head (`iteration.md`).

A taste of what the `diagnose` output looks like — the Layer categorization section from the worked example:

> This failure most likely sits in **Layer 3 (Context)**.
>
> Reasoning: The classification prompt *does* contain a tie-break rule for mixed signals (classification.j2:44 — "Mixed signals (e.g., 'the docs are wrong AND I think this is a bug') → `unknown`"), which is almost a verbatim match for issue 107's framing. But this rule lives in a "Calibration notes" bullet list near the end of the prompt, after the intents and confidence bands, where it competes with the much more prominent `bug` signals enumerated at line 10 ("error messages, stack traces, … reproduction steps, version numbers") — all of which issue 107 also contains. The model picked the salient prominent signal-set match over the buried tie-break rule.

The full report produces three structurally distinct hypotheses (one Layer 3, one Layer 1, one Layer 4), two with applyable structured edits. The Layer 4 hypothesis ships `applyable: false` because it requires a multi-line cross-cutting addition to `runner.py` rather than an in-place edit. 4 of the report's `file:line` citations were spot-checked against the real source files during the run; all matched. The citation format is line-numbered, so any reader can verify the others against the live source. See `examples/issue_107/AUTHORING_BASELINE.md` for the methodology used to grade a `diagnose` run.

`delta_h1.md` shows the result of applying hypothesis 1 in isolation: pass rate moved from 0.857 (6/7) to 1.000 (7/7), the target scenario flipped from `bug @ 0.75 (fail)` to `unknown @ 0.60 (pass)`, and no other scenarios changed status.

`iteration.md` runs both applyable hypotheses against the same baseline. H1 (Layer 3 — promote the buried mixed-signals rule to the top of the prompt) lifted pass rate by +0.143 and fixed scenario 107. H2 (Layer 1 — tighten the wording of the 0.7–0.9 confidence band) edited the same file with a different mechanism and moved the pass rate by 0.000. The layer-3 framing of the failure — that the right rule existed but was structurally buried — was the one that produced a measured improvement. The layer-1 framing was falsified by the eval. H3 was correctly skipped. The agent-skill-kit working tree reverted between hypotheses and ended identical to the starting state.

A note on variance: single-run eval results are subject to temperature variance in the target agent's underlying model. Five fresh runs of the routing eval against the unmodified reference_agent produced scenario 107 classified as `bug` 5/5 times, with confidence ranging 0.75–0.85, so the failure is at least reproducible. But `iterate`'s pass-rate deltas are directional signals, not statistically verified rankings. Operators who want a verified ranking should re-run the iteration, or author single-mechanism hypotheses (see below).

### Single-mechanism hypotheses for clean ablation

Hypothesis 1 in the worked example does two things at once: it `move`s the mixed-signals rule earlier in the prompt (a salience change, Layer 3) and `replace`s the rule with new wording that explicitly covers "either…or" phrasing (a phrasing change, Layer 1). The applier verifies and applies both. The re-eval verifies the combined edit improves the pass rate. What it cannot do is tell you which of the two mechanisms moved the needle.

Operators who want a clean attribution can author hypotheses with a single mechanism per spec. A `move` edit that re-inserts the original text verbatim isolates the salience theory. A `replace` edit at the original line isolates the phrasing theory. Run each as a separate `apply` invocation against a clean baseline. The model's default tendency is to bundle both into one hypothesis because the bundled version reads as more confident; the structured-edit format makes the bundle visible and ablatable.

## Forbidden hypothesis patterns

The system prompt rejects hypotheses that look like:

- "Make the prompt clearer"
- "Add more examples"
- "Lower the temperature"
- "Use a stronger model"
- "The model is confused"

These pattern-match to generic LLM advice and don't isolate the specific defect. If a hypothesis starts to look like one of these, the agent is told to dig deeper or drop it.

## Self-checks before emission

Before the model returns a report, it walks eight self-checks: evidence-supports-claim (does the cited evidence actually back the claim?), structural distinctness (would two hypotheses be fixed by the same edit?), layer assignment, applyability of proposed changes, no forbidden patterns, no claiming a rule is missing when the rule exists, line-number verification (does the cited number match the prefix on the quoted content?), and structured-edit verification (does each `expected_content` string match the file verbatim, and are the file paths and line numbers in the structured block consistent with the prose?).

The line-number check exists because earlier iterations of the agent reliably fabricated line numbers when source was shown unnumbered. Numbering the source plus the self-check turned citation accuracy from 0/9 to 9/9 on the worked example. The structured-edit check exists because the applier refuses to write a single byte unless every `expected_content` matches exactly; an unverified edit becomes a hypothesis that can't be tried.

## Tests

```bash
python -m pytest tests/
```

87 tests cover the loader, eval analyzer, prompt assembler (including line-numbering and Jinja-brace survival), hypothesis agent (via a stub client; no API calls), the applier (report parsing, all four edit actions, verbatim verification, multi-edit composition, overlap detection, dry-run, non-applyable opt-out), the delta module (pass-rate change, target-scenario flip, collateral flips, markdown rendering), the iterate orchestrator (per-hypothesis apply-eval-revert sequencing, skip handling, best-result selection, baseline reuse), and the comparison renderer (ranked table, delta arrows, regression flagging).

## License

MIT.
