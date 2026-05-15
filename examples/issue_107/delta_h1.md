# Apply-and-re-eval delta: scenario 107

_Worked example output. File paths reference the agent under test (reference_agent in agent-skill-kit) as it was structured at run time._

## Hypothesis applied

**Hypothesis 1: Mixed-signal guidance is buried and loses salience competition (Layer 3)**

The "mixed signals" instruction at classification.j2:44 exists but sits at the end of a long "Calibration notes" section after 43 lines of other guidance. When the issue contains strong bug signals (traceback, version number, reproduction steps), the model pattern-matches to `bug` before reaching the mixed-signals check. The instruction fires only when the ambiguity is the *primary* feature of the issue, not when it's competing with strong category signals.

## Summary

| Metric | Before | After | Δ |
|---|---|---|---|
| Pass rate | 0.857 | 1.000 | ↑ +0.143 |
| Passed / total | 6 / 7 | 7 / 7 | ↑ +1 |

## Target scenario (107)

✓ FIXED — the target scenario now passes.

- Before: expected=unknown, predicted=bug @ 0.75 (fail)
- After:  expected=unknown, predicted=unknown @ 0.60 (pass)

## Other flips

No other scenarios flipped.

## Per-scenario state

| Scenario | Before | After | Status |
|---|---|---|---|
| `101` | expected=bug, predicted=bug @ 0.95 (pass) | expected=bug, predicted=bug @ 0.95 (pass) | — |
| `102` | expected=feature, predicted=feature @ 0.95 (pass) | expected=feature, predicted=feature @ 0.95 (pass) | — |
| `103` | expected=docs, predicted=docs @ 0.95 (pass) | expected=docs, predicted=docs @ 0.95 (pass) | — |
| `104` | expected=security, predicted=security @ 0.99 (pass) | expected=security, predicted=security @ 0.98 (pass) | — |
| `105` | expected=paid_support, predicted=paid_support @ 0.95 (pass) | expected=paid_support, predicted=paid_support @ 0.95 (pass) | — |
| `106` | expected=code_review, predicted=code_review @ 0.95 (pass) | expected=code_review, predicted=code_review @ 0.95 (pass) | — |
| `107` | expected=unknown, predicted=bug @ 0.75 (fail) | expected=unknown, predicted=unknown @ 0.60 (pass) | fixed |

## How to revert

The applier did NOT revert these edits. The operator decides whether to keep them. Files written by this run:

- `../agent-skill-kit/reference_agent/prompts/classification.j2`

To revert, run `git checkout HEAD --` followed by the file paths above (inside the target agent's git repo, if any), or restore from your own backup.
