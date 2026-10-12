# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Evaluation execution and overall scoring.

Scoring rules:
  - each item is normalized to [0, 1]: assertion pass = 1, fail = 0; rubric scores 1–5 map to (score-1)/4;
  - case score = 100 × Σ(item weight × item score) / Σ item weight; a failed critical assertion sets the case score to 0;
  - dimension score = 100 × Σ(case weight × item weight × item score) / Σ(case weight × item weight), over that dimension's items only;
  - overall score = weighted mean of dimension scores by the paper's dimension weights (dimensions without items are excluded and weights re-normalized);
  - pass condition: overall score ≥ passing_score and no critical failures.
"""

from __future__ import annotations

import statistics
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .adapters import Agent, AdapterError
from .assertions import check
from .judges import Judge
from .messages import fmt
from .models import (
    AssertionResult, CaseResult, DimensionScore, EvalRun, EvalStats, ExamPaper, RubricResult, TranscriptEntry,
)


def grade_for(score: float) -> str:
    for g, cut in (("A", 90), ("B", 80), ("C", 70), ("D", 60)):
        if score >= cut:
            return g
    return "F"


def _items(cr: CaseResult) -> list[tuple[str, float, float]]:
    """List of (dimension, weight, normalized score); rubric items whose judge failed (score=None) are excluded."""
    out = [(a.dimension, a.weight, 1.0 if a.passed else 0.0) for a in cr.assertions]
    out += [(r.dimension, r.weight, (r.score - 1) / 4) for r in cr.rubric if r.score is not None]
    return out


def run_case(paper: ExamPaper, case, agent: Agent, judge: Judge) -> CaseResult:
    cr = CaseResult(id=case.id, title=case.title, category=case.category,
                    difficulty=case.difficulty, weight=case.weight)
    session = None
    try:
        session = agent.new_session(paper, case)
        for i, turn in enumerate(case.turns, start=1):
            cr.transcript.append(TranscriptEntry(role="user", content=turn.user))
            reply = session.send(turn.user)
            cr.transcript.append(TranscriptEntry(role="agent", content=reply.text,
                                                 tool_calls=reply.tool_calls, latency_ms=reply.latency_ms))
            cr.assertions += [check(a, reply, i) for a in turn.expect]
    except AdapterError as e:
        cr.error, cr.error_code, cr.error_params = str(e), e.code, e.params
        # Assertions that never ran count as failures, so an erroring agent is not penalized less
        done = {(a.turn, a.type, str(a.value)) for a in cr.assertions}
        for i, turn in enumerate(case.turns, start=1):
            for a in turn.expect:
                if (i, a.type, str(a.value)) not in done:
                    cr.assertions.append(AssertionResult(
                        turn=i, type=a.type, value=a.value, dimension=a.dimension, weight=a.weight,
                        critical=a.critical, passed=False, detail=fmt("not_executed", {}),
                        detail_code="not_executed"))
    finally:
        if session is not None:
            session.close()

    if case.rubric:
        if cr.error:
            cr.rubric = [RubricResult(id=r.id, dimension=r.dimension, criterion=r.criterion, weight=r.weight,
                                      score=1, rationale=fmt("agent_error_min", {}), rationale_code="agent_error_min")
                        for r in case.rubric]
        else:
            cr.rubric = judge.score(paper, case, cr.transcript, case.rubric)

    cr.critical_failures = [f"T{a.turn}:{a.type}:{a.value}" for a in cr.assertions if a.critical and not a.passed]
    items = _items(cr)
    total_w = sum(w for _, w, _ in items)
    cr.score = round(100 * sum(w * s for _, w, s in items) / total_w, 1) if total_w else 0.0
    if cr.critical_failures or cr.error:
        cr.score = 0.0
    cr.passed = cr.score >= paper.case_passing_score
    return cr


def score_run(paper: ExamPaper, cases: list[CaseResult]) -> tuple[list[DimensionScore], float]:
    dims = []
    for d in paper.dimensions:
        num = den = 0.0
        n = 0
        for cr in cases:
            for dim, w, s in _items(cr):
                if dim == d.id:
                    num += cr.weight * w * s
                    den += cr.weight * w
                    n += 1
        dims.append(DimensionScore(id=d.id, name=d.name, weight=d.weight,
                                   score=round(100 * num / den, 1) if den else None, items=n))
    scored = [d for d in dims if d.score is not None]
    wsum = sum(d.weight for d in scored)
    overall = round(sum(d.weight * d.score for d in scored) / wsum, 1) if wsum else 0.0
    return dims, overall


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 1)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1], 1)


def run_exam(paper: ExamPaper, agent: Agent, judge: Judge, concurrency: int = 1,
             case_ids: list[str] | None = None) -> EvalRun:
    started = datetime.now(timezone.utc)
    cases = [c for c in paper.cases if not case_ids or c.id in case_ids]
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(pool.map(lambda c: run_case(paper, c, agent, judge), cases))

    dims, overall = score_run(paper, results)
    latencies = [t.latency_ms for r in results for t in r.transcript if t.role == "agent" and t.latency_ms is not None]
    crit = sum(len(r.critical_failures) for r in results)
    passed_cases = sum(1 for r in results if r.passed)
    stats = EvalStats(
        cases=len(results), cases_passed=passed_cases,
        pass_rate=round(100 * passed_cases / len(results), 1) if results else 0.0,
        critical_failures=crit, errors=sum(1 for r in results if r.error),
        latency_p50_ms=_pct(latencies, 50), latency_p95_ms=_pct(latencies, 95),
    )
    return EvalRun(
        run_id=uuid.uuid4().hex[:12], paper_id=paper.id, paper_title=paper.title, paper_version=paper.version,
        agent_name=agent.name, judge=judge.name, started_at=started, finished_at=datetime.now(timezone.utc),
        overall_score=overall, grade=grade_for(overall), passed=overall >= paper.passing_score and crit == 0,
        passing_score=paper.passing_score, dimensions=dims, cases=results, stats=stats,
    )


def to_attestation(run: EvalRun, asset_id: str, rule_id: str = "FED-GOV-A02",
                   attested_by: str = "agenteval-eval") -> dict:
    """Turn a passing evaluation into an attestation entry usable by the compliance scan (as pre_deployment_testing evidence)."""
    if not run.passed:
        raise ValueError("Evaluation did not pass and cannot serve as pre-deployment testing attestation")
    return {
        "rule_id": rule_id,
        "asset_id": asset_id,
        "attested_by": attested_by,
        "attested_at": run.finished_at.date().isoformat() if run.finished_at else None,
        "evidence": {
            "pre_deployment_testing": (f"agenteval-eval run {run.run_id}: {run.paper_id}@{run.paper_version} "
                                       f"score={run.overall_score} grade={run.grade} judge={run.judge}"),
        },
    }
