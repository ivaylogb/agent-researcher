# Investigation request

You are investigating a failure in the **{target_agent_name}** agent.

## The failing scenario

```json
{eval_failure_json}
```

## The scenario input (what the user said)

```
{scenario_input}
```

## The target agent's code

Every file below is shown with each line prefixed by its 1-indexed line number in the form `{{N:4d}}  {{line}}` (4-char right-aligned gutter, two spaces, then the line content — blank lines are also numbered). When you cite evidence as `file:line`, use the exact number shown on the prefix of the line you are quoting. Do not count lines yourself.

### agent.yaml (manifest)

```yaml
{agent_yaml}
```

### system.j2 (system prompt)

```
{system_prompt}
```

### classification.j2 (classification prompt)

```
{classification_prompt}
```

{additional_files_section}

## Context: the four-layer model

This agent was built using the four-layer agent engineering methodology. The layers (as defined in your system prompt) are:

- **Layer 1 (Evaluation)**: the eval criterion itself. Compare the eval's expected answer against the rules the agent has actually been given (prompts, manifest thresholds, calibration bands). If the agent followed its documented rules and the eval still marks it wrong, the eval's expected answer is the most likely defect — not the agent. Specifically check whether any explicit threshold or confidence band in the agent's code supports the eval's expected outcome given the observed prediction; if not, you may be looking at an L1 failure.
- **Layer 2 (Tools)**: tool definitions, descriptions, schemas
- **Layer 3 (Context)**: what information reaches the model at decision time — including which instructions are loaded, where they sit in the prompt, and whether critical guidance is buried or salient
- **Layer 4 (Workflow)**: runtime architecture, classify-then-dispatch, handoff templates

Use these definitions when categorizing the failure's most likely layer. The system prompt's definitions are authoritative; the bullets above are reminders.

## Your task

Produce a hypothesis report following the structure specified in your system prompt. Generate 2-3 distinct hypotheses, ranked by likelihood. Each must cite specific evidence (file:line) from the agent code shown above.

Begin the report now.
