# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Certification decision: aggregate scan and evaluation evidence within an AI system's scope and check each gate.

The security score counts only findings on the system's own assets plus organization-level (cluster) assets that
belong to the system's registered rule packs. Issues in other workloads of the same cluster neither hurt nor help it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agenteval.evaluation.models import EvalRun
from agenteval.models import RulePack, ScanResult, Status

from .models import AISystem, CertPolicy, Decision, Gate, PackResult, QualityAssessment, SecurityAssessment


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _pct(p: int, f: int) -> float | None:
    return round(100.0 * p / (p + f), 1) if (p + f) else None


def in_scope(system: AISystem, finding) -> bool:
    return (finding.asset_id == system.asset_id or finding.asset_type == "cluster") and finding.pack in system.rule_packs


def assess_security(system: AISystem, scan: ScanResult | None, packs: dict[str, RulePack],
                    policy: CertPolicy) -> SecurityAssessment:
    sa = SecurityAssessment()
    for pid in system.rule_packs:
        p = packs.get(pid)
        sa.packs.append(PackResult(id=pid, name=p.name if p else pid, name_zh=p.name_zh if p else "",
                                   tier=p.tier if p else "unknown", jurisdiction=p.jurisdiction if p else ""))
    if scan is None:
        return sa
    sa.scan_id, sa.scanned_at, sa.rules_version = scan.scan_id, _aware(scan.finished_at or scan.started_at), scan.rules_version
    by_pack = {p.id: p for p in sa.packs}
    seen_pack: set[str] = set()
    for f in scan.findings:
        if not in_scope(system, f):
            continue
        pr = by_pack[f.pack]
        seen_pack.add(f.pack)
        if f.status == Status.passed:
            pr.passed += 1; sa.passed += 1
        elif f.status in (Status.failed, Status.error):
            pr.failed += 1; sa.failed += 1
            if f.severity.value in policy.block_on_severities:
                sa.blocking_findings.append(f"{f.rule_id}@{f.asset_id}")
        elif f.status == Status.manual:
            pr.manual += 1; sa.manual += 1
            sa.pending.append(f"{f.rule_id}@{f.asset_id}")
        else:
            pr.not_applicable += 1
    for pr in sa.packs:
        pr.score = _pct(pr.passed, pr.failed)
        if pr.id not in seen_pack:
            pr.status = "not_assessed"
        elif pr.passed + pr.failed + pr.manual == 0:
            pr.status = "not_applicable"
        elif pr.failed == 0 and pr.manual == 0:
            pr.status = "passed"
        else:
            pr.status = "failed"
    sa.score = _pct(sa.passed, sa.failed)
    return sa


def assess_quality(run: EvalRun | None) -> QualityAssessment:
    qa = QualityAssessment()
    if run is None:
        return qa
    qa.run_id, qa.evaluated_at = run.run_id, _aware(run.finished_at or run.started_at)
    qa.exam, qa.exam_version, qa.agent_name, qa.judge = run.paper_id, run.paper_version, run.agent_name, run.judge
    qa.score, qa.grade = run.overall_score, run.grade
    qa.critical_failures, qa.errors = run.stats.critical_failures, run.stats.errors
    qa.cases, qa.cases_passed = run.stats.cases, run.stats.cases_passed
    qa.dimensions = [{"id": d.id, "name": d.name, "weight": d.weight, "score": d.score} for d in run.dimensions]
    return qa


def decide(system: AISystem, scan: ScanResult | None, run: EvalRun | None, packs: dict[str, RulePack],
           policy: CertPolicy, now: datetime | None = None) -> Decision:
    now = _aware(now) or _now()
    sa = assess_security(system, scan, packs, policy)
    qa = assess_quality(run)
    g: list[Gate] = []
    max_age = policy.max_evidence_age_days

    # ---- Security (scan) ----
    asset_seen = scan is not None and any(f.asset_id == system.asset_id for f in scan.findings)
    g.append(Gate(code="sec_asset_scanned", ok=asset_seen, params={"asset": system.asset_id}))
    missing = [p.id for p in sa.packs if p.status == "not_assessed"]
    g.append(Gate(code="sec_packs_assessed", ok=asset_seen and not missing, params={"missing": missing}))
    g.append(Gate(code="sec_score", ok=sa.score is not None and sa.score > policy.min_security_score,
                  params={"score": sa.score, "threshold": policy.min_security_score}))
    g.append(Gate(code="sec_no_blocking", ok=asset_seen and not sa.blocking_findings,
                  params={"items": sa.blocking_findings, "severities": policy.block_on_severities}))
    if policy.require_no_pending_attestations:
        g.append(Gate(code="sec_no_pending", ok=asset_seen and not sa.pending, params={"items": sa.pending}))
    scan_age = (now - sa.scanned_at).days if sa.scanned_at else None
    g.append(Gate(code="sec_fresh", kind="freshness", ok=scan_age is not None and scan_age <= max_age,
                  params={"age": scan_age, "max": max_age}))

    # ---- Quality (evaluation) ----
    subject_ok = run is not None and run.agent_name == system.agent_name and run.paper_id == system.exam
    g.append(Gate(code="q_run", ok=subject_ok, params={"agent": system.agent_name, "exam": system.exam}))
    g.append(Gate(code="q_score", ok=subject_ok and qa.score is not None and qa.score > policy.min_quality_score,
                  params={"score": qa.score, "threshold": policy.min_quality_score}))
    if policy.require_zero_critical_eval_failures:
        g.append(Gate(code="q_no_critical", ok=subject_ok and qa.critical_failures == 0,
                      params={"n": qa.critical_failures}))
    if policy.require_no_eval_errors:
        g.append(Gate(code="q_no_errors", ok=subject_ok and qa.errors == 0, params={"n": qa.errors}))
    run_age = (now - qa.evaluated_at).days if qa.evaluated_at else None
    g.append(Gate(code="q_fresh", kind="freshness", ok=run_age is not None and run_age <= max_age,
                  params={"age": run_age, "max": max_age}))
    demo = False
    if policy.require_llm_judge:
        llm = bool(qa.judge) and qa.judge != "heuristic"
        demo = subject_ok and not llm
        g.append(Gate(code="q_llm_judge", kind="demo" if policy.allow_demo_certificates else "blocking",
                      ok=llm, params={"judge": qa.judge}))

    eligible = all(x.ok for x in g if x.kind in ("blocking", "freshness"))
    return Decision(system_id=system.id, evaluated_at=now, eligible=eligible, demo=eligible and demo,
                    gates=g, security=sa, quality=qa)
