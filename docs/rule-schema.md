# Rule files and the YAML schema

AgentEval rules are policy-as-code. Each rule is a YAML document. A JSON Schema
(Draft 2020-12) validates it at load time, and the loader then turns it into a typed model.
Invalid files are rejected before anything is scanned.

| File | Schema | Purpose |
|---|---|---|
| `rules/<pack>/pack.yaml` | [`pack.schema.json`](../agenteval/schemas/pack.schema.json) | Pack metadata: ID, tier and jurisdiction |
| `rules/<pack>/*.yaml` | [`rule.schema.json`](../agenteval/schemas/rule.schema.json) | One rule, a list of rules, or `{rules: [...]}` |
| `rules/<pack>/i18n/<lang>.yaml` | [`translations.schema.json`](../agenteval/schemas/translations.schema.json) | Translated display text per rule ID |
| `attestations*.yaml` | [`attestations.schema.json`](../agenteval/schemas/attestations.schema.json) | Human evidence for attestation rules |

The files listed in [certification.md](certification.md) and [exams.md](exams.md) are covered by schemas too.

## Editor support

Every shipped YAML file starts with a modeline. The YAML language server (VS Code
"YAML" extension, JetBrains, Neovim) reads it to give completion, hover docs and inline errors:

```yaml
# yaml-language-server: $schema=../../agenteval/schemas/rule.schema.json
```

To use the schemas outside this repository, export them:

```bash
agenteval schemas --out .schemas/
```

## Layout

```
rules/
  us-federal/            # national tier
    pack.yaml
    security.yaml        # automated checks
    governance.yaml      # attestation checks
    i18n/zh.yaml         # Simplified Chinese display text
  us-ca/                 # regional tier
  industry-pci-dss/      # industry tier
```

The loader walks the directory recursively. It skips `pack.yaml` and anything under `i18n/`,
and every other `*.yaml` file is read as rules. A rule belongs to the nearest `pack.yaml`
above it. Rule IDs must be unique across all packs.

## Pack metadata (`pack.yaml`)

```yaml
id: us-ca                 # referenced by certification/systems/*.yaml → rule_packs
name: California
name_zh: 加利福尼亚州        # optional
tier: regional            # national | regional | industry
jurisdiction: US-CA
description: SB 1001 bot disclosure, CCPA/CPRA data minimization, AB 2013 training-data transparency.
description_zh: ...       # optional
```

Certification uses the tier to show national, regional and industry results separately.

## Rule fields

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | `^[A-Z]{2,6}(-[A-Z0-9]{2,8})+$`, e.g. `FED-SEC-001`, `PCI-REQ8-001` |
| `title` | yes | string | The requirement, phrased as what must be true |
| `description` | yes | string | Why the rule exists and what it checks |
| `severity` | yes | enum | `critical` · `high` · `medium` · `low` · `info` |
| `type` | yes | enum | `automated` (needs `check`) or `attestation` (needs `attestation`) |
| `category` | yes | string | Free-form, e.g. `access-control`, `governance` |
| `applies_to.asset_types` | yes | string[] | `inference_service`, `cluster` |
| `applies_to.when` | no | condition | If false, the rule is `not_applicable` for that asset |
| `check` | automated | condition | Must be true for the asset to **pass** |
| `attestation.evidence_required` | attestation | string[] | Evidence keys that must all be present and non-empty |
| `attestation.max_age_days` | no | integer ≥ 1 | Older attestations fail |
| `remediation` | yes | string | A concrete fix, shown in every report |
| `sources[]` | yes (≥1) | object | `framework`, `reference`, optional `url`, `verified` |
| `jurisdiction` | no | string | Overrides the pack's jurisdiction |
| `effective_date` | no | date string | Quote it: `"2026-01-01"` |
| `version` | no | string | Bump it when the logic changes. The scan's `rules_version` fingerprint includes it |
| `enabled` | no | boolean | `false` keeps the rule in the repo without running it |

Unknown keys are errors (`additionalProperties: false`), so a typo such as `serverity` is rejected.

### `sources[].verified`

A source is `verified: false` unless you set it to `true`. `agenteval rules validate`
reports how many mappings are still unverified. Set `verified: true` only after your legal or
compliance team has reviewed that the rule really implements the cited text.

## Conditions

Conditions are a small, side-effect-free DSL. Nothing is passed to `eval`.

```yaml
check:
  all:                                  # every child must be true
    - {field: auth.enabled, op: eq, value: true}
    - any:                              # at least one child must be true
        - {field: tls.enabled, op: truthy}
        - not: {field: exposure.public, op: truthy}
```

| Operator | True when |
|---|---|
| `eq` / `ne` | value equals / does not equal `value` |
| `in` / `not_in` | value is / is not in the list `value` |
| `contains` / `not_contains` | list, string or dict contains / does not contain `value` |
| `regex` | the string value matches `value` (Python `re.search`) |
| `gt` `gte` `lt` `lte` | numeric or ordered comparison; a type mismatch makes it false |
| `truthy` / `falsy` | the value is truthy / falsy (a missing value counts as falsy) |
| `exists` / `not_exists` | the field is present and not null / is missing or null |
| `empty` / `not_empty` | a missing, null or zero-length value / a value with length > 0 |

**Fail-closed.** If a field is missing from the collected facts, every operator returns
false, except `not_exists`, `falsy` and `empty`. Missing evidence therefore never makes
a check pass. Write checks as "the good state is true" (for example `auth.enabled eq true`),
not "the bad state is false".

