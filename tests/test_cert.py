# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors

import json
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from agenteval.certification import (
    CertPolicy, certificate_state, decide, issue, load_or_create_key, load_policy, load_systems, verify,
)
from agenteval.certification.issuer import add_months
from agenteval.certification.render import render_certificate
from agenteval.cli import app as cli_app
from agenteval.collectors import collect_from_snapshot_file
from agenteval.config import load_attestations
from agenteval.engine import load_packs, load_rules, run_scan
from agenteval.evaluation import Agent, load_agent_config, make_judge, resolve_paper, run_exam, to_attestation


@pytest.fixture(scope="module")
def env(root=None):
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    systems = load_systems(root / "certification")
    paper = resolve_paper("cs-ecommerce-v1", root / "exams")
    runs = {k: run_exam(paper, Agent(load_agent_config(root / "examples" / "agents" / f"demo-{k}.yaml")), make_judge("heuristic"))
            for k in ("good", "weak")}
    assets, _ = collect_from_snapshot_file(root / "examples" / "snapshots" / "sample-cluster.json")
    base = load_attestations([root / "examples" / "attestations.yaml", root / "examples" / "attestations-certification.yaml"])
    rules = load_rules(root / "rules")
    full = run_scan(rules, assets, "t", base + [to_attestation(runs["good"], systems["northwind-cs"].asset_id)])
    no_cert_atts = run_scan(rules, assets, "t", load_attestations(root / "examples" / "attestations.yaml"))
    return {"root": root, "systems": systems, "runs": runs, "scan": full, "scan_pending": no_cert_atts,
            "packs": load_packs(root / "rules"), "policy": load_policy(root / "certification")}


def test_rule_packs_have_three_tiers(env):
    assert {p.tier for p in env["packs"].values()} == {"national", "regional", "industry"}
    assert {r.pack for r in load_rules(env["root"] / "rules")} == set(env["packs"])


def test_good_system_eligible_but_demo(env):
    d = decide(env["systems"]["northwind-cs"], env["scan"], env["runs"]["good"], env["packs"], env["policy"])
    assert d.eligible and d.demo
    assert d.security.score == 100.0 and d.quality.score > 90
    assert {p.id: p.status for p in d.security.packs} == {"us-federal": "passed", "us-ca": "passed", "pci-dss": "passed"}


def test_llm_judge_makes_formal_certificate(env):
    run = env["runs"]["good"].model_copy(deep=True)
    run.judge = "anthropic:test-model"
    d = decide(env["systems"]["northwind-cs"], env["scan"], run, env["packs"], env["policy"])
    assert d.eligible and not d.demo


def test_demo_disallowed_blocks(env):
    pol = env["policy"].model_copy(update={"allow_demo_certificates": False})
    d = decide(env["systems"]["northwind-cs"], env["scan"], env["runs"]["good"], env["packs"], pol)
    assert not d.eligible


def test_weak_system_not_eligible(env):
    d = decide(env["systems"]["northwind-cs-staging"], env["scan"], env["runs"]["weak"], env["packs"], env["policy"])
    failed = {g.code for g in d.gates if not g.ok}
    assert not d.eligible and {"sec_score", "sec_no_blocking", "q_score", "q_no_critical"} <= failed
    # other workloads' issues don't count; this system isn't customer-facing → regional/industry packs don't apply
    assert {p.id: p.status for p in d.security.packs}["pci-dss"] == "not_applicable"


def test_score_must_be_strictly_greater(env):
    run = env["runs"]["good"]
    pol = env["policy"].model_copy(update={"min_quality_score": run.overall_score})
    d = decide(env["systems"]["northwind-cs"], env["scan"], run, env["packs"], pol)
    assert not d.eligible and not next(g for g in d.gates if g.code == "q_score").ok


def test_pending_attestations_block(env):
    d = decide(env["systems"]["northwind-cs"], env["scan_pending"], env["runs"]["good"], env["packs"], env["policy"])
    assert not d.eligible
    assert not next(g for g in d.gates if g.code == "sec_no_pending").ok
    assert not next(g for g in d.gates if g.code == "sec_no_blocking").ok  # FED-GOV-A02 lacks pre-deployment testing evidence


def test_wrong_subject_eval_rejected(env):
    d = decide(env["systems"]["northwind-cs"], env["scan"], env["runs"]["weak"], env["packs"], env["policy"])
    assert not next(g for g in d.gates if g.code == "q_run").ok


def test_stale_evidence_blocks_issue_but_not_revocation(env):
    later = datetime.now(timezone.utc) + timedelta(days=45)
    d = decide(env["systems"]["northwind-cs"], env["scan"], env["runs"]["good"], env["packs"], env["policy"], now=later)
    assert not d.eligible and d.revocation_failures == []


