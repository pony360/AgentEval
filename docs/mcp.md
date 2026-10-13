# MCP (Model Context Protocol) support

AgentEval works with [MCP](https://modelcontextprotocol.io/) in two directions:

1. **AgentEval as an MCP server.** `agenteval mcp serve` exposes scanning, evaluation and
   certificate checks as MCP tools, so Claude, IDEs and other MCP clients can call them.
2. **Scanning MCP client configurations.** `agenteval scan --mcp-config` checks the MCP servers your
   tools are configured to launch or connect to, using the `agent-protocols` rule pack.

## AgentEval as an MCP server

```bash
agenteval mcp serve                         # stdio; file access limited to the current directory
agenteval mcp serve --root ~/governance     # restrict tools to another directory
agenteval mcp serve --allow-network         # also allow remote Agent Cards and non-demo agents
```

Client configuration (Claude Desktop, Claude Code `.mcp.json`, Cursor and similar):

```json
{
  "mcpServers": {
    "agenteval": {
      "command": "agenteval",
      "args": ["mcp", "serve", "--root", "/path/to/your/governance-repo"]
    }
  }
}
```

The server speaks the MCP stdio transport (newline-delimited JSON-RPC 2.0) and needs no extra
dependency. It negotiates protocol versions `2025-11-25`, `2025-06-18`, `2025-03-26` and
`2024-11-05`, and has been tested with the official MCP Python SDK client (2.3).

### Tools

| Tool | Read-only | What it does |
|---|---|---|
| `list_rules` | yes | Rules with pack, severity and (localized) title. Can be filtered by pack |
| `validate_rules` | yes | Validates the rule packs against the JSON Schemas |
| `scan` | yes | Scans a snapshot, A2A Agent Cards and/or MCP configs. Returns the score and failed or pending findings |
| `inspect_a2a_card` | yes | Agent Card facts plus the A2A rule results |
| `list_exam_papers` | yes | The exam library |
| `run_eval` | no (open world) | Runs an exam against an agent config. Returns the scores and failed cases |
| `check_certification` | yes | Certification gates for a registered system, from scan and evaluation JSON. Issues nothing |
| `verify_certificate` | yes | Checks the Ed25519 signature and expiry with the issuer public key |

Every result is returned both as `structuredContent` and as a JSON text block. Library errors come back
as tool results with `isError: true`, so the model can see and fix them.

### Safety model

- **File access is confined to `--root`.** A path that resolves outside it (including through `..` or
  an absolute path) is rejected.
- **No network by default.** Fetching a remote Agent Card, evaluating any adapter other than `demo`,
  or using an LLM judge requires `--allow-network`.
- **Nothing is written to disk.** There is no tool that issues certificates or creates signing keys.
  Run `agenteval cert issue` yourself for that.
- **stdout carries only protocol messages.** Logs and tracebacks go to stderr.

## Scanning MCP client configurations

```bash
agenteval scan --mcp-config ~/.cursor/mcp.json --mcp-config .mcp.json -f html -o mcp-report.html
```

Two formats are read: `{"mcpServers": {...}}` (Claude Desktop, Claude Code, Cursor) and
`{"servers": {...}}` (VS Code). Each enabled server becomes an `mcp_server` asset with the ID
`mcp/<config-file-stem>/<server-name>`.

| Rule | Severity | Checks |
|---|---|---|
| PROTO-MCP-001 | high | Remote (HTTP/SSE) servers use TLS. localhost is exempt |
| PROTO-MCP-002 | medium | Non-local remote servers declare auth: an Authorization-style header, or an `oauth` block |
| PROTO-MCP-003 | critical | No literal secrets in `env` or `headers`. `${VAR}`, `${input:...}` and `Bearer ${VAR}` are fine |
| PROTO-MCP-004 | high | npx / uvx / pipx packages are version-pinned, and docker images use a tag other than `latest` or a digest |
| PROTO-MCP-005 | critical | The launch command does not download and execute a script (`curl … \| sh`) |
| PROTO-MCP-006 | high | Docker-launched servers are not `--privileged`, do not use `--network host` and do not mount `/` |

Only the names of offending variables and headers are recorded, never their values. The fact catalog
is in [rule-schema.md](rule-schema.md#mcp_server-mcp-client-configurations).
