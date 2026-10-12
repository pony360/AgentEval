# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors

import json
from datetime import date

from agenteval.collectors import collect_from_snapshot_file, normalize_snapshot
from agenteval.config import load_attestations
from agenteval.engine import load_rules, run_scan
from agenteval.reports import render


def _assets(sample_snapshot):
    assets, _ = collect_from_snapshot_file(sample_snapshot)
    return {a.id: a for a in assets}


def test_detects_only_inference_workloads(sample_snapshot):
    ids = set(_assets(sample_snapshot))
    assert ids == {
        "cluster/gpu-prod-sandbox",
        "deployment/ml-prod/llama-chat",
        "deployment/ml-dev/mistral-test",
        "statefulset/ml-prod/embeddings",
    }


def test_compliant_workload_facts(sample_snapshot):
    f = _assets(sample_snapshot)["deployment/ml-prod/llama-chat"].facts
    assert f["server"] == "vllm"
    assert f["auth"]["enabled"] is True
    assert f["exposure"]["public"] is True and f["tls"]["enabled"] is True
    assert f["network_policy"]["present"] is True
    assert f["image"]["all_digest_pinned"] is True
    assert f["security_context"] == {"run_as_non_root": True, "privileged": False, "allow_privilege_escalation": False}
    assert f["secrets"]["plaintext_env"] == []
    assert f["model"]["generative"] is True


def test_noncompliant_workload_facts(sample_snapshot):
    f = _assets(sample_snapshot)["deployment/ml-dev/mistral-test"].facts
    assert f["server"] == "tgi"
    assert f["model"]["name"] == "mistralai/Mistral-7B-Instruct-v0.3"
    assert f["auth"]["enabled"] is False
    assert f["exposure"]["service_types"] == ["LoadBalancer"]
    assert f["tls"]["enabled"] is False
    assert f["image"]["any_latest"] is True
    assert f["security_context"]["privileged"] is True
    assert f["secrets"]["plaintext_env"] == ["tgi:HF_TOKEN"]


def test_secret_values_never_leak(sample_snapshot, rules_dir, sample_attestations):
    assets, _ = collect_from_snapshot_file(sample_snapshot)
    result = run_scan(load_rules(rules_dir), assets, "test", load_attestations(sample_attestations))
    for fmt in ("json", "sarif", "html"):
        assert "hf_REDACTED_EXAMPLE" not in render(result, fmt)


def test_auth_sidecar_detected(sample_snapshot):
    f = _assets(sample_snapshot)["statefulset/ml-prod/embeddings"].facts
    assert f["auth"] == {"enabled": True, "method": "proxy-sidecar"}
    assert f["owner"] == "search-infra"
    assert f["model"]["generative"] is False


def test_end_to_end_statuses(sample_snapshot, rules_dir, sample_attestations):
    assets, _ = collect_from_snapshot_file(sample_snapshot)
    result = run_scan(load_rules(rules_dir), assets, "test",
                      load_attestations(sample_attestations), today=date(2026, 10, 1))
    by = {(f.rule_id, f.asset_id): f.status.value for f in result.findings}
    assert by[("FED-SEC-006", "deployment/ml-dev/mistral-test")] == "fail"
    assert by[("FED-SEC-001", "deployment/ml-prod/llama-chat")] == "pass"
    assert by[("FED-SEC-002", "statefulset/ml-prod/embeddings")] == "not_applicable"
    assert by[("FED-GOV-A01", "cluster/gpu-prod-sandbox")] == "pass"
    assert by[("FED-GOV-A02", "deployment/ml-prod/llama-chat")] == "fail"   # incomplete attestation
    assert by[("FED-GOV-A03", "deployment/ml-prod/llama-chat")] == "manual"
    assert by[("FED-IR-001", "cluster/gpu-prod-sandbox")] == "manual"
    assert result.summary.by_status["fail"] > 0
    assert 0 < result.summary.score < 100


def test_expired_attestation_fails(rules_dir):
    rules = [r for r in load_rules(rules_dir) if r.id == "FED-GOV-A01"]
    assets = normalize_snapshot({"cluster": {"name": "c"}})
    atts = [{"rule_id": "FED-GOV-A01", "asset_id": "*", "attested_at": "2025-01-01",
             "evidence": {"ai_policy_document": "x", "accountable_owner": "y"}}]
    result = run_scan(rules, assets, "t", atts, today=date(2026, 10, 1))
    assert result.findings[0].status.value == "fail"
    assert "expired" in result.findings[0].message


def test_sarif_structure(sample_snapshot, rules_dir):
    assets, _ = collect_from_snapshot_file(sample_snapshot)
    doc = json.loads(render(run_scan(load_rules(rules_dir), assets, "t"), "sarif"))
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
    assert all(res["ruleId"] in rule_ids for res in run["results"])
    assert any(res["level"] == "error" for res in run["results"])
