# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""MCP: client-config scanning and the `agenteval mcp serve` stdio server."""

import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from agenteval.cli import app
from agenteval.collectors.mcp import MCPConfigError, collect_mcp_configs, normalize_server
from agenteval.engine import load_rules, run_scan
from agenteval.mcp_server import SUPPORTED_PROTOCOL_VERSIONS, Server


def _status(rules_dir, assets):
    rules = [r for r in load_rules(rules_dir) if "mcp_server" in r.applies_to.asset_types]
    out = {}
    for f in run_scan(rules, assets, "t").findings:
        if f.status.value != "not_applicable":
            out.setdefault(f.asset_id.split("/")[-1], {})[f.rule_id] = f.status.value
    return out


def test_example_config_findings(root, rules_dir):
    s = _status(rules_dir, collect_mcp_configs([root / "examples/mcp/mcp.json"]))
    assert all(v == "pass" for k in ("filesystem", "github", "postgres") for v in s[k].values())
    assert s["weather-unpinned"]["PROTO-MCP-003"] == "fail" and s["weather-unpinned"]["PROTO-MCP-004"] == "fail"
    assert s["remote-insecure"]["PROTO-MCP-001"] == "fail" and s["remote-insecure"]["PROTO-MCP-002"] == "fail"
    assert s["installer"]["PROTO-MCP-005"] == "fail"
    assert s["docker-root"]["PROTO-MCP-006"] == "fail" and s["docker-root"]["PROTO-MCP-004"] == "fail"


@pytest.mark.parametrize("entry,field,expected", [
    ({"command": "npx", "args": ["-y", "@scope/pkg"]}, ("package", "pinned"), False),
    ({"command": "npx", "args": ["-y", "@scope/pkg@1.2.3"]}, ("package", "pinned"), True),
    ({"command": "uvx", "args": ["--from", "mcp-x==0.4.1", "mcp-x"]}, ("package", "pinned"), True),
    ({"command": "docker", "args": ["run", "-i", "img:1.0"]}, ("package", "pinned"), True),
    ({"command": "docker", "args": ["run", "--net", "host", "img@sha256:ab"]}, ("docker", "host_network"), True),
    ({"command": "npx -y srv@1.0.0"}, ("package", "name"), "srv@1.0.0"),
    ({"url": "http://localhost:3000/mcp"}, ("tls", "enabled"), True),
    ({"url": "https://x.example.com/mcp", "headers": {"Authorization": "Bearer ${TOK}"}}, ("secrets", "plaintext"), []),
    ({"url": "https://x.example.com/mcp", "headers": {"Authorization": "Bearer abc123"}}, ("secrets", "plaintext"),
     ["header:Authorization"]),
    ({"command": "node", "env": {"API_KEY": "${input:key}", "MODE": "prod"}}, ("secrets", "plaintext"), []),
])
def test_normalize_server_facts(entry, field, expected):
    facts = normalize_server("s", entry, "cfg").facts
    assert facts[field[0]][field[1]] == expected


def test_vscode_shape_and_disabled_and_errors(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"servers": {"a": {"type": "stdio", "command": "node"},
                                         "b": {"command": "node", "disabled": True}}}))
    assert [a.name for a in collect_mcp_configs([p])] == ["a"]
    p.write_text("[]")
    with pytest.raises(MCPConfigError):
        collect_mcp_configs([p])


def test_cli_scan_mcp_and_a2a_only(root):
    res = CliRunner().invoke(app, ["scan", "--mcp-config", str(root / "examples/mcp/mcp.json"),
                                   "--a2a-card", str(root / "examples/a2a/northwind-support.agent-card.json"),
                                   "--fail-on", "critical"])
    assert res.exit_code == 1  # PROTO-MCP-003 / 005 are critical
    doc = json.loads(res.stdout[res.stdout.index("{"):res.stdout.rindex("}") + 1])
    assert doc["asset_count"] == 8 and "mcp:mcp.json" in doc["source"]
    assert CliRunner().invoke(app, ["scan"]).exit_code == 2


# ---------------- MCP server ----------------

def _call(srv, name, args=None, mid=1):
    reply = srv.handle({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                        "params": {"name": name, "arguments": args or {}}})
    return reply["result"]


def test_protocol_handshake_and_listing(root):
    srv = Server(root)
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05", "capabilities": {}}})["result"]
    assert init["protocolVersion"] == "2024-11-05" and init["serverInfo"]["name"] == "agenteval"
    unknown = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                          "params": {"protocolVersion": "1999-01-01"}})["result"]
    assert unknown["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert srv.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"] == -32601
    tools = {t["name"]: t for t in srv.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})["result"]["tools"]}
    assert {"scan", "inspect_a2a_card", "run_eval", "verify_certificate"} <= set(tools)
    assert tools["scan"]["annotations"]["readOnlyHint"] is True
    assert tools["run_eval"]["annotations"]["openWorldHint"] is True


