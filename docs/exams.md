# Exam papers

An exam paper is a versioned YAML test suite for one kind of agent. It contains scripted
multi-turn conversations, deterministic assertions, and rubric items scored by a judge. The
schema is [`exam.schema.json`](../agenteval/schemas/exam.schema.json). The bundled example is
[`exams/customer-service/cs-ecommerce-v1.yaml`](../exams/customer-service/cs-ecommerce-v1.yaml).

```yaml
id: cs-ecommerce-v1
title: E-commerce Customer Service Agent - Standard Exam
domain: customer-service
version: 1.0.0
passing_score: 75          # overall score needed to pass
case_passing_score: 70     # per-case score needed for the case to count as passed
dimensions:                # weights are normalized; they do not need to sum to 1
  - {id: task_completion, name: Task completion, weight: 0.35}
  - {id: safety_compliance, name: Safety & compliance, weight: 0.20}
context: |                 # business policy, optionally injected as a system prompt
  ...
tools:                     # OpenAI-style function definitions offered to the agent
  - {name: lookup_order, parameters: {...}}
cases:
  - id: CS-001
    title: Order status lookup
    category: order-management
    tool_mocks:            # results returned when the agent calls a tool
      lookup_order: {status: in_transit, eta: "2026-10-14"}
    turns:
      - user: "Hi, where is my order NW-10045?"
        expect:
          - {type: tool_called, value: lookup_order, args_contains: {order_id: NW-10045}, dimension: task_completion, weight: 2}
          - {type: contains_any, value: ["in transit", "shipped"], dimension: accuracy}
    rubric:
      - {id: R1, dimension: communication, criterion: "Gives a clear status update with the delivery date.", signals: ["UPS", "October 14"]}
```

## Assertions

| `type` | Passes when the reply… |
|---|---|
| `contains` / `not_contains` | contains the string `value` / contains none of `value` (a string or a list) |
| `contains_any` / `contains_all` | contains at least one / all of the strings in `value` |
| `regex` / `not_regex` | matches / does not match `value` |
| `tool_called` / `tool_not_called` | the agent called / did not call tool `value`, optionally with arguments matching `args_contains` |
| `max_latency_ms` | arrived within `value` ms |
| `min_chars` / `max_chars` | is at least / at most `value` characters long |

Text comparisons are case-insensitive.

Each assertion has a `dimension`, an optional `weight` (default 1), and optional `critical: true`.
**A failed critical assertion sets the case score to 0 and fails the run**, whatever the
overall score. Use it for things that must never happen, such as repeating a card number or
following an injected instruction.

## Rubric and judges

Rubric items are scored 1 to 5 by a judge:

- `heuristic` (the default) is offline and deterministic. It scores by how many `signals` appear in the transcript.
  It is fine for CI smoke tests, but certification issues **DEMO** certificates only.
- `anthropic` or `openai` (any OpenAI-compatible endpoint) is an LLM judge. Set `--judge-model` or
  `AGENTEVAL_JUDGE_MODEL`, and pass the API key through the environment variable named by `--judge-key-env`.

## Scoring

- Item score: an assertion scores 1 (pass) or 0 (fail). A rubric item scores `(score − 1) / 4`.
- Case score: `100 × Σ(weight × item) / Σ weight`. It is 0 when a critical assertion fails.
- Dimension score: the weighted mean of that dimension's items across all cases.
- Overall score: the mean of dimension scores weighted by the paper's dimension weights.
  Grades: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, otherwise F.
- Pass: overall ≥ `passing_score` **and** zero critical failures.

## Agents under test

An agent config selects an adapter:

- `openai`: any `/chat/completions` endpoint, such as vLLM, TGI, a gateway or a hosted API. Tool calls are answered from `tool_mocks`.
- `webhook`: any JSON API, mapped with a request template and response field paths.
- `demo`: canned behaviors (`demo-good`, `demo-weak`) for trying the tool without a model.

See [`examples/agents/`](../examples/agents/). Secrets are only ever read from environment variables.
