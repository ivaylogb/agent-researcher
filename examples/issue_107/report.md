# Hypothesis report: reference_agent / issue 107

## Failure summary

- Scenario: issue 107
- Input: User reports that documentation says `parse()` returns `None` on invalid input but the code raises `ParseError` in 2.3.1, framing it as "either docs are wrong or this is a regression"
- Expected: `unknown`
- Predicted: `bug` (confidence 0.75)
- Notes from eval: Mixed signals: docs disagree with code AND user calls it a regression. Should escalate as unknown rather than pick one.

## Layer categorization

This failure most likely sits in **Layer 3 (Context)**.

Reasoning: The classification prompt *does* contain a tie-break rule for mixed signals (classification.j2:44 — "Mixed signals (e.g., 'the docs are wrong AND I think this is a bug') → `unknown`"), which is almost a verbatim match for issue 107's framing. But this rule lives in a "Calibration notes" bullet list near the end of the prompt, after the intents and confidence bands, where it competes with the much more prominent `bug` signals enumerated at line 10 ("error messages, stack traces, … reproduction steps, version numbers") — all of which issue 107 also contains. The model picked the salient prominent signal-set match over the buried tie-break rule. A secondary possibility is Layer 1 (the confidence band at line 29 actually permits 0.7–0.9 for "some ambiguity", which arguably justifies the agent's 0.75 prediction — making the eval's expected `unknown` inconsistent with the documented calibration).

Secondary candidates: Layer 1, Layer 4.

## Hypotheses

### Hypothesis 1: The mixed-signals rule is buried below the intent definitions and confidence bands (Layer 3)

**Claim:** The classification prompt contains the correct rule for this case — "Mixed signals … → `unknown`" — but it is the fourth bullet in a "Calibration notes" section that appears after the intents (lines 8–22) and the confidence-scoring bands (lines 26–33). The model matches the strong `bug` signals first (stack trace, version number, "expected X but got Y"), commits to `bug`, and never returns to apply the conflict-resolution rule.

**Evidence:**
- classification.j2:10: bug signals include "error messages, stack traces, 'expected X but got Y', reproduction steps, version numbers" — all present in issue 107
- classification.j2:44: "Mixed signals (e.g., 'the docs are wrong AND I think this is a bug') → `unknown`. Let a human disambiguate." — this rule directly describes issue 107 but is buried as the last bullet of calibration notes
- classification.j2:29: confidence band "0.7–0.9 — Signals point to one intent, but there's some ambiguity" — gives the model permission to commit at 0.75 rather than down-shift into the unknown band
- Predicted confidence is 0.75 with intent `bug`, exactly what the 0.7–0.9 band invites when bug signals are visible

**Proposed change:** Promote the mixed-signals tie-break to a top-of-prompt rule, before intents are introduced, framed as a pre-classification check. Add a sentence at the start of the classification instructions that the model must apply before scanning for signals.

```json
{
  "applyable": true,
  "edits": [
    {
      "file": "classification.j2",
      "action": "insert_after",
      "at_line": 4,
      "new_content": "\n## Before you classify: check for conflicting frames\n\nIf the issue body presents the problem as a disjunction the user themselves cannot resolve — e.g., \"either the docs are wrong OR this is a bug\", \"is this a regression or am I misreading the docs\", code-vs-docs disagreement, or any \"X or Y\" framing where X and Y are different intents — return `unknown` with confidence ≤ 0.7. Do not pick the intent whose signals look strongest; the user has explicitly told you they cannot tell which frame applies, and a human needs to disambiguate. This rule takes precedence over the per-intent signal lists below."
    }
  ]
}
```

**How to verify:** Issue 107's predicted_intent should change from `bug` to `unknown` (with confidence ≤ 0.7), making the scenario pass. Re-run the full routing golden set; pass_rate should rise by 1/N for whatever N is. Watch for regressions on legitimate single-intent bug reports that happen to mention docs in passing — those should still classify as `bug`.

### Hypothesis 2: The 0.7–0.9 confidence band legitimizes the agent's prediction, so the eval's expected `unknown` is inconsistent with the documented calibration (Layer 1)

**Claim:** The classification prompt explicitly defines the 0.7–0.9 band as "Signals point to one intent, but there's some ambiguity (e.g., a bug report that could also be a docs question)" — which is almost exactly issue 107. The agent followed this rule, returned `bug` at 0.75, and the router correctly did not escalate (0.75 ≥ threshold 0.7). The eval's expected `unknown` contradicts the agent's own documented calibration band. Either the band should be tightened or the eval's expected answer is wrong.