All children of `all`/`any` are evaluated even after the result is known. The finding's
`evidence` then lists every field that was consulted and its actual value.

## Fact catalog

Collectors normalize Kubernetes objects into **assets**, and each asset has a dictionary of
**facts**. Fields in conditions are dotted paths into those facts.

### `inference_service` (Deployments and StatefulSets that run a model server)

Detection: a known server image (vLLM, TGI, Triton, Ollama, llama.cpp, SGLang, Ray Serve,
KServe), or the label `agenteval.io/inference: "true"`.

| Fact | Type | Derived from |
|---|---|---|
| `kind`, `server`, `replicas` | str, str, int | the workload spec |
| `owner` | str | label `agenteval.io/owner`, `owner` or `team` |
| `risk_tier` | str | annotation `agenteval.io/risk-tier` (default `standard`) |
| `customer_facing` | bool | `agenteval.io/customer-facing` |
| `ai_disclosure.enabled` | bool | `agenteval.io/ai-disclosure` |
| `model.name` | str | `agenteval.io/model` or the server's `--model` argument |
| `model.source`, `model.card_url` | str | `agenteval.io/model-source`, `agenteval.io/model-card` |
| `model.provenance_verified` | bool | `agenteval.io/provenance-verified` |
| `model.generative`, `model.fine_tuned` | bool | server type, `agenteval.io/generative`, `agenteval.io/fine-tuned` |
| `auth.enabled`, `auth.method` | bool, str | `--api-key` flags, auth sidecars, `agenteval.io/auth` |
| `exposure.public` | bool | LoadBalancer/NodePort Services or Ingresses that select the pods |
| `exposure.service_types`, `exposure.ingress_hosts` | str[] | matching Services and Ingresses |
| `tls.enabled` | bool | Ingress `tls`, or `agenteval.io/tls` |
| `network_policy.present` | bool | a NetworkPolicy that selects the pods |
| `image.refs`, `image.all_digest_pinned`, `image.any_latest` | str[], bool, bool | container images |
| `security_context.run_as_non_root`, `.privileged`, `.allow_privilege_escalation` | bool | pod and container security contexts |
| `secrets.plaintext_env` | str[] | **names** of env vars that look like secrets and hold literal values. Values are never recorded |
| `resources.limits_set` | bool | every container sets resource limits (memory at minimum) |
| `logging.request_logging`, `logging.pii_redaction` | bool | `agenteval.io/request-logging`, `agenteval.io/log-redaction` |
| `monitoring.enabled` | bool | `prometheus.io/scrape` or `agenteval.io/monitoring` |
| `content_safety.enabled`, `content_safety.method` | bool, str | `agenteval.io/content-safety` or a guard sidecar image |

### `cluster` (one per scan)

| Fact | Type |
|---|---|
| `inference_service_count` | int |
| `namespaces` | str[] (namespaces that contain inference services) |

The `agenteval.io/` prefix can be changed with `AGENTEVAL_ANNOTATION_PREFIX`. Point it at a
domain your organization controls, for example `ai.example.com/`.

## Attestations

Some obligations cannot be observed from configuration, such as a governance policy or an
impact assessment. Attestation rules ask for evidence:

```yaml
- id: FED-GOV-A02
  type: attestation
  applies_to: {asset_types: [inference_service], when: {field: risk_tier, op: eq, value: high-impact}}
  attestation:
    evidence_required: [impact_assessment, pre_deployment_testing]
    max_age_days: 365
```

The evidence is supplied in one or more attestation files:

```yaml
# yaml-language-server: $schema=../agenteval/schemas/attestations.schema.json
attestations:
  - rule_id: FED-GOV-A02
    asset_id: deployment/ml-prod/llama-chat   # or "*" for every asset
    attested_by: genai-platform@example.com
    attested_at: "2026-08-20"
    evidence:
      impact_assessment: JIRA-AIGOV-112        # links or ticket IDs, not pasted content
```

Merging rules:

- Entries for the same rule and asset are merged across files. Asset-specific entries
  override `"*"` entries.
- The **oldest** `attested_at` among the merged entries is used for the age check.
- If any required key is still missing, the finding is `fail`. If no entry exists at all, it is `manual` (pending).
- `agenteval eval run --attest-asset ...` writes `pre_deployment_testing` evidence for you
  when an evaluation passes.

## Translations

The source language of rules is English. To add display text in another language, add
`i18n/<lang>.yaml` next to the pack:

```yaml
FED-SEC-001:
  title: 推理端点必须启用身份认证
  description: ...
  remediation: ...
  sources:              # one entry per source, in the same order; replaces `reference`
    - MEASURE 2.7（AI 系统安全性与韧性评估）
    - AC-3 访问控制执行
```

Only `title`, `description`, `remediation` and `sources` may be translated. Logic is never translated.
Fields that are missing fall back to English. Currently supported output languages: `en` and `zh`.

## Workflow for a new rule

1. Add the rule to the right pack and run `agenteval rules validate`.
2. Add a fixture to `examples/snapshots/` or a unit test in `tests/` that shows the rule both passing and failing.
3. Add translations if you can. `agenteval rules list --lang zh` shows what will be displayed.
4. Leave `verified: false` in the PR. Reviewers with the right expertise flip it.
