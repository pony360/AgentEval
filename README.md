# AgentEval

**Scan, evaluate, certify.** AgentEval is an Apache-2.0 command-line toolkit with three jobs:

- it **scans** self-hosted AI infrastructure against policy-as-code rule packs,
- it **evaluates** AI agents with standard exam papers, and
- it **issues signed certificates** when a system passes both.

[English](README.md) · [简体中文](README.zh-CN.md)

> **Status: alpha (0.1.0).** The rule packs are starter mappings, and every one is marked `verified: false`.
> They are a starting point for your own legal and compliance review. They are not legal advice.

---

## Contents

- [Why](#why)
- [Features](#features)
- [Install](#install)
- [Quickstart](#quickstart)
- [Commands and exit codes](#commands-and-exit-codes)
- [Rule packs](#rule-packs)
- [Writing rules](#writing-rules)
- [Evaluating agents](#evaluating-agents)
- [Certification](#certification)
- [Configuration](#configuration)
- [Security model](#security-model)
- [Project layout](#project-layout)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

## Why

Teams that run their own models on Kubernetes have to answer two separate questions:

- *Is the deployment configured the way our policies require?* (auth, TLS, provenance, PII handling, …)
- *Does the agent built on it do its job safely?* (task completion, accuracy, prompt-injection resistance, …)

AgentEval answers both with versioned, reviewable files, rules and exams in YAML, and turns the
combined result into a certificate that anyone can verify offline.

## Features

| | |
|---|---|
| **Compliance scanning** | Live Kubernetes cluster or offline snapshot. Normalizes vLLM, TGI, Triton, Ollama, llama.cpp, SGLang, Ray Serve and KServe workloads into facts |
| **Policy-as-code** | YAML rules validated by JSON Schema. A safe, fail-closed condition DSL. Automated and attestation rules |
| **Tiered rule packs** | National (US federal guidance), regional (California) and industry (PCI DSS) starter packs |
| **Reports** | JSON, SARIF (GitHub code scanning) and HTML, in English or Simplified Chinese |
| **Agent evaluation** | Scripted multi-turn exams with tool mocks, deterministic assertions plus rubric judging, and critical-failure vetoes |
| **Pluggable adapters and judges** | OpenAI-compatible endpoints, generic webhooks and demo agents. Heuristic (offline), Anthropic or OpenAI-compatible judges |
| **Certification** | Strict gates, Ed25519-signed JSON, HTML/PDF certificates, optional QR verification URL and revocation status check |
| **CI-friendly** | Stable exit codes. No database or server required |

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/pony360/AgentEval.git
cd agenteval
pip install -e ".[kubernetes]"      # drop [kubernetes] if you only scan snapshots
agenteval version
```

Optional extras: `pdf` (certificate PDFs through Playwright/Chromium) and `dev` (pytest and build).

The CLI looks for `rules/`, `exams/` and `certification/` in the current directory. Pass
`--rules` or `--exams`, or set the [environment variables](#configuration), to use other locations.

## Quickstart

All of these commands run offline against the bundled sample cluster and demo agents.

```bash
# 1. Validate the rule packs
agenteval rules validate
#   ✓ 23 rules validated

# 2. Scan a cluster snapshot (exits 1: there are high/critical findings)
agenteval scan --snapshot examples/snapshots/sample-cluster.json -f html -o scan.html

# 3. Evaluate an agent. On a pass, also write pre-deployment-testing evidence for its asset
agenteval eval run -p cs-ecommerce-v1 -a examples/agents/demo-good.yaml -f json -o eval.json \
  --attest-asset deployment/ml-prod/llama-chat --attest-output att-eval.yaml
#   → 96.7 (grade A) · cases passed 10/10 · critical failures 0

# 4. Re-scan with the human attestations and the evaluation evidence
agenteval scan --snapshot examples/snapshots/sample-cluster.json \
  --attestations examples/attestations.yaml \
  --attestations examples/attestations-certification.yaml \
  --attestations att-eval.yaml -o scan.json --fail-on none

# 5. Certify the registered system, then verify the certificate
agenteval cert keygen --public-out issuer.pem
agenteval cert issue -s northwind-cs --scan scan.json --eval eval.json -o certificates/
agenteval cert verify certificates/AEV-*.json --public-key issuer.pem
#   Signature valid · State: active · DEMO certificate (heuristic judge)
```

To scan a live cluster, run `agenteval scan --source kubernetes [--context ctx] [-n namespace]`.
It needs read-only access to Deployments, StatefulSets, Services, Ingresses and NetworkPolicies.
For air-gapped clusters, use `agenteval snapshot` to export a snapshot and scan it elsewhere.

Every command accepts `--lang zh` for Chinese output.

## Commands and exit codes

| Command | Purpose |
|---|---|
| `agenteval scan` | Collect assets and evaluate rules. Writes JSON, SARIF or HTML |
| `agenteval snapshot` | Export a read-only cluster snapshot |
| `agenteval rules validate \| list` | Validate rule packs and list rules |
| `agenteval schemas -o DIR` | Export the JSON Schemas for editors |
| `agenteval eval papers \| validate \| run` | List or validate exam papers. Run an exam against an agent |
| `agenteval cert check \| issue \| verify \| keygen` | Certification |

| Exit code | Meaning |
|---|---|
| `0` | Success, or the gate passed |
| `1` | The gate did not pass: findings at or above `--fail-on`, the exam was not passed, the system is not eligible, or the certificate is invalid |
| `2` | Usage or input error, such as an invalid rule file or a missing file |

## Rule packs

| Pack | Tier | Rules | Covers |
|---|---|---|---|
| `us-federal` | national | 16 | NIST AI RMF 1.0, AI 600-1, SP 800-53, SP 800-190, SP 800-218A and OMB M-25-21: authentication, TLS, network policy, image pinning, secrets, provenance, governance attestations |
| `us-ca` | regional | 3 | Bot disclosure (B&P §17940), CCPA data minimization, AB 2013 training-data transparency |
| `pci-dss` | industry | 4 | PCI DSS v4.0.1 (Req. 3, 4, 8, 10): no full card numbers in logs, strong cryptography in transit, authentication, and audit logging for AI that handles payment conversations |

Rule text is in English, and Simplified Chinese translations are in `rules/*/i18n/zh.yaml`.

## Writing rules

```yaml
# yaml-language-server: $schema=../../agenteval/schemas/rule.schema.json
rules:
  - id: FED-SEC-002
    title: Externally exposed inference endpoints must use TLS
    description: Services exposed through LoadBalancer, NodePort or Ingress must encrypt traffic.
    severity: high
    type: automated
    category: data-protection
    applies_to:
      asset_types: [inference_service]
      when: {field: exposure.public, op: eq, value: true}
    check: {field: tls.enabled, op: eq, value: true}
    remediation: Add a tls section to the Ingress with a trusted certificate (e.g. cert-manager).
    sources:
      - framework: NIST SP 800-53 Rev.5
        reference: SC-8 Transmission Confidentiality and Integrity
        verified: false
```

The full reference is in **[docs/rule-schema.md](docs/rule-schema.md)**. It covers every field,
the condition operators, the fact catalog, attestations and translations. The schemas are in
[`agenteval/schemas/`](agenteval/schemas/).

## Evaluating agents

An exam paper defines weighted dimensions, multi-turn cases, assertions (some of them
**critical**) and rubric items. The bundled `cs-ecommerce-v1` paper tests an e-commerce
support agent in 10 cases, from order lookup and returns to PII handling and prompt injection.

```bash
agenteval eval run -p cs-ecommerce-v1 -a my-agent.yaml --judge anthropic --judge-model <model> \
  --judge-key-env ANTHROPIC_API_KEY -o report.html
```

See **[docs/exams.md](docs/exams.md)** for the paper format, scoring, adapters and judges.

## Certification

`cert issue` signs a certificate only if all of the following hold:

- the security score is **> 90** and the quality score is **> 90**,
- there are no open high or critical findings,
- no attestations are pending,
- there are no critical evaluation failures and no session errors, and
- the evidence is at most 30 days old.

Evaluations scored by the offline heuristic judge get **DEMO** certificates only. Certificates are
valid for 6 months. See **[docs/certification.md](docs/certification.md)** for the gates, keys,
the verification URL and status hooks, and how to revoke certificates.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `AGENTEVAL_LANG` | `en` | Output language (`en`, `zh`) |
| `AGENTEVAL_RULES_DIR` | `./rules` | Rules directory |
| `AGENTEVAL_EXAMS_DIR` | `./exams` | Exam library |
| `AGENTEVAL_CERT_DIR` | `./certification` | Policy and system registry |
| `AGENTEVAL_SIGNING_KEY` | `./.agenteval/signing-key.pem` | Ed25519 private key |
| `AGENTEVAL_VERIFY_URL` | — | Verification URL template with `{id}` |
| `AGENTEVAL_STATUS_URL` | — | Revocation status URL template with `{id}` |
| `AGENTEVAL_JUDGE_MODEL`, `AGENTEVAL_JUDGE_BASE_URL` | — | LLM judge settings |
| `AGENTEVAL_ANNOTATION_PREFIX` | `agenteval.io/` | Kubernetes label and annotation prefix for hints |

## Security model

- **Read-only.** The scanner only lists and reads Kubernetes objects. It never changes them.
- **No secret values.** Plaintext-secret detection records environment variable *names* only.
- **No arbitrary code.** Rule conditions are a fixed DSL, and YAML is loaded with `safe_load`.
- **Fail-closed.** Missing facts never make a check pass.
- **Secrets come from the environment.** Agent and judge API keys are read from the variables you name.
- **Private key.** The signing key is created with mode 0600 and is git-ignored.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Project layout

```
agenteval/            Python package (CLI, engine, collectors, evaluation, certification, schemas)
rules/                Rule packs (national / regional / industry)
exams/                Exam papers
certification/        Certification policy and registered systems
examples/             Sample snapshot, attestations and agent configs
docs/                 Reference documentation
tests/                Test suite (pytest)
```

## Roadmap

- More collectors (cloud-managed endpoints, model registries, API gateways)
- More rule packs (EU AI Act, ISO/IEC 42001, HIPAA), contributed with reviewer sign-off
- More exam papers (IT helpdesk, sales assistant, RAG question answering)
- Bundled starter content in the wheel, and a `pip install agenteval` release on PyPI
- A hosted verification and status service reference implementation

## Contributing

Contributions are welcome, especially rules, exam papers and translations. Read
[CONTRIBUTING.md](CONTRIBUTING.md) first. Commits must be signed off under the
[DCO](https://developercertificate.org/) (`git commit -s`). This project follows the
[Code of Conduct](CODE_OF_CONDUCT.md).

```bash
pip install -e ".[dev]"
pytest
```

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE).

Framework and regulation names, including NIST, PCI DSS and California statutes, are used
only to identify the source material a rule refers to. Their use does not imply endorsement by,
or affiliation with, their owners.