**Evidence:**
- classification.j2:29: "`0.7–0.9` — Signals point to one intent, but there's some ambiguity (e.g., a bug report that could also be a docs question)" — this is the agent's documented behavior for exactly issue 107
- classification.j2:30: "`0.5–0.7` — The issue is mixed or unclear. **You should output `unknown` at this confidence range.**" — only the 0.5–0.7 band is told to produce unknown
- agent.yaml:30: `confidence_threshold: 0.7` — and predicted 0.75 clears it
- The bug-or-docs example at line 29 is a near-exact match for issue 107's framing

**Proposed change:** Tighten the 0.7–0.9 band so that "ambiguity between two intents" is not an acceptable reason to stay above 0.7. Replace the example so that the 0.7–0.9 band only covers within-intent uncertainty (e.g., severity unclear, component unknown), and move cross-intent ambiguity firmly into the 0.5–0.7 unknown range.

```json
{
  "applyable": true,
  "edits": [
    {
      "file": "classification.j2",
      "action": "replace",
      "from_line_start": 29,
      "from_line_end": 30,
      "expected_content": "- `0.7–0.9` — Signals point to one intent, but there's some ambiguity (e.g., a bug report that could also be a docs question).\n- `0.5–0.7` — The issue is mixed or unclear. **You should output `unknown` at this confidence range.**",
      "new_content": "- `0.7–0.9` — Signals point to one intent. Any remaining uncertainty is within that intent (e.g., severity unclear, component not yet identified), not between intents.\n- `0.5–0.7` — The issue is mixed or unclear, OR signals point to two different intents (e.g., a bug report that could also be a docs question). **You should output `unknown` at this confidence range.**"
    }
  ]
}
```

**How to verify:** Issue 107's predicted_confidence should drop below 0.7 (the cross-intent ambiguity now lives in the 0.5–0.7 band), causing the runtime gate at runner.py:270 to set intent=`unknown` and escalate. Re-run golden set; cases where the model was inflating to 0.7+ on cross-intent ambiguity should shift to unknown, while clean single-intent cases should be unaffected.

### Hypothesis 3: No structural conflict-detection step exists between classification and dispatch (Layer 4)

**Claim:** The workflow has exactly one gate between classification and a flow: a confidence-threshold check at runner.py:270. There is no separate pass that inspects classification reasoning for cross-intent conflict language. The agent's only chance to catch "either the docs are wrong or this is a regression" is inside the single classification call — and once the model commits to `bug` at 0.75, the runner has no mechanism to second-guess it. A dedicated lightweight check (e.g., a regex or a second-model pass over classification.reasoning for disjunctive intent language) would catch this independently of how well the classification prompt is written.

**Evidence:**
- runner.py:270–278: the only post-classification gate is `confidence < threshold`; there is no inspection of `reasoning` content or detection of multi-intent language
- runner.py:281–282: dispatch immediately follows the threshold check; once `bug` clears 0.7 there is no further filtering
- classification.j2:120 (schema): the `reasoning` field is captured but only used downstream as a display string in the handoff (handoff.j2:73), not as input to any conflict-detection logic
- The failing case shows the only defense against multi-intent issues is a single bullet in a long prompt — a structurally fragile design

**Proposed change:** This requires adding a new function (e.g., `detect_cross_intent_conflict(classification, issue)`) and wiring it into runner.py between the threshold check and dispatch, downgrading intent to `unknown` when conflict signals are detected. This is a multi-line cross-cutting addition rather than a small in-place edit.

```json
{
  "applyable": false,
  "reason": "Requires adding a new helper function plus a new conditional branch in triage(), and the natural place for the helper is either a new module or a multi-line insertion in runner.py that interleaves with the existing dispatch logic. This is not a clean in-place edit on a small range and shouldn't be forced into the supported edit actions."
}
```

**How to verify:** Issue 107 should be caught by the new conflict-detection step (cross-intent disjunction in the issue body or in classification.reasoning) and downgraded to `unknown`, making the scenario pass. Importantly, this hypothesis predicts the fix should generalize to other cross-intent-ambiguity cases without requiring the classification prompt to anticipate every phrasing. Watch for false positives: legitimate bug reports that mention "the docs say X" as context should *not* trip the detector.

## What this report is NOT

- These are hypotheses, not verified fixes. Each requires a re-eval to confirm.
- These are the strongest candidates the investigation surfaced. Other theories were considered and dropped because they lacked grounding in the evidence.
- This report does not prescribe which hypothesis to try first. That depends on the cost of each change and the operator's judgment about the system's invariants.
