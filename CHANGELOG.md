# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
