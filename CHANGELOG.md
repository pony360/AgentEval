# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-12

### Added
- **A2A (Agent2Agent) evaluation adapter** (`adapter: a2a`): discovers the Agent Card, prefers a v1.0 JSON-RPC
  interface and falls back to v0.3; one A2A conversation per exam case (`contextId`), `taskId` continuation for
  `input-required` / `auth-required` tasks, `GetTask` polling for long-running tasks, tool calls read from
  `metadata.tool_calls` or DataParts. Verified against the official `a2a-sdk` 1.2 server (v1.0 and v0.3 compat).
- **A2A Agent Card scanning**: `agenteval a2a card` and `agenteval scan --a2a-card` (new `a2a_agent` asset type).
- **MCP client-config scanning**: `agenteval scan --mcp-config` for `mcpServers` / `servers` JSON (new `mcp_server` asset type).
- **`agent-protocols` rule pack** (new `baseline` tier): 6 A2A and 6 MCP rules, with Chinese translations.
- **`agenteval mcp serve`**: dependency-free MCP stdio server with 8 tools (scan, inspect_a2a_card, run_eval,
  check_certification, verify_certificate, …), confined to `--root`, network-gated by `--allow-network`.
  Verified with the official MCP Python SDK 2.3 client.
- `agent.schema.json` for agent-under-test configs; editor modelines on the example agents.

### Changed
- `scan` no longer requires `--snapshot` when `--a2a-card` or `--mcp-config` is given.
- Adapter error messages default to English.

## [0.1.0] - 2026-10-11

### Added
- `agenteval scan` / `snapshot`: Kubernetes and offline-snapshot collection; JSON, SARIF and HTML reports in English and Chinese.
- Policy-as-code rule engine with JSON Schema validation and a fail-closed condition DSL; automated and attestation rules.
- Starter rule packs: `us-federal` (national), `us-ca` (regional), `pci-dss` (industry) — all mappings `verified: false`.
- `agenteval eval`: exam papers with multi-turn scripted cases, tool mocks, assertions, critical vetoes and rubric judging;
  OpenAI-compatible, webhook and demo adapters; heuristic, Anthropic and OpenAI-compatible judges.
- `cs-ecommerce-v1` customer-service exam paper (10 cases).
- `agenteval cert`: strict certification gates, Ed25519-signed certificates, HTML/PDF rendering,
  optional verification-URL QR code and status-URL revocation check.
- JSON Schemas for rules, packs, translations, attestations, exams, systems and policy; `agenteval schemas`.
- `agenteval init`: copy the bundled starter content into a working directory.
- Published on PyPI as `agenteval-toolkit` (command and import name `agenteval`), released via Trusted Publishing.
