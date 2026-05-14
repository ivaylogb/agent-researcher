# Iteration report: scenario 107

## Summary

Best result: H1 (pass_rate 0.857 → 1.000, target scenario fixed, no regressions).

## Baseline

- Pass rate: 0.857 (6/7)
- Target scenario: `107`
- Baseline eval runtime: 30.4s

## Hypothesis comparison

| Hypothesis | Layer | Status | Pass-rate Δ | Target | Other flips |
|---|---|---|---|---|---|
| H1 | Layer 3 | applied | ↑ +0.143 | fixed | 0 |
| H2 | Layer 1 | applied | · +0.000 | unchanged | 0 |
| H3 | Layer 4 | skipped | — | — | — |

## Per-hypothesis detail

### H1 (Layer 3) — applied

**Claim:** The classification prompt contains the correct rule for this case — "Mixed signals … → `unknown`" — but it is the fourth bullet in a "Calibration notes" section that appears after the intents (lines 8–22) and the confidence-scoring bands (lines 26–33). The model matches the strong `bug` signals first (stack trace, version number, "expected X but got Y"), commits to `bug`, and never returns to apply the conflict-resolution rule.

- Pass rate: 0.857 → 1.000 (↑ +0.143)
- Target scenario `107`: target scenario fixed
- Edits applied: 1
- Duration: 27.0s
- Other flips: none
- Files modified during this iteration:
  - `../agent-skill-kit/reference_agent/prompts/classification.j2`

### H2 (Layer 1) — applied

**Claim:** The classification prompt explicitly defines the 0.7–0.9 band as "Signals point to one intent, but there's some ambiguity (e.g., a bug report that could also be a docs question)" — which is almost exactly issue 107. The agent followed this rule, returned `bug` at 0.75, and the router correctly did not escalate (0.75 ≥ threshold 0.7). The eval's expected `unknown` contradicts the agent's own documented calibration band. Either the band should be tightened or the eval's expected answer is wrong.

- Pass rate: 0.857 → 0.857 (· +0.000)
- Target scenario `107`: target scenario unchanged
- Edits applied: 1
- Duration: 28.3s
- Other flips: none
- Files modified during this iteration:
  - `../agent-skill-kit/reference_agent/prompts/classification.j2`

### H3 (Layer 4) — skipped

**Claim:** The workflow has exactly one gate between classification and a flow: a confidence-threshold check at runner.py:270. There is no separate pass that inspects classification reasoning for cross-intent conflict language. The agent's only chance to catch "either the docs are wrong or this is a regression" is inside the single classification call — and once the model commits to `bug` at 0.75, the runner has no mechanism to second-guess it. A dedicated lightweight check (e.g., a regex or a second-model pass over classification.reasoning for disjunctive intent language) would catch this independently of how well the classification prompt is written.

**Skipped.** Requires adding a new helper function plus a new conditional branch in triage(), and the natural place for the helper is either a new module or a multi-line insertion in runner.py that interleaves with the existing dispatch logic. This is not a clean in-place edit on a small range and shouldn't be forced into the supported edit actions.

## Runtime

- Total: 85.7s
- Baseline: 30.4s
- Hypotheses processed: 3
