# agent-researcher

A failure-diagnosis agent for other agents. When a target agent fails an eval, this reads the failing scenario and the target agent's source, then produces a small set of structured hypotheses about the cause.

Phase 1 is the hypothesis generator. It produces hypotheses; it does not apply them. Output is markdown: 2–3 ranked hypotheses, each assigned to one of the four agent-engineering layers, each citing specific `file:line` evidence from the target agent's code.

## The four layers

Hypotheses are categorized against the four-layer model from [agent-engineering](https://github.com/ivaylogb/agent-engineering):

1. **Evaluation** — the eval criterion itself
2. **Tools** — tool definitions, descriptions, schemas
3. **Context** — what reaches the model at decision time
4. **Workflow** — runtime architecture and orchestration

The system prompt requires every hypothesis to pick exactly one layer and cite evidence specific to that layer. Two hypotheses that would be fixed by the same edit get collapsed.

## Phase 1 scope

Produces hypotheses. Does not modify target-agent code, run evals, or iterate autonomously. Those are Phase 2 and Phase 3.

Phase 1 ships independently because hypothesis quality is the gate. If the generated hypotheses are distinct, grounded, and applyable, building a closed loop on top is worthwhile. If they collapse to generic prompt-tuning advice, a closed loop would amplify the noise.

## Install

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
python -m agent_researcher \
    --target-agent /path/to/reference_agent \
    --eval-result /path/to/eval_output.json \
    --scenario-id 107 \
    --scenario-input-file /path/to/scenario_input.txt \
    --output-file outputs/issue_107.md
```

Or via the installed entry point: `agent-researcher` accepts the same flags.

The target agent's source files (agent.yaml, prompts, runner.py, tools/) are loaded and shown to the model with each line prefixed by its 1-indexed line number. This is what lets the hypothesis report cite `file:line` evidence the reader can actually verify.

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

`examples/issue_107/` runs the agent against agent-skill-kit's reference_agent on the only failing scenario in its routing eval. The full produced report is at `examples/issue_107/report.md`; all 9 `file:line` citations in it were spot-checked against the real source files and all matched.

A taste of what the output looks like — the Layer categorization section from the worked example:

> This failure most likely sits in **Layer 1 (Evaluation)**.
>
> Reasoning: The agent classified this as `bug` with confidence 0.85, which exceeds the 0.7 threshold specified in `agent.yaml:30`. The classification prompt at `classification.j2:44` explicitly instructs "Mixed signals (e.g., 'the docs are wrong AND I think this is a bug') → `unknown`." The agent has a rule that directly addresses this case. However, the eval's expected answer is `unknown` while the agent followed a different interpretation: that the user's evidence (stack trace, version number, reproduction) constitutes a clear bug signal, and the docs disagreement is secondary context. The question is whether the eval's expectation reflects an agent rule that does not exist in writing, or whether the agent misapplied rule `classification.j2:44`.

The full report produces three structurally distinct hypotheses (one Layer 3, one Layer 1, one Layer 2) with applyable proposed changes. See `examples/issue_107/AUTHORING_BASELINE.md` for the methodology used to grade a run like this.

## Forbidden hypothesis patterns

The system prompt rejects hypotheses that look like:

- "Make the prompt clearer"
- "Add more examples"
- "Lower the temperature"
- "Use a stronger model"
- "The model is confused"

These pattern-match to generic LLM advice and don't isolate the specific defect. If a hypothesis starts to look like one of these, the agent is told to dig deeper or drop it.

## Self-checks before emission

Before the model returns a report, it walks seven self-checks: evidence-supports-claim (does the cited evidence actually back the claim?), structural distinctness (would two hypotheses be fixed by the same edit?), layer assignment, applyability of proposed changes, no forbidden patterns, no claiming a rule is missing when the rule exists, and line-number verification (does the cited number match the prefix on the quoted content?).

The line-number check exists because earlier iterations of the agent reliably fabricated line numbers when source was shown unnumbered. Numbering the source plus the self-check turned citation accuracy from 0/9 to 9/9 on the worked example.

## Roadmap

- **Phase 2**: take a hypothesis, apply the proposed change to the target agent, re-run the eval, report the delta.
- **Phase 3**: loop Phase 2 across N iterations autonomously.

Neither is built yet. The condition for moving to Phase 2 was that Phase 1's hypotheses against `examples/issue_107/` come out distinct, grounded, and applyable. That bar was met on the run committed in this repo.

## Tests

```bash
python -m pytest tests/
```

28 tests cover the loader, eval analyzer, prompt assembler (including line-numbering and Jinja-brace survival), and hypothesis agent (via a stub client; no API calls in tests).

## License

MIT.
