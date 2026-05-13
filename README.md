# agent-researcher

A failure-diagnosis agent for other agents. When a target agent fails an eval, this reads the failing scenario and the target agent's source, produces a small set of structured hypotheses about the cause, and (in Phase 2) applies one of those hypotheses and re-runs the eval to measure the delta.

Two subcommands:

- `diagnose` (Phase 1): produce 2–3 ranked hypotheses for one failing scenario. Each hypothesis is assigned to one of the four agent-engineering layers and cites specific `file:line` evidence from the target agent's code. Each hypothesis also ships a structured edit spec so the change can be applied mechanically.
- `apply` (Phase 2): apply one hypothesis's edits to the target, re-run the eval, and emit a before/after delta report. The applier verifies every edit's expected content matches the file verbatim before writing; no edit, no eval run on a half-applied state.

## The four layers

Hypotheses are categorized against the four-layer model from [agent-engineering](https://github.com/ivaylogb/agent-engineering):

1. **Evaluation** — the eval criterion itself
2. **Tools** — tool definitions, descriptions, schemas
3. **Context** — what reaches the model at decision time
4. **Workflow** — runtime architecture and orchestration

The system prompt requires every hypothesis to pick exactly one layer and cite evidence specific to that layer. Two hypotheses that would be fixed by the same edit get collapsed.

## Scope

Phase 1 produces hypotheses. Phase 2 applies one hypothesis and measures the result of a single re-eval. Neither phase iterates autonomously across hypotheses; that's Phase 3.

Phase 1 ships independently because hypothesis quality is the gate. If the generated hypotheses are distinct, grounded, and applyable, building Phase 2 on top is worthwhile. If they collapse to generic prompt-tuning advice, a closed loop would amplify the noise. Phase 2 is built on the same gate: it relies on the model producing a structured edit spec whose `expected_content` strings match the file byte-for-byte, so the applier can reject drift rather than silently mis-edit.

## Install

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

### Phase 1: diagnose

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

### Phase 2: apply

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

`examples/issue_107/` runs the agent against agent-skill-kit's reference_agent on the only failing scenario in its routing eval. It carries two artifacts: the Phase 1 diagnosis (`report.md`) and the Phase 2 delta from applying hypothesis 1 (`delta_h1.md`).

A taste of what the Phase 1 output looks like — the Layer categorization section from the worked example:

> This failure most likely sits in **Layer 1 (Evaluation)**.
>
> Reasoning: The agent classified this as `bug` with confidence 0.85, which exceeds the 0.7 threshold specified in `agent.yaml:30`. The classification prompt at `classification.j2:44` explicitly instructs "Mixed signals (e.g., 'the docs are wrong AND I think this is a bug') → `unknown`." The agent has a rule that directly addresses this case. However, the eval's expected answer is `unknown` while the agent followed a different interpretation: that the user's evidence (stack trace, version number, reproduction) constitutes a clear bug signal, and the docs disagreement is secondary context. The question is whether the eval's expectation reflects an agent rule that does not exist in writing, or whether the agent misapplied rule `classification.j2:44`.

The full report produces three structurally distinct hypotheses (one Layer 3, one Layer 1, one Layer 2) with applyable proposed changes. All 9 `file:line` citations in it were spot-checked against the real source files; all matched. See `examples/issue_107/AUTHORING_BASELINE.md` for the methodology used to grade a Phase 1 run.

`delta_h1.md` shows the Phase 2 result of applying hypothesis 1: pass rate moved from 0.857 (6/7) to 1.000 (7/7), the target scenario flipped from `bug @ 0.75 (fail)` to `unknown @ 0.60 (pass)`, and no other scenarios changed status.

### Single-mechanism hypotheses for clean ablation

Hypothesis 1 in the worked example does two things at once: it `move`s the mixed-signals rule earlier in the prompt (a salience change, Layer 3) and `replace`s the rule with new wording that explicitly covers "either…or" phrasing (a phrasing change, Layer 1). The applier verifies and applies both. The re-eval verifies the combined edit improves the pass rate. What it cannot do is tell you which of the two mechanisms moved the needle.

Operators who want a clean attribution can author hypotheses with a single mechanism per spec. A `move` edit that re-inserts the original text verbatim isolates the salience theory. A `replace` edit at the original line isolates the phrasing theory. Run each as a separate Phase 2 invocation against a clean baseline. The model's default tendency is to bundle both into one hypothesis because the bundled version reads as more confident; the structured-edit format makes the bundle visible and ablatable.

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

The line-number check exists because earlier iterations of the agent reliably fabricated line numbers when source was shown unnumbered. Numbering the source plus the self-check turned citation accuracy from 0/9 to 9/9 on the worked example. The structured-edit check exists because Phase 2's applier refuses to write a single byte unless every `expected_content` matches exactly; an unverified edit becomes a hypothesis that can't be tried.

## Roadmap

- **Phase 3**: loop Phase 2 across N hypotheses (or N iterations of the same hypothesis with feedback) autonomously.

Not built yet. Phase 1 and Phase 2 ship in this repo. The condition for moving to Phase 3 is that Phase 2's apply-and-re-eval cycle works reliably across more than one target agent and more than one eval shape.

## Tests

```bash
python -m pytest tests/
```

60 tests cover the loader, eval analyzer, prompt assembler (including line-numbering and Jinja-brace survival), hypothesis agent (via a stub client; no API calls), the applier (report parsing, all four edit actions, verbatim verification, multi-edit composition, overlap detection, dry-run, non-applyable opt-out), and the delta module (pass-rate change, target-scenario flip, collateral flips, markdown rendering).

## License

MIT.
