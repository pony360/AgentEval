# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Certification: AI system registry, issuance policy, gate decisions and certificate data structures."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class AISystem(BaseModel):
    """An AI system to certify: binds a scanned asset to an evaluated agent."""

    id: str
    name: str
    name_zh: str = ""
    organization: str
    industry: str = ""
    description: str = ""
    description_zh: str = ""
    asset_id: str                 # scanned asset ID, e.g. deployment/ml-prod/llama-chat
    model: str = ""
    agent: str = ""               # config file name in the agents directory (used by the API)
    agent_name: str               # agent name in evaluation records
    exam: str                     # exam paper ID
    rule_packs: list[str]         # rule pack IDs that must be covered
    contact: str = ""


class CertPolicy(BaseModel):
    min_security_score: float = 90.0
    min_quality_score: float = 90.0
    block_on_severities: list[str] = Field(default_factory=lambda: ["critical", "high"])
    require_no_pending_attestations: bool = True
    require_zero_critical_eval_failures: bool = True
    require_no_eval_errors: bool = True
    require_llm_judge: bool = True
    allow_demo_certificates: bool = True
    max_evidence_age_days: int = 30
    validity_months: int = 6
    auto_revoke: bool = True
    issuer_name: str = "AgentEval Assurance Service"
    issuer_name_zh: str = "AgentEval 认证服务"


class Gate(BaseModel):
    code: str
    ok: bool
    kind: Literal["blocking", "freshness", "demo"] = "blocking"
    params: dict[str, Any] = Field(default_factory=dict)


class PackResult(BaseModel):
    id: str
    name: str
    name_zh: str = ""
    tier: str
    jurisdiction: str
    passed: int = 0
    failed: int = 0
    manual: int = 0
    not_applicable: int = 0
    score: float | None = None
    status: Literal["passed", "failed", "not_applicable", "not_assessed"] = "not_assessed"


class SecurityAssessment(BaseModel):
    scan_id: str | None = None
    scanned_at: datetime | None = None
    rules_version: str | None = None
    score: float | None = None
    passed: int = 0
    failed: int = 0
    manual: int = 0
    blocking_findings: list[str] = Field(default_factory=list)   # "RULE@asset"
    pending: list[str] = Field(default_factory=list)
    packs: list[PackResult] = Field(default_factory=list)


class QualityAssessment(BaseModel):
    run_id: str | None = None
    evaluated_at: datetime | None = None
    exam: str | None = None
    exam_version: str | None = None
    agent_name: str | None = None
    judge: str | None = None
    score: float | None = None
    grade: str | None = None
    critical_failures: int = 0
    errors: int = 0
    cases: int = 0
    cases_passed: int = 0
    dimensions: list[dict[str, Any]] = Field(default_factory=list)


class Decision(BaseModel):
    system_id: str
    evaluated_at: datetime
    eligible: bool
    demo: bool
    gates: list[Gate]
    security: SecurityAssessment
    quality: QualityAssessment

    @property
    def revocation_failures(self) -> list[Gate]:
        """Failed gates that trigger revocation: substantive gates only; stale evidence does not revoke (certificate validity handles that)."""
        return [g for g in self.gates if not g.ok and g.kind == "blocking"]


CertStatus = Literal["active", "revoked", "expired", "superseded"]
