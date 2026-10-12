# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Rule evaluation: assets × rules → findings."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from agenteval.models import (
    Asset, Finding, Rule, RuleType, ScanResult, ScanSummary, Status,
)

from agenteval.i18n import DEFAULT_LANG, format_message

from .conditions import ConditionError, evaluate
from .loader import rules_version


def _match_attestation(rule: Rule, asset: Asset, attestations: list[dict[str, Any]]) -> dict | None:
    """Merge all attestations for this rule that apply to the asset (wildcard "*" and asset-level); asset-level evidence wins.

    When several attestations are merged, the earliest attested_at is kept (a conservative validity check), so
    manual material and evaluation-generated attestations (e.g. agenteval eval --attest-asset) can live in separate files.
    """
    matched = [a for a in attestations if a.get("rule_id") == rule.id
               and a.get("asset_id", "*") in ("*", asset.id)]
    if not matched:
        return None
    matched.sort(key=lambda a: a.get("asset_id", "*") != "*")  # wildcard first, asset-level overrides
    evidence: dict[str, Any] = {}
    dates, people = [], []
    for a in matched:
        evidence.update({k: v for k, v in (a.get("evidence") or {}).items() if v})
        if a.get("attested_at"):
            d = a["attested_at"]
            dates.append(date.fromisoformat(d) if isinstance(d, str) else (d.date() if isinstance(d, datetime) else d))
        if a.get("attested_by"):
            people.append(str(a["attested_by"]))
    return {"evidence": evidence, "attested_at": min(dates) if dates else None,
            "attested_by": ", ".join(dict.fromkeys(people)) or None}


def _evaluate_attestation(rule: Rule, asset: Asset, attestations, today: date) -> tuple[Status, str, dict, dict]:
    """Return (status, message code, message params, evidence)."""
    spec = rule.attestation or {}
    required = spec.get("evidence_required", [])
    att = _match_attestation(rule, asset, attestations)
    if att is None:
        return Status.manual, "att_missing", {"required": required}, {"required": required}

    evidence = att.get("evidence") or {}
    missing = [k for k in required if not evidence.get(k)]
    ev = {"attested_by": att.get("attested_by"), "attested_at": str(att.get("attested_at")),
          "provided": sorted(evidence.keys())}
    if missing:
        return Status.failed, "att_incomplete", {"missing": missing}, {**ev, "missing": missing}

    max_age = spec.get("max_age_days")
    attested_at = att.get("attested_at")
    if max_age and attested_at:
        if isinstance(attested_at, str):
            attested_at = date.fromisoformat(attested_at)
        if isinstance(attested_at, datetime):
            attested_at = attested_at.date()
        age = (today - attested_at).days
        ev["age_days"] = age
        if age > max_age:
            return Status.failed, "att_expired", {"age": age, "max_age": max_age}, ev
    elif max_age and not attested_at:
        return Status.failed, "att_no_date", {}, ev

    return Status.passed, "att_ok", {}, ev


def evaluate_rule(rule: Rule, asset: Asset, attestations: list[dict] | None = None,
                  today: date | None = None) -> Finding | None:
    """Return None if the rule does not target this asset type."""
    if asset.type not in rule.applies_to.asset_types:
        return None
    today = today or datetime.now(timezone.utc).date()
    evidence: dict[str, Any] = {}
    params: dict[str, Any] = {}

    try:
        if rule.applies_to.when is not None and not evaluate(rule.applies_to.when, asset.facts):
            status, code = Status.not_applicable, "not_applicable"
        elif rule.type == RuleType.attestation:
            status, code, params, evidence = _evaluate_attestation(rule, asset, attestations or [], today)
        else:
            ok = evaluate(rule.check or {}, asset.facts, evidence)
            status = Status.passed if ok else Status.failed
            code = "ok" if ok else "check_failed"
    except ConditionError as e:
        status, code, params = Status.error, "eval_error", {"error": str(e)}

    return Finding(
        rule_id=rule.id, pack=rule.pack, rule_title=rule.title, severity=rule.severity, category=rule.category,
        asset_id=asset.id, asset_name=asset.name, asset_type=asset.type,
        status=status, message=format_message(code, params, DEFAULT_LANG, rule.title),
        message_code=code, message_params=params, evidence=evidence,
        remediation=rule.remediation, sources=rule.sources, translations=rule.translations,
    )


def summarize(findings: list[Finding]) -> ScanSummary:
    s = ScanSummary(total=len(findings))
    for f in findings:
        s.by_status[f.status.value] = s.by_status.get(f.status.value, 0) + 1
        if f.status == Status.failed:
            s.failed_by_severity[f.severity.value] = s.failed_by_severity.get(f.severity.value, 0) + 1
    passed = s.by_status.get("pass", 0)
    failed = s.by_status.get("fail", 0)
    s.score = round(100.0 * passed / (passed + failed), 1) if (passed + failed) else 100.0
    return s


def run_scan(rules: list[Rule], assets: list[Asset], source: str,
             attestations: list[dict] | None = None, today: date | None = None) -> ScanResult:
    started = datetime.now(timezone.utc)
    findings: list[Finding] = []
    for asset in assets:
        for rule in rules:
            f = evaluate_rule(rule, asset, attestations, today)
            if f is not None:
                findings.append(f)
    findings.sort(key=lambda f: (f.status != Status.failed, -f.severity.rank, f.rule_id, f.asset_id))
    return ScanResult(
        scan_id=uuid.uuid4().hex[:12], source=source, started_at=started,
        finished_at=datetime.now(timezone.utc), rules_version=rules_version(rules),
        asset_count=len(assets), findings=findings, summary=summarize(findings),
    )
