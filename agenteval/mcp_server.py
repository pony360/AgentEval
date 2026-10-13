# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""AgentEval as an MCP (Model Context Protocol) server over stdio.

    agenteval mcp serve [--root DIR] [--allow-network]

A dependency-free implementation of the MCP stdio transport: newline-delimited
JSON-RPC 2.0 on stdin/stdout, with `initialize`, `ping`, `tools/list` and
`tools/call`. Logs go to stderr only, so stdout carries nothing but protocol messages.

Safety:
- every file path argument must resolve inside --root (default: the working directory);
- tools that reach the network (fetching a remote Agent Card, running an exam against a
  remote agent) are refused unless the server was started with --allow-network;
- no tool writes files; results are returned to the client.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from agenteval import __version__

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "agenteval"

INSTRUCTIONS = (
    "AgentEval scans AI infrastructure, A2A Agent Cards and MCP client configs against policy-as-code rule packs, "
    "evaluates AI agents with standard exam papers, and checks or verifies signed certificates. "
    "File paths are relative to the server root. Findings are technical control checks, not legal advice."
)


class ToolError(Exception):
    """An error reported to the client as a tool result with isError=true."""


class Server:
    def __init__(self, root: Path | None = None, allow_network: bool = False):
        self.root = (root or Path.cwd()).resolve()
        self.allow_network = allow_network
        self.tools: dict[str, dict[str, Any]] = {}
        self.handlers: dict[str, Callable[[dict], Any]] = {}
        _register_tools(self)

    # ---------------- helpers for tools ----------------
    def path(self, value: Any, *, must_exist: bool = True) -> Path:
        if not isinstance(value, str) or not value:
            raise ToolError("Expected a non-empty path string")
        p = (self.root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
        if p != self.root and self.root not in p.parents:
            raise ToolError(f"Path is outside the server root ({self.root}): {value}")
        if must_exist and not p.exists():
            raise ToolError(f"Not found: {value}")
        return p

    def content_dir(self, value: Any, name: str, env: str) -> Path:
        if value:
            return self.path(value)
        from agenteval.config import content_dir

        return content_dir(name, env)

    def need_network(self, what: str) -> None:
        if not self.allow_network:
            raise ToolError(f"{what} needs network access; restart the server with --allow-network")

    def tool(self, name: str, title: str, description: str, schema: dict, *, read_only: bool = True,
             open_world: bool = False):
        def deco(fn):
            self.tools[name] = {
                "name": name, "title": title, "description": description,
                "inputSchema": {"type": "object", "additionalProperties": False, **schema},
                "annotations": {"title": title, "readOnlyHint": read_only, "destructiveHint": False,
                                "idempotentHint": read_only, "openWorldHint": open_world},
            }
            self.handlers[name] = fn
            return fn
        return deco

    # ---------------- protocol ----------------
    def handle(self, msg: Any) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error(None, -32600, "Invalid Request")
        mid, method = msg.get("id"), msg.get("method")
        if "method" not in msg:          # a response to something we never sent: ignore
            return None
        if mid is None:                   # notification (e.g. notifications/initialized): no reply
            return None
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                requested = params.get("protocolVersion")
                version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
                return _result(mid, {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "title": "AgentEval", "version": __version__},
                    "instructions": INSTRUCTIONS,
                })
            if method == "ping":
                return _result(mid, {})
            if method == "tools/list":
                return _result(mid, {"tools": list(self.tools.values())})
            if method == "tools/call":
                return _result(mid, self.call(params.get("name"), params.get("arguments") or {}))
            return _error(mid, -32601, f"Method not found: {method}")
        except Exception as e:  # noqa: BLE001 - protocol boundary
            print(traceback.format_exc(), file=sys.stderr)
            return _error(mid, -32603, f"Internal error: {e}")

    def call(self, name: Any, args: dict) -> dict:
        if name not in self.handlers:
            return _tool_result({"error": f"Unknown tool: {name}"}, is_error=True)
        if not isinstance(args, dict):
            return _tool_result({"error": "arguments must be an object"}, is_error=True)
        allowed = set(self.tools[name]["inputSchema"].get("properties", {}))
        unknown = sorted(set(args) - allowed)
        if unknown:
            return _tool_result({"error": f"Unknown argument(s): {', '.join(unknown)}"}, is_error=True)
        missing = [k for k in self.tools[name]["inputSchema"].get("required", []) if k not in args]
        if missing:
            return _tool_result({"error": f"Missing argument(s): {', '.join(missing)}"}, is_error=True)
        try:
            return _tool_result(self.handlers[name](args))
        except ToolError as e:
            return _tool_result({"error": str(e)}, is_error=True)
        except Exception as e:  # noqa: BLE001 - report library errors to the model, not as protocol errors
            print(traceback.format_exc(), file=sys.stderr)
            return _tool_result({"error": f"{type(e).__name__}: {e}"}, is_error=True)

    def serve(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                reply = _error(None, -32700, "Parse error")
            else:
                if isinstance(msg, list):  # JSON-RPC batch (older protocol revisions)
                    replies = [r for r in (self.handle(m) for m in msg) if r is not None]
                    reply = replies or None
                else:
                    reply = self.handle(msg)
            if reply is not None:
                stdout.write(json.dumps(reply, ensure_ascii=False, default=str) + "\n")
                stdout.flush()


def _result(mid: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _tool_result(data: Any, is_error: bool = False) -> dict:
    structured = data if isinstance(data, dict) else {"result": data}
    return {"content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False, indent=2, default=str)}],
            "structuredContent": structured, "isError": is_error}


# ---------------- tools ----------------

_LANG = {"type": "string", "enum": ["en", "zh"], "default": "en", "description": "Language for titles and messages."}


def _finding_rows(result, lang: str, include_passed: bool) -> list[dict]:
    from agenteval.i18n import localize_result

    loc = localize_result(result, lang)
    rows = []
    for f in loc.findings:
        status = f.status.value
        if status == "not_applicable" or (status == "pass" and not include_passed):
            continue
        rows.append({"rule_id": f.rule_id, "asset_id": f.asset_id, "status": status, "severity": f.severity.value,
                     "title": f.rule_title, "message": f.message, "remediation": f.remediation})
    return rows


def _scan_summary(result, lang: str, include_passed: bool) -> dict:
    s = result.summary
    return {"scan_id": result.scan_id, "source": result.source, "rules_version": result.rules_version,
            "assets": result.asset_count, "score": s.score, "by_status": s.by_status,
            "findings": _finding_rows(result, lang, include_passed)}


def _register_tools(srv: Server) -> None:  # noqa: C901 - a flat list of tool definitions
    @srv.tool("list_rules", "List rules", "List the loaded rules with pack, severity and title.",
              {"properties": {"rules_dir": {"type": "string", "description": "Rules directory (default ./rules)."},
                              "pack": {"type": "string", "description": "Only rules from this pack ID."},
                              "lang": _LANG}})
    def list_rules(a):
        from agenteval.engine import load_rules

        rules = load_rules(srv.content_dir(a.get("rules_dir"), "rules", "AGENTEVAL_RULES_DIR"))
        lang = a.get("lang", "en")
        return {"rules": [{"id": r.id, "pack": r.pack, "severity": r.severity.value, "type": r.type.value,
                           "asset_types": r.applies_to.asset_types,
                           "title": (r.translations.get(lang) or {}).get("title") or r.title}
                          for r in rules if not a.get("pack") or r.pack == a["pack"]]}

    @srv.tool("validate_rules", "Validate rules", "Validate rule packs against the JSON Schemas.",
              {"properties": {"rules_dir": {"type": "string"}}})
    def validate_rules(a):
        from agenteval.engine import load_packs, load_rules
        from agenteval.engine.loader import RuleLoadError

        d = srv.content_dir(a.get("rules_dir"), "rules", "AGENTEVAL_RULES_DIR")
        try:
            rules = load_rules(d)
            packs = load_packs(d)
        except RuleLoadError as e:
            return {"valid": False, "errors": e.errors}
        unverified = sum(1 for r in rules for s in r.sources if not s.verified)
        return {"valid": True, "rules": len(rules), "packs": sorted(packs), "unverified_mappings": unverified}

    @srv.tool("scan", "Scan", "Scan an offline Kubernetes snapshot, A2A Agent Cards and/or MCP client configs "
              "against the rule packs. Returns the score and failed or pending findings.",
              {"properties": {
                  "snapshot": {"type": "string", "description": "Snapshot JSON exported by `agenteval snapshot`."},
                  "a2a_cards": {"type": "array", "items": {"type": "string"},
                                "description": "Agent Card files, or https URLs (needs --allow-network)."},
                  "mcp_configs": {"type": "array", "items": {"type": "string"},
                                  "description": "MCP client config files (mcp.json, claude_desktop_config.json)."},
                  "attestations": {"type": "array", "items": {"type": "string"}},
                  "rules_dir": {"type": "string"},
                  "include_passed": {"type": "boolean", "default": False},
                  "lang": _LANG}})
    def scan(a):
        from agenteval.collectors import collect_from_snapshot_file
        from agenteval.collectors.a2a import collect_a2a_cards
        from agenteval.collectors.mcp import collect_mcp_configs
        from agenteval.config import load_attestations
        from agenteval.engine import load_rules, run_scan

        assets, labels = [], []
        if a.get("snapshot"):
            assets += collect_from_snapshot_file(srv.path(a["snapshot"]))[0]
            labels.append(f"snapshot:{Path(a['snapshot']).name}")
        cards = []
        for c in a.get("a2a_cards") or []:
            if c.startswith(("http://", "https://")):
                srv.need_network("Fetching a remote Agent Card")
                cards.append(c)
            else:
                cards.append(str(srv.path(c)))
        if cards:
            assets += collect_a2a_cards(cards)
            labels.append(f"a2a:{len(cards)}")
        if a.get("mcp_configs"):
            assets += collect_mcp_configs([srv.path(p) for p in a["mcp_configs"]])
            labels.append(f"mcp:{len(a['mcp_configs'])}")
        if not assets and not labels:
            raise ToolError("Nothing to scan: pass snapshot, a2a_cards or mcp_configs")
        atts = load_attestations([srv.path(p) for p in a.get("attestations") or []])
        rules = load_rules(srv.content_dir(a.get("rules_dir"), "rules", "AGENTEVAL_RULES_DIR"))
        result = run_scan(rules, assets, source=" + ".join(labels), attestations=atts)
        return _scan_summary(result, a.get("lang", "en"), bool(a.get("include_passed")))

    @srv.tool("inspect_a2a_card", "Inspect A2A Agent Card", "Parse an A2A Agent Card (v0.3 or v1.0): endpoints, "
              "protocol versions, auth schemes, signing and skills, plus the agent-protocols rule results.",
              {"properties": {"card": {"type": "string", "description": "Card file, or https URL (needs --allow-network)."},
                              "lang": _LANG},
               "required": ["card"]})
    def inspect_a2a_card(a):
        from agenteval.collectors.a2a import fetch_card, normalize_card
        from agenteval.engine import load_rules, run_scan

        target = a["card"]
        if target.startswith(("http://", "https://")):
            srv.need_network("Fetching a remote Agent Card")
        else:
            target = str(srv.path(target))
        asset = normalize_card(*fetch_card(target))
        rules = [r for r in load_rules(srv.content_dir(None, "rules", "AGENTEVAL_RULES_DIR"))
                 if "a2a_agent" in r.applies_to.asset_types]
        result = run_scan(rules, [asset], source="a2a-card")
        return {"asset_id": asset.id, "facts": asset.facts, **_scan_summary(result, a.get("lang", "en"), True)}

    @srv.tool("list_exam_papers", "List exam papers", "List the exam papers in the exam library.",
              {"properties": {"exams_dir": {"type": "string"}}})
    def list_exam_papers(a):
        from agenteval.evaluation import load_papers

        papers = load_papers(srv.content_dir(a.get("exams_dir"), "exams", "AGENTEVAL_EXAMS_DIR"))
        return {"papers": [{"id": p.id, "title": p.title, "version": p.version, "domain": p.domain,
                            "cases": len(p.cases), "passing_score": p.passing_score} for p in papers.values()]}

    @srv.tool("run_eval", "Run evaluation", "Run an exam paper against an agent config and return the scores. "
              "Agents other than the offline demo adapter need --allow-network.",
              {"properties": {"paper": {"type": "string", "description": "Exam paper ID or file."},
                              "agent_config": {"type": "string", "description": "Agent-under-test YAML file."},
                              "cases": {"type": "array", "items": {"type": "string"}},
                              "exams_dir": {"type": "string"},
                              "judge": {"type": "string", "enum": ["heuristic", "anthropic", "openai"],
                                        "default": "heuristic"},
                              "judge_model": {"type": "string"},
                              "lang": _LANG},
               "required": ["paper", "agent_config"]},
              read_only=False, open_world=True)
    def run_eval(a):
        from agenteval.evaluation import Agent, load_agent_config, make_judge, resolve_paper, run_exam

        cfg = load_agent_config(srv.path(a["agent_config"]))
        if cfg.adapter != "demo" or a.get("judge", "heuristic") != "heuristic":
            srv.need_network(f"Evaluating a '{cfg.adapter}' agent or using an LLM judge")
        paper_ref = a["paper"]
        if paper_ref.endswith((".yaml", ".yml")):
            paper_ref = str(srv.path(paper_ref))
        paper = resolve_paper(paper_ref, srv.content_dir(a.get("exams_dir"), "exams", "AGENTEVAL_EXAMS_DIR"))
        judge = make_judge(a.get("judge", "heuristic"), model=a.get("judge_model"))
        run = run_exam(paper, Agent(cfg), judge, case_ids=a.get("cases") or None)
        return {"run_id": run.run_id, "agent": run.agent_name, "paper": f"{run.paper_id}@{run.paper_version}",
                "overall_score": run.overall_score, "grade": run.grade, "passed": run.passed,
                "judge": run.judge, "stats": run.stats.model_dump(),
                "dimensions": [d.model_dump() for d in run.dimensions],
                "failed_cases": [{"id": c.id, "title": c.title, "score": c.score, "critical": c.critical_failures,
                                  "error": c.error} for c in run.cases if not c.passed]}

    @srv.tool("check_certification", "Check certification", "Evaluate the certification gates for a registered "
              "system from a scan result and an evaluation result (JSON files). Issues nothing.",
              {"properties": {"system": {"type": "string", "description": "System ID or system YAML file."},
                              "scan_result": {"type": "string"}, "eval_result": {"type": "string"},
                              "rules_dir": {"type": "string"}, "cert_dir": {"type": "string"}},
               "required": ["system", "scan_result", "eval_result"]})
    def check_certification(a):
        from agenteval.certification import decide, load_policy, load_systems
        from agenteval.certification.issuer import load_system_file
        from agenteval.engine import load_packs
        from agenteval.evaluation.models import EvalRun
        from agenteval.models import ScanResult

        cert_dir = srv.content_dir(a.get("cert_dir"), "certification", "AGENTEVAL_CERT_DIR")
        ref = a["system"]
        if ref.endswith((".yaml", ".yml")):
            system = load_system_file(srv.path(ref))
        else:
            systems = load_systems(cert_dir)
            if ref not in systems:
                raise ToolError(f"Unknown system: {ref} (known: {', '.join(sorted(systems))})")
            system = systems[ref]
        scan = ScanResult.model_validate_json(srv.path(a["scan_result"]).read_text(encoding="utf-8"))
        run = EvalRun.model_validate_json(srv.path(a["eval_result"]).read_text(encoding="utf-8"))
        packs = load_packs(srv.content_dir(a.get("rules_dir"), "rules", "AGENTEVAL_RULES_DIR"))
        d = decide(system, scan, run, packs, load_policy(cert_dir))
        return {"system": system.id, "eligible": d.eligible, "demo": d.demo,
                "gates": [g.model_dump() for g in d.gates]}

    @srv.tool("verify_certificate", "Verify certificate", "Verify a certificate's Ed25519 signature and expiry "
              "offline with the issuer's public key.",
              {"properties": {"certificate": {"type": "string", "description": "Certificate JSON envelope."},
                              "public_key": {"type": "string", "description": "Issuer public key PEM."}},
               "required": ["certificate", "public_key"]})
    def verify_certificate(a):
        from agenteval.certification import certificate_state, load_public_key, verify

        env = json.loads(srv.path(a["certificate"]).read_text(encoding="utf-8"))
        pub = load_public_key(srv.path(a["public_key"]).read_bytes())
        ok = verify(env, pub)
        c = env.get("certificate", {})
        return {"id": c.get("id"), "signature_valid": ok, "state": certificate_state(env) if ok else "invalid",
                "demo": c.get("demo"), "subject": (c.get("subject") or {}).get("name"),
                "expires_at": c.get("expires_at"), "revocation_checked": False}


def serve(root: Path | None = None, allow_network: bool = False) -> None:
    # Keep stdout clean for the protocol even if a library prints.
    out = sys.stdout
    sys.stdout = sys.stderr
    try:
        Server(root, allow_network).serve(sys.stdin, out)
    finally:
        sys.stdout = out
