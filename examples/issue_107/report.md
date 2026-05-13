# Hypothesis report: reference_agent / issue 107

## Failure summary

- Scenario: issue 107
- Input: User reports docs say `parse()` returns `None` on invalid input, but version 2.3.1 raises `ParseError`. User tested both and confirms the code raises. Explicitly calls it "either the docs are wrong or this is a regression."
- Expected: `unknown`
- Predicted: `bug` (confidence 0.85)
- Notes from eval: "Mixed signals: docs disagree with code AND user calls it a regression. Should escalate as unknown rather than pick one."

## Layer categorization

This failure most likely sits in **Layer 1 (Evaluation)**.

Reasoning: The agent classified this as `bug` with confidence 0.85, which exceeds the 0.7 threshold specified in agent.yaml:30. The classification prompt at classification.j2:44 explicitly instructs "Mixed signals (e.g., 'the docs are wrong AND I think this is a bug') → `unknown`." The agent has a rule that directly addresses this case. However, the eval's expected answer is `unknown` while the agent followed a different interpretation — that the user's evidence (stack trace, version number, reproduction) constitutes a clear bug signal, and the docs disagreement is secondary context. The question is whether the eval's expectation reflects an agent rule that doesn't exist in writing, or whether the agent misapplied rule classification.j2:44.

Secondary candidates: Layer 3 (the mixed-signals rule may not be salient enough when competing with strong bug signals), Layer 4 (no explicit disambiguation step between classification and flow dispatch).

## Hypotheses

### Hypothesis 1: The classification prompt's mixed-signals rule is present but under-weighted (Layer 3)

**Claim:** The classification prompt has an explicit rule at line 44 for mixed signals, but it appears after nine lines of intent-specific guidance and competes with strong bug signals (stack trace, version number, reproduction steps). The model applies the more salient bug-classification pattern because the mixed-signals rule is a single mention in a "calibration notes" section rather than part of the core intent definitions.

**Evidence:**
- classification.j2:10: Bug intent defined with signals "error messages, stack traces, 'expected X but got Y', reproduction steps, version numbers" — all present in issue 107
- classification.j2:44: Mixed-signals rule exists: "Mixed signals (e.g., 'the docs are wrong AND I think this is a bug') → `unknown`. Let a human disambiguate."
- classification.j2:24-33: Confidence scoring section makes no reference to mixed signals reducing confidence
- The scenario input contains: stack trace ("`Traceback...ParseError`"), version number ("2.3.1"), reproduction code, AND the phrase "Either the docs are wrong or this is a regression" — exactly matching both bug signals and the mixed-signals example

**Proposed change:** Move the mixed-signals rule from classification.j2:44 to classification.j2:22 (immediately after the fallback intent definition, before confidence scoring). Reframe it as a pre-check: "**Before scoring confidence:** If the issue presents evidence for multiple conflicting intents (e.g., 'the docs say X but the code does Y' or 'this could be a bug OR a feature gap'), classify as `unknown` with confidence 0.6 regardless of individual signal strength. The human will disambiguate."

**How to verify:** Issue 107's predicted_intent should change from `bug` to `unknown`, moving the routing eval's pass_rate from its current value toward the 0.90 target specified in agent.yaml:56. Specifically, this case should contribute +1 to passed scenarios.

### Hypothesis 2: The eval expects a tie-break rule the agent doesn't have (Layer 1)

**Claim:** The eval's expected answer (`unknown`) assumes the agent should escalate whenever a user explicitly frames uncertainty ("either the docs are wrong or this is a regression"), but no such rule exists in the agent's prompts. The agent correctly applies its documented logic: the issue has unambiguous bug signals (stack trace, reproduction, version number), so it classifies as `bug` with high confidence. The classification.j2:44 rule addresses "mixed signals" as conflicting evidence, but issue 107 presents converging evidence (all signals point to incorrect code behavior); the user's uncertainty is about root cause (docs vs. regression), not about whether a bug exists.

**Evidence:**
- classification.j2:10: Bug definition includes all signals present in issue 107
- classification.j2:44: Mixed-signals rule says "e.g., 'the docs are wrong AND I think this is a bug'" — but the user is not presenting docs-wrong as an alternative to bug; they're presenting it as a possible explanation for the bug
- agent.yaml:30: Confidence threshold is 0.7; the agent's 0.85 confidence exceeds this
- classification.j2:26-29: Confidence range 0.7–0.9 is defined as "Signals point to one intent, but there's some ambiguity" — which describes this case, and the agent classified accordingly

**Proposed change:** This is not an agent defect; it's an eval calibration issue. The eval's golden set should be updated to reflect the agent's actual policy. Specifically, issue 107's expected_intent should change from `unknown` to `bug`, because the agent has no written rule that user uncertainty about root cause triggers escalation when signals are otherwise clear. If the policy should be "escalate when user explicitly states uncertainty," add that rule explicitly at classification.j2:23: "`unknown` — None of the above clearly apply, OR signals are ambiguous between two categories, OR the user explicitly frames the issue as uncertain (phrases like 'either X or Y', 'not sure if', 'could be')."

**How to verify:** After updating the golden set, issue 107 should pass with predicted_intent=`bug` matching the new expected_intent=`bug`. This verifies that the agent is correctly following its documented rules and the eval was misaligned.

### Hypothesis 3: No explicit disambiguation between "docs wrong" and "code wrong" (Layer 2)

**Claim:** The agent has no tool or workflow step to distinguish between "documentation gap" and "code regression." Issue 107 requires answering "is this a docs bug or a code bug?" before classification, but the agent's tool set (github_issues, github_search, codeowners_lookup) cannot help with this determination. The classification prompt forces a single-intent decision when the correct answer is "this issue spans two intents: docs AND bug, priority-order unknown."

**Evidence:**
- classification.j2:4: "Classify the following GitHub issue into exactly one intent" — no provision for multi-intent issues
- agent.yaml:18-27: Intent list has `bug` and `docs` as separate intents, but no `docs_bug` or `mixed_intent` option
- classification.j2:10: Bug intent focuses on code behavior
- classification.j2:12: Docs intent focuses on documentation quality
- Issue 107 simultaneously reports incorrect code behavior (raises instead of returning None) and incorrect documentation (docs say it returns None)

**Proposed change:** Add a new tool `check_api_history` with description: "Look up the documented API contract for a function or method across versions. Returns the documented behavior and the version where it was last updated. Use this when a user reports that docs and code disagree — it helps determine whether this is a code regression or stale documentation." The tool would enable the bug_flow to distinguish these cases. Additionally, add to classification.j2:22: "`docs_bug` — The issue reports that documentation contradicts actual behavior. Route to `bug` flow but flag for docs team follow-up."

**How to verify:** With the new tool, issue 107's flow would call `check_api_history("parse")`, discover the documented behavior, and classify as `bug` with a note that docs need updating (or discover docs were recently updated and classify as `docs` with a note that code regressed). The scenario might still predict `bug` rather than `unknown`, but the handoff would contain structured context about the docs/code conflict, improving downstream triage quality. This hypothesis predicts the eval would still fail unless the eval is also updated to accept `bug` or `docs` with appropriate handoff context.

## What this report is NOT

- These are hypotheses, not verified fixes. Each requires a re-eval to confirm.
- These are the strongest candidates the investigation surfaced. Other theories were considered and dropped because they lacked grounding in the evidence.
- This report does not prescribe which hypothesis to try first. That depends on the cost of each change and the operator's judgment about the system's invariants.
