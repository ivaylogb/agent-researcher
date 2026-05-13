# Example: issue 107

The canonical worked example for Phase 1. Used both as a usage demo and as the failure case the system prompt was iterated against.

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

If Phase 1 produces useful hypotheses for issue 107, the same approach should produce useful hypotheses for other class-1 and class-2 failures (prompt and tool issues). If it produces only generic guesses, the closed loop should not be built on top.

## Running it

```bash
python -m agent_researcher \
    --target-agent ../agent-skill-kit/reference_agent \
    --eval-result ../agent-skill-kit/reference_agent/evals/routing/last_run.json \
    --scenario-id 107 \
    --scenario-input-file /path/to/issue_107_input.txt \
    --output-file outputs/issue_107.md
```

Adjust paths for your local checkout. The user message text for scenario 107 is the title + body from the eval's input fixture.

## The produced report

`report.md` in this directory is the actual run. All 9 `file:line` citations in that report were spot-checked against the real reference_agent files; all match.

The report categorizes the failure as Layer 1 (the eval's expected answer may not be justified by the agent's documented rules) and produces three structurally distinct hypotheses:

- H1 (Layer 3): the mixed-signals rule is present at `classification.j2:44` but buried in a calibration-notes section below the confidence-scoring rules
- H2 (Layer 1): the eval expects `unknown` for a 0.85-confidence case, but the agent's rules only direct `unknown` for the 0.5–0.7 band, so the disagreement may be between the eval author and the agent's documented behavior
- H3 (Layer 2): no tool exists to disambiguate "docs wrong" vs "code wrong"

## Grading a future run

`AUTHORING_BASELINE.md` is the methodology doc for writing your own grading bar against a future run (different scenario, different target agent, or a re-run after a fix). Read it before grading anything that isn't this exact scenario.