def test_signature_and_expiry(env, tmp_path):
    key = load_or_create_key(tmp_path / "k.pem")
    d = decide(env["systems"]["northwind-cs"], env["scan"], env["runs"]["good"], env["packs"], env["policy"])
    envlp = issue(env["systems"]["northwind-cs"], d, env["policy"], key, "https://x.test/certs/{id}")
    assert verify(envlp, key.public_key())
    tampered = json.loads(json.dumps(envlp)); tampered["certificate"]["assurance"]["quality"]["score"] = 100
    assert not verify(tampered, key.public_key())
    assert not verify(envlp, load_or_create_key(tmp_path / "other.pem").public_key())
    c = envlp["certificate"]
    assert c["demo"] is True and c["verify_url"].startswith("https://x.test/certs/AEV-")
    issued = datetime.fromisoformat(c["issued_at"])
    assert datetime.fromisoformat(c["expires_at"]) == add_months(issued, 6)
    assert certificate_state(envlp, now=issued + timedelta(days=200)) == "expired"
    assert certificate_state(envlp, "revoked") == "revoked"
    html_en, html_zh = render_certificate(envlp, "en"), render_certificate(envlp, "zh")
    assert "AI System Assurance Certificate" in html_en and "DEMO" in html_en and "<svg" in html_en
    assert "AI 系统安全与质量认证证书" in html_zh and "加利福尼亚州" in html_zh


def test_add_months_month_end():
    assert add_months(datetime(2026, 8, 31, tzinfo=timezone.utc), 6).date().isoformat() == "2027-02-28"
    assert add_months(datetime(2027, 8, 31, tzinfo=timezone.utc), 6).date().isoformat() == "2028-02-29"


# ---------------- CLI ----------------

def test_cli_check_issue_verify(env, tmp_path, monkeypatch):
    root = env["root"]
    monkeypatch.setenv("AGENTEVAL_SIGNING_KEY", str(tmp_path / "key.pem"))
    monkeypatch.setenv("AGENTEVAL_CERT_DIR", str(root / "certification"))
    scan = tmp_path / "scan.json"; scan.write_text(env["scan"].model_dump_json(), encoding="utf-8")
    good = tmp_path / "good.json"; good.write_text(env["runs"]["good"].model_dump_json(), encoding="utf-8")
    weak = tmp_path / "weak.json"; weak.write_text(env["runs"]["weak"].model_dump_json(), encoding="utf-8")
    r = CliRunner()
    base = ["--scan", str(scan), "--rules", str(root / "rules"), "--lang", "en"]
    assert r.invoke(cli_app, ["cert", "check", "-s", "northwind-cs", "--eval", str(good)] + base).exit_code == 0
    assert r.invoke(cli_app, ["cert", "check", "-s", "northwind-cs-staging", "--eval", str(weak)] + base).exit_code == 1
    out = tmp_path / "certs"
    res = r.invoke(cli_app, ["cert", "issue", "-s", "northwind-cs", "--eval", str(good), "-o", str(out)] + base)
    assert res.exit_code == 0, res.output
    cert = next(out.glob("*.json"))
    assert {p.suffix for p in out.iterdir()} == {".json", ".html"}
    assert r.invoke(cli_app, ["cert", "verify", str(cert), "--lang", "en"]).exit_code == 0
    data = json.loads(cert.read_text()); data["certificate"]["demo"] = False
    cert.write_text(json.dumps(data))
    assert r.invoke(cli_app, ["cert", "verify", str(cert)]).exit_code == 1


def test_cli_verify_with_published_public_key(env, tmp_path, monkeypatch):
    root = env["root"]
    monkeypatch.setenv("AGENTEVAL_SIGNING_KEY", str(tmp_path / "key.pem"))
    monkeypatch.setenv("AGENTEVAL_CERT_DIR", str(root / "certification"))
    r = CliRunner()
    assert r.invoke(cli_app, ["cert", "keygen", "--public-out", str(tmp_path / "issuer.pem")]).exit_code == 0
    scan = tmp_path / "scan.json"; scan.write_text(env["scan"].model_dump_json(), encoding="utf-8")
    good = tmp_path / "good.json"; good.write_text(env["runs"]["good"].model_dump_json(), encoding="utf-8")
    out = tmp_path / "certs"
    res = r.invoke(cli_app, ["cert", "issue", "-s", "northwind-cs", "--scan", str(scan), "--eval", str(good),
                             "--rules", str(root / "rules"), "-o", str(out)])
    assert res.exit_code == 0, res.output
    cert = next(out.glob("*.json"))
    assert "agenteval cert verify" in next(out.glob("*.en.html")).read_text(encoding="utf-8")  # offline fallback, no QR
    monkeypatch.setenv("AGENTEVAL_SIGNING_KEY", str(tmp_path / "missing.pem"))
    assert r.invoke(cli_app, ["cert", "verify", str(cert)]).exit_code == 2           # no key → usage error
    assert r.invoke(cli_app, ["cert", "verify", str(cert), "--public-key", str(tmp_path / "issuer.pem")]).exit_code == 0
