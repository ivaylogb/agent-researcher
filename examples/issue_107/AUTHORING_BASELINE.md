# Authoring a baseline to grade against

`report.md` in this directory is one real run of the agent. If you want to grade a future run (or a different scenario), you need a baseline — the hypotheses you'd accept as correct — written independently of the run.

An earlier version of this baseline was written against an *assumed* shape of `classification.j2` without reading the actual file. Code review caught that the assumed shape was wrong: the prompt already contained rules the assumed report claimed were missing. This doc captures how to avoid that.

## The point of a baseline

The baseline is the **quality bar** you grade the agent's output against. If the bar is wrong, the comparison is meaningless. A wrong bar can either inflate or deflate the assessment, and you won't know which.

Two failure modes when authoring it:

1. **Assumed-not-read** (the one we hit): writing hypotheses about what's "missing" from the prompt without reading the prompt. The agent will read the file and notice the gap between your assumption and reality.
2. **Cherry-picked**: writing exactly the hypotheses you want the agent to produce. The bar becomes a key the agent's output is judged against, rather than an independent benchmark.

## How to write it correctly

Before drafting any hypothesis, read every file the agent will read:

```bash
cat ../agent-skill-kit/reference_agent/agent.yaml
cat ../agent-skill-kit/reference_agent/prompts/system.j2
cat ../agent-skill-kit/reference_agent/prompts/classification.j2
cat ../agent-skill-kit/reference_agent/prompts/handoff.j2
cat ../agent-skill-kit/reference_agent/prompts/bug_flow.j2
cat ../agent-skill-kit/reference_agent/prompts/feature_flow.j2
cat ../agent-skill-kit/reference_agent/prompts/docs_flow.j2
cat ../agent-skill-kit/reference_agent/runner.py | head -200
cat ../agent-skill-kit/reference_agent/evals/routing/last_run.json
```

Look specifically for:
- What rules and guidance already exist in the prompts (so you don't claim they're absent)
- Where rules sit (intent definitions vs calibration notes vs runtime config)
- Numeric thresholds (`routing.confidence_threshold`, model-confidence bands)
- The exact failure data (predicted intent, predicted confidence)

Then write hypotheses that are **structurally distinct** and **grounded in the actual files**.

## The strongest L1 reading for issue 107 (from code review)

Code review identified a Layer 1 hypothesis the first draft of the baseline missed entirely. Reproduced here as a seed for any future authoring pass:

> The agent's rules in `classification.j2` say to output `unknown` at confidence 0.5-0.7. The model came in at 0.85 — *above* the rule's mixed-signal band. Both the rule and the threshold (`agent.yaml: routing.confidence_threshold: 0.7`) are internally consistent with each other. But the eval's expected answer of `unknown` for a 0.85-confidence case is not justified by any rule the agent has been given. The disagreement may be between the eval author's intent and the agent's documented behavior — not between the agent and reality.

This is a real Layer 1 hypothesis (the eval criterion may be the problem, not the agent). It's structurally distinct from any L2/L3/L4 hypothesis. It's also testable: change the eval's expected answer to `bug` and the pass rate goes to 7/7 immediately, with no agent change.

Use it as one of your three hypotheses, alongside one L2 (tool schema) and one L3/L4 (prompt structure) candidate.

## The shape of the report

The agent emits this structure; your baseline should follow the same outline so the comparison is apples to apples.

```markdown
# Hypothesis report: reference_agent / 107

## Failure summary
[from last_run.json — quote actual numbers]

## Layer categorization
[which layer most likely, why, what other candidates]

## Hypotheses

### Hypothesis 1: [name] (Layer N)
**Claim:** ...
**Evidence:** [real file:line citations]
**Proposed change:** ...
**How to verify:** ...

### Hypothesis 2: ...
### Hypothesis 3: ...

## What this report is NOT
...
```

## What good looks like

After writing it, ask of every hypothesis:

- Did I actually open the file and read the lines I'm citing?
- If my claim is "the rule is missing," can I show that the file does not contain that rule anywhere?
- If my claim is "the rule exists but is wrong," can I quote the rule verbatim?
- Are my three hypotheses distinct *theories* (different mechanisms), not three variations of one fix?

If you can answer yes to all four for each hypothesis, the baseline is honest. The agent's run can then be fairly compared against it.
