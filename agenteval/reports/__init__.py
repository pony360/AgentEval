# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Report output: JSON / SARIF 2.1.0 / HTML."""

from __future__ import annotations

import json
from importlib import resources

from jinja2 import Environment, select_autoescape

from agenteval import __version__
from agenteval.i18n import DEFAULT_LANG, UI, localize_result
from agenteval.models import ScanResult, Severity, Status

FORMATS = ("json", "sarif", "html")

_SARIF_LEVEL = {
    Severity.critical: "error", Severity.high: "error",
    Severity.medium: "warning", Severity.low: "note", Severity.info: "note",
}


def to_json(result: ScanResult) -> str:
    return result.model_dump_json(indent=2)


def to_sarif(result: ScanResult) -> str:
    rules_seen: dict[str, dict] = {}
    results = []
    for f in result.findings:
        if f.status not in (Status.failed, Status.manual, Status.error):
            continue
        if f.rule_id not in rules_seen:
            rules_seen[f.rule_id] = {
                "id": f.rule_id,
                "name": f.rule_id.replace("-", ""),
                "shortDescription": {"text": f.rule_title},
                "help": {"text": f.remediation},
                "properties": {
                    "severity": f.severity.value,
                    "category": f.category,
                    "sources": [s.model_dump() for s in f.sources],
                },
            }
        results.append({
            "ruleId": f.rule_id,
            "level": _SARIF_LEVEL[f.severity] if f.status == Status.failed else "note",
            "kind": "fail" if f.status == Status.failed else "review",
            "message": {"text": f"[{f.asset_id}] {f.message}"},
            "locations": [{"logicalLocations": [{"fullyQualifiedName": f.asset_id, "kind": f.asset_type}]}],
            "properties": {"status": f.status.value, "evidence": f.evidence},
        })
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "agenteval", "version": __version__,
                "informationUri": "https://github.com/your-org/agenteval",
                "rules": list(rules_seen.values()),
            }},
            "automationDetails": {"id": f"agenteval/{result.scan_id}"},
            "properties": {"rulesVersion": result.rules_version, "summary": result.summary.model_dump()},
            "results": results,
        }],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False, default=str)


def to_html(result: ScanResult, lang: str = DEFAULT_LANG) -> str:
    env = Environment(autoescape=select_autoescape(default=True, default_for_string=True))
    env.filters["tojson_pretty"] = lambda v: json.dumps(v, ensure_ascii=False, indent=1, default=str)
    tpl_text = resources.files("agenteval").joinpath("reports/templates/report.html.j2").read_text(encoding="utf-8")
    return env.from_string(tpl_text).render(r=result, t=UI[lang])


def render(result: ScanResult, fmt: str, lang: str = DEFAULT_LANG) -> str:
    result = localize_result(result, lang)
    if fmt == "json":
        return to_json(result)
    if fmt == "sarif":
        return to_sarif(result)
    if fmt == "html":
        return to_html(result, lang)
    raise ValueError(f"Unsupported format: {fmt} (choose from: {', '.join(FORMATS)})")
