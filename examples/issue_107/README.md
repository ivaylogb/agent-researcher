# Example: issue 107

The canonical worked example. Used both as a usage demo and as the failure case the system prompt was iterated against.

## The failure

`agent-skill-kit`'s reference_agent (an issue-triage agent) has a routing eval with 7 scenarios. Scenario 107 is the one that fails:

- **Expected**: `unknown` (the model should escalate as ambiguous)
- **Predicted**: `bug` (confidence 0.85)
- **Notes from the eval**: "Mixed signals: docs disagree with code AND user calls it a regression. Should escalate as unknown rather than pick one."

The eval pass rate is 6/7 (0.857), below the 0.90 threshold.

## Why this is the right first test

Issue 107 is a *class-1 failure*:

1. Prompt-fixable — likely solvable by a change to the classification prompt or system prompt
2. Documented and reproducible — the eval notes call out the issue
3. Genuinely ambiguous — the "right answer" requires nuance the model can plausibly miss
4. Routing/classification — a clean target for hypothesis generation

If `diagnose` produces useful hypotheses for issue 107, the same approach should produce useful hypotheses for other class-1 and class-2 failures (prompt and tool issues).

## Running diagnose

```bash
python -m agent_researcher diagnose \
    --target-agent ../agent-skill-kit/reference_agent \
    --eval-result ../agent-skill-kit/reference_agent/evals/routing/last_run.json \
    --scenario-id 107 \
    --scenario-input-file /path/to/issue_107_input.txt \
    --output-file outputs/issue_107.md
```

Adjust paths for your local checkout. The user message text for scenario 107 is the title + body from the eval's input fixture.

## The produced report

`report.md` in this directory is the actual `diagnose` run. All 9 `file:line` citations in that report were spot-checked against the real reference_agent files; all match.

The report categorizes the failure as Layer 1 (the eval's expected answer may not be justified by the agent's documented rules) and produces three structurally distinct hypotheses:

- H1 (Layer 3): the mixed-signals rule is present at `classification.j2:44` but buried in a calibration-notes section below the confidence-scoring rules
- H2 (Layer 1): the eval expects `unknown` for a 0.85-confidence case, but the agent's rules only direct `unknown` for the 0.5–0.7 band, so the disagreement may be between the eval author and the agent's documented behavior
- H3 (Layer 2): no tool exists to disambiguate "docs wrong" vs "code wrong"

Each hypothesis ships a structured edit spec. H1 and H2 ship `applyable: true` specs; H3 ships `applyable: false` because it requires a new tool file rather than in-place edits.

## Running apply

```bash
python -m agent_researcher apply \
    --hypothesis-report examples/issue_107/report.md \
    --hypothesis-id 1 \
    --target-agent ../agent-skill-kit/reference_agent \
    --eval-command "../agent-skill-kit/.venv/bin/python -m reference_agent.evals.routing.run_eval" \
    --output-file outputs/issue_107_h1_delta.md
```

The interpreter path matters: the eval subprocess needs the target agent's dependencies (jinja2, anthropic, yaml). See the top-level README for the rationale.

## The produced delta

`delta_h1.md` in this directory is the actual `apply` run for hypothesis 1.

- Pass rate: 0.857 (6/7) → 1.000 (7/7)
- Target scenario 107: `bug @ 0.75 (fail)` → `unknown @ 0.60 (pass)`
- No other scenarios flipped status

Worth knowing: this run modified `reference_agent/prompts/classification.j2` in the agent-skill-kit checkout. The change was reverted afterwards because reference_agent is a teaching artifact that intentionally has one failing scenario for future `diagnose` runs. The delta report stands as the record; the source change does not.

## Grading a future run

`AUTHORING_BASELINE.md` is the methodology doc for writing your own grading bar against a future run (different scenario, different target agent, or a re-run after a fix). Read it before grading anything that isn't this exact scenario.