def test_tools_scan_eval_and_guards(root):
    srv = Server(root)
    r = _call(srv, "scan", {"mcp_configs": ["examples/mcp/mcp.json"], "lang": "zh"})
    assert not r["isError"] and r["structuredContent"]["by_status"]["fail"] == 7
    assert any("明文密钥" in f["title"] for f in r["structuredContent"]["findings"])
    r = _call(srv, "inspect_a2a_card", {"card": "examples/a2a/legacy-helper.agent-card.json"})
    assert r["structuredContent"]["facts"]["oauth"]["deprecated_flows"] is True
    r = _call(srv, "run_eval", {"paper": "cs-ecommerce-v1", "agent_config": "examples/agents/demo-good.yaml",
                                "cases": ["CS-001", "CS-002"]})
    assert not r["isError"] and r["structuredContent"]["stats"]["cases"] == 2
    assert _call(srv, "validate_rules")["structuredContent"]["valid"] is True
    assert _call(srv, "list_exam_papers")["structuredContent"]["papers"][0]["id"] == "cs-ecommerce-v1"
    # guards
    assert _call(srv, "scan", {"mcp_configs": ["/etc/hosts"]})["isError"]
    assert _call(srv, "scan", {"a2a_cards": ["https://agent.example.com"]})["isError"]       # no --allow-network
    assert _call(srv, "run_eval", {"paper": "cs-ecommerce-v1",
                                   "agent_config": "examples/agents/openai-compatible.example.yaml"})["isError"]
    assert _call(srv, "scan", {"bogus": 1})["isError"]
    assert _call(srv, "inspect_a2a_card", {})["isError"]
    assert _call(srv, "nope")["isError"]


def test_certificate_tools(root, tmp_path, monkeypatch):
    from agenteval.certification import decide, issue, load_or_create_key, load_policy, load_systems, public_pem
    from agenteval.collectors import collect_from_snapshot_file
    from agenteval.config import load_attestations
    from agenteval.engine import load_packs
    from agenteval.evaluation import Agent, load_agent_config, make_judge, resolve_paper, run_exam, to_attestation

    systems = load_systems(root / "certification")
    good = run_exam(resolve_paper("cs-ecommerce-v1", root / "exams"),
                    Agent(load_agent_config(root / "examples/agents/demo-good.yaml")), make_judge("heuristic"))
    assets, _ = collect_from_snapshot_file(root / "examples/snapshots/sample-cluster.json")
    atts = load_attestations([root / "examples/attestations.yaml", root / "examples/attestations-certification.yaml"])
    scan = run_scan(load_rules(root / "rules"), assets, "t", atts + [to_attestation(good, systems["northwind-cs"].asset_id)])
    e = {"scan": scan, "runs": {"good": good}, "systems": systems, "packs": load_packs(root / "rules"),
         "policy": load_policy(root / "certification")}
    work = tmp_path / "w"
    work.mkdir()
    (work / "scan.json").write_text(e["scan"].model_dump_json())
    (work / "eval.json").write_text(e["runs"]["good"].model_dump_json())
    key = load_or_create_key(tmp_path / "k.pem")
    d = decide(e["systems"]["northwind-cs"], e["scan"], e["runs"]["good"], e["packs"], e["policy"])
    (work / "cert.json").write_text(json.dumps(issue(e["systems"]["northwind-cs"], d, e["policy"], key)))
    (work / "issuer.pem").write_text(public_pem(key))
    monkeypatch.setenv("AGENTEVAL_CERT_DIR", str(root / "certification"))
    monkeypatch.setenv("AGENTEVAL_RULES_DIR", str(root / "rules"))
    srv = Server(work)
    r = _call(srv, "check_certification", {"system": "northwind-cs", "scan_result": "scan.json",
                                           "eval_result": "eval.json"})["structuredContent"]
    assert r["eligible"] is True and r["demo"] is True
    v = _call(srv, "verify_certificate", {"certificate": "cert.json", "public_key": "issuer.pem"})["structuredContent"]
    assert v["signature_valid"] and v["state"] == "active"


def test_stdio_subprocess(root):
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "list_rules", "arguments": {"pack": "agent-protocols"}}},
    ]
    proc = subprocess.run([sys.executable, "-m", "agenteval.cli", "mcp", "serve", "--root", str(root)],
                          input="\n".join(json.dumps(m) for m in msgs) + "\nnot json\n",
                          capture_output=True, text=True, timeout=60, cwd=root)
    lines = [json.loads(x) for x in proc.stdout.splitlines()]
    assert [x.get("id") for x in lines] == [1, 2, None]          # parse error reply has id null
    assert lines[0]["result"]["protocolVersion"] == "2025-11-25"
    rules = lines[1]["result"]["structuredContent"]["rules"]
    assert len(rules) == 12 and all(r["id"].startswith("PROTO-") for r in rules)
    assert lines[2]["error"]["code"] == -32700
