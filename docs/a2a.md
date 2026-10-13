# A2A (Agent2Agent) support

AgentEval works with [A2A](https://a2a-protocol.org/) agents in two ways:

1. **Evaluate** an A2A agent with an exam paper (`adapter: a2a`).
2. **Scan** its Agent Card against the `agent-protocols` rule pack (`agenteval a2a card`, `agenteval scan --a2a-card`).

Both protocol generations are supported: **v1.0** (`SendMessage`, `ROLE_USER`, parts without `kind`,
`TASK_STATE_*`, `A2A-Version: 1.0` header, `supportedInterfaces[]`) and **v0.3** (`message/send`,
`kind`-tagged parts, top-level `url` / `preferredTransport`). AgentEval talks to agents over the
JSON-RPC binding. The adapter has been tested against the official `a2a-sdk` (1.2.x) server in both
v1.0 and v0.3-compatibility modes, for agents that return either Messages or Tasks.

## Evaluating an A2A agent

```yaml
# examples/agents/a2a.example.yaml
name: northwind-support-a2a
adapter: a2a
a2a:
  agent_card_url: https://agents.northwind.example.com   # /.well-known/agent-card.json is appended
  protocol_version: auto                                 # auto | "0.3" | "1.0"
  headers:
    Authorization: "Bearer ${NORTHWIND_A2A_TOKEN}"
```

```bash
agenteval eval run -p cs-ecommerce-v1 -a examples/agents/a2a.example.yaml -o report.html
```

How an exam case maps onto A2A:

| Exam concept | A2A |
|---|---|
| A case | One conversation. The `contextId` returned by the agent is sent back on every later turn |
| A turn | One `SendMessage` (v1.0) or `message/send` (v0.3) call. v0.3 calls ask for `blocking: true` |
| A paused task | If the task state is `input-required` or `auth-required`, the next turn carries its `taskId` |
| A long task | `submitted` or `working` tasks are polled with `GetTask` / `tasks/get` until they finish, up to `timeout_s` |
| Reply text | The text parts of the task's artifacts. If there are none, the text parts of the status message. For a Message result, its text parts |
| Tool calls | A2A agents are opaque, so tool calls are only visible if the agent reports them, as a list under `metadata.tool_calls` or in a DataPart `{"tool_calls": [...]}`. The key is set by `tool_calls_key` |

Options:

| Key | Default | Meaning |
|---|---|---|
| `agent_card_url` | — | Card URL or agent base URL. AgentEval picks a JSON-RPC interface, preferring v1.0 |
| `url` | — | Call this JSON-RPC endpoint directly and skip discovery |
| `protocol_version` | `auto` | Pin the version to `"0.3"` or `"1.0"` |
| `headers` | `{}` | Extra request headers. `${ENV_VAR}` placeholders are expanded and secrets never live in the file |
| `timeout_s` | 60 | Per-turn limit, polling included |
| `poll_interval_s` | 1 | Delay between `GetTask` polls |
| `send_context` | `false` | Prepend the paper's business `context` to the first message. Leave it off for real agents, which have their own instructions |
| `tool_calls_key` | `tool_calls` | Where the agent reports tool calls |

Safety: a non-local endpoint over plain `http://` is refused. Agent Cards are only fetched over
HTTPS, except from `localhost`, and redirects are not followed.

`tool_called` assertions only pass if the agent reports its tool calls. With an opaque agent, use the
text assertions and rubric items, or ask the agent's owner to emit `metadata.tool_calls` in test environments.

## Checking an Agent Card

```bash
agenteval a2a card https://agents.northwind.example.com        # or a card URL / local JSON file
agenteval scan --a2a-card ./agent-card.json --mcp-config ./.mcp.json -f html -o report.html
```

`a2a card` prints the endpoints, protocol versions, auth schemes and signing status, then the rule results.
It exits 1 if a high or critical rule fails.

| Rule | Severity | Checks |
|---|---|---|
| PROTO-A2A-001 | high | Every advertised interface uses HTTPS (localhost is exempt) |
| PROTO-A2A-002 | high | Security schemes are declared and required |
| PROTO-A2A-003 | medium | No API keys in query strings, no credentials in URLs |
| PROTO-A2A-004 | medium | No OAuth implicit or password flows |
| PROTO-A2A-005 | low | The card carries `signatures`. AgentEval checks that they are present, not that they verify |
| PROTO-A2A-006 | low | `provider.organization` is set |

The full list of `a2a_agent` facts is in [rule-schema.md](rule-schema.md#a2a_agent-a2a-agent-cards).

To certify an A2A agent, register it in `certification/systems/` with
`asset_id: a2a/<host>/<agent-name-slug>` (as printed by `agenteval a2a card`). Add `agent-protocols`
to its `rule_packs`, and scan with `--a2a-card`.
