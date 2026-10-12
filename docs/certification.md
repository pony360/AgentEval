# Certification

A certificate states that one registered AI system passed two things at the same point in time:

1. **Security.** A compliance scan of the system's asset against every rule pack it is
   registered for (national, regional and industry).
2. **Quality.** An evaluation of the system's agent on its exam paper.

Certificates are Ed25519-signed JSON envelopes. Each one comes with English and Chinese HTML
pages, and optionally PDFs.

## Register a system

`certification/systems/<id>.yaml` ([`system.schema.json`](../agenteval/schemas/system.schema.json)):

```yaml
id: northwind-cs
name: Northwind Outfitters AI Customer Service
organization: Northwind Outfitters (fictional demo company)
asset_id: deployment/ml-prod/llama-chat     # the scanned asset
agent_name: demo-support-bot-good           # the agent name recorded in eval runs
exam: cs-ecommerce-v1
rule_packs: [us-federal, us-ca, pci-dss]
```

The scan scope is the system's asset plus cluster-level findings, limited to the registered packs.

## Policy

`certification/policy.yaml` ([`policy.schema.json`](../agenteval/schemas/policy.schema.json))
sets the thresholds. The defaults are strict:

| Gate | Kind | Default |
|---|---|---|
| `sec_asset_scanned`: the scan includes the system asset | blocking | — |
| `sec_packs_assessed`: every registered pack produced results | blocking | — |
| `sec_score`: security score **>** `min_security_score` | blocking | 90 |
| `sec_no_blocking`: no failed findings at `block_on_severities` | blocking | critical, high |
| `sec_no_pending`: no attestation still pending | blocking | on |
| `sec_fresh`: scan age ≤ `max_evidence_age_days` | freshness | 30 days |
| `q_run`: an evaluation exists for agent × exam | blocking | — |
| `q_score`: overall score **>** `min_quality_score` | blocking | 90 |
| `q_no_critical`: zero critical assertion failures | blocking | on |
| `q_no_errors`: no session errors | blocking | on |
| `q_fresh`: evaluation age ≤ `max_evidence_age_days` | freshness | 30 days |
| `q_llm_judge`: rubric scored by an LLM judge | demo | on |

- When every blocking and freshness gate passes, the system is **eligible**.
- If only the `demo` gate fails (for example, the heuristic judge was used), the certificate is marked
  **DEMO** and watermarked. Set `allow_demo_certificates: false` to refuse those.
- Certificates are valid for `validity_months` (default 6).

## Commands

```bash
agenteval cert check  -s northwind-cs --scan scan.json --eval eval.json   # exit 0 eligible / 1 not
agenteval cert issue  -s northwind-cs --scan scan.json --eval eval.json -o certificates/
agenteval cert keygen --public-out issuer.pem                             # publish this file
agenteval cert verify certificates/AEV-....json --public-key issuer.pem   # exit 0 valid & active
```

## Keys

The signing key is read from `AGENTEVAL_SIGNING_KEY`, or `./.agenteval/signing-key.pem` by default.
It is created on first use with mode 0600. Keep it out of version control (`.gitignore` already excludes it).
In CI, inject the key from a secret store. Publish the public key so that anyone can verify
certificates offline.

## Online verification and revocation

The CLI is stateless. It does not keep a registry of the certificates it issues. Two optional hooks
connect it to a service you run:

- `--verify-url https://certs.example.com/{id}` (or `AGENTEVAL_VERIFY_URL`) embeds a verification URL
  in the signed certificate and prints it as a QR code. Without it, the certificate page shows offline
  verification instructions instead.
- `agenteval cert verify --status-url https://certs.example.com/api/{id}` (or `AGENTEVAL_STATUS_URL`)
  fetches `{"state": "active" | "revoked" | "superseded"}` and fails verification unless the state is `active`.

To revoke automatically, run `cert check` on a schedule (for example after each nightly scan and
evaluation). When it exits 1 because a blocking gate failed, mark the certificate revoked in your
status service. Failing a freshness gate alone means that evidence must be renewed. It does not
trigger revocation.

## What a certificate is not

A certificate records the technical control checks and test results for one point in time. It is
not a legal opinion, a regulatory approval, or a statement by any standards body. Rule mappings
marked `verified: false` have not been reviewed by your legal or compliance team.
