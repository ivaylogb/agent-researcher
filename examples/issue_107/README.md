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

`report.md` in this directory is the actual `diagnose` run. 4 of the report's `file:line` citations were spot-checked against the real reference_agent source during the run; all matched. The citation format is line-numbered, so any reader can verify the others against the live source.

The report categorizes the failure as Layer 3 — the right tie-break rule exists in the classification prompt but is structurally buried — with Layer 1 and Layer 4 as secondary candidates, and produces three structurally distinct hypotheses:

- H1 (Layer 3): the mixed-signals rule is present at `classification.j2:44` but buried in a calibration-notes section below the intents and confidence bands; the model matches the prominent `bug` signals first and never returns to apply the conflict-resolution rule
- H2 (Layer 1): the 0.7–0.9 confidence band's example wording ("a bug report that could also be a docs question") legitimizes the agent's `bug @ 0.75` prediction, so the eval's expected `unknown` is inconsistent with the documented calibration
- H3 (Layer 4): no structural conflict-detection step exists between classification and dispatch; the only post-classification gate is the confidence threshold at `runner.py:270`

Each hypothesis ships a structured edit spec. H1 and H2 ship `applyable: true` specs; H3 ships `applyable: false` because the change requires a new helper function plus a new conditional branch in `triage()`, which doesn't fit the in-place edit actions the applier supports.

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

## Running iterate

```bash
python -m agent_researcher iterate \
    --hypothesis-report examples/issue_107/report.md \
    --target-agent ../agent-skill-kit/reference_agent \
    --eval-command "../agent-skill-kit/.venv/bin/python -m reference_agent.evals.routing.run_eval" \
    --output-file outputs/issue_107_iteration.md
```

`iterate` runs every applyable hypothesis in the report against the same baseline. For this report that means H1 and H2 each get an apply-eval-revert cycle; H3 is skipped because its structured-edit block is `applyable: false`. The output is a comparison table showing which hypothesis actually moved the eval, plus per-hypothesis detail (pass-rate delta, target-scenario flip, other flips, files modified). The hypothesis with the best measured outcome is highlighted in the summary.

The revert between hypotheses is what makes the comparison meaningful: H2 is evaluated against the original baseline, not against the state H1 left behind. If H2 had been evaluated on top of H1, the result would conflate two changes.

## The produced iteration

`iteration.md` in this directory is the actual `iterate` run.

- Baseline: 0.857 (6/7)
- H1 (Layer 3): 0.857 → 1.000 (↑ +0.143), scenario 107 fixed, no other flips
- H2 (Layer 1): 0.857 → 0.857 (no change), scenario 107 still failing, no other flips
- H3 (Layer 4): skipped (`applyable: false`)
- Best: H1

H2's edit applied cleanly to the same file H1 edited (`classification.j2`), so the no-change result is meaningful: tightening the 0.7–0.9 confidence band's wording did not change the model's prediction on this scenario. The Layer 3 framing of the failure (the right rule existed but was structurally buried) is the one that produced a measured improvement. The Layer 1 framing (the calibration band itself was the issue) was falsified.

Worth knowing: this run modified `classification.j2` twice — once for H1, once for H2 — and reverted both times. The agent-skill-kit working tree was clean before the run, between hypotheses, and after. Same teaching-artifact protection as the `apply` section: reference_agent stays broken on purpose for future `diagnose` runs.

A note on variance: this is one run. The eval was re-run five times against the unmodified reference_agent before this iteration; scenario 107 classified as `bug` 5/5 times with confidence ranging 0.75–0.85, so the failure itself is reproducible. The deltas in `iteration.md` are single-run signals — H1's +0.143 is directional evidence that the salience theory was correct, not a verified ranking. Re-run the iteration if you need confidence in the ordering.

## Grading a future run

`AUTHORING_BASELINE.md` is the methodology doc for writing your own grading bar against a future run (different scenario, different target agent, or a re-run after a fix). Read it before grading anything that isn't this exact scenario.
