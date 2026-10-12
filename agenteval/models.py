# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Core data models: rules, assets, findings and scan results."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"

    @property
    def rank(self) -> int:
        return ["info", "low", "medium", "high", "critical"].index(self.value)


class RuleType(str, Enum):
    automated = "automated"      # Decided automatically by the scanner from collected facts
    attestation = "attestation"  # Requires a human-submitted attestation (e.g. an impact assessment)


class Status(str, Enum):
    passed = "pass"
    failed = "fail"
    not_applicable = "not_applicable"
    manual = "manual"            # Awaiting human attestation
    error = "error"


class Source(BaseModel):
    """Regulation/framework source a rule maps to."""

    framework: str
    reference: str
    url: str | None = None
    # Set to True only after legal/compliance has reviewed the mapping
    verified: bool = False


class Applicability(BaseModel):
    asset_types: list[str]
    when: dict[str, Any] | None = None  # Optional precondition; same syntax as check


class RulePack(BaseModel):
    """Rule pack, organized by tier (national / regional / industry)."""

    id: str
    name: str
    name_zh: str = ""
    tier: str  # national | regional | industry
    jurisdiction: str
    description: str = ""
    description_zh: str = ""


class Rule(BaseModel):
    id: str
    title: str
    description: str
    severity: Severity
    type: RuleType
    jurisdiction: str = "us-federal"
    category: str
    applies_to: Applicability
    check: dict[str, Any] | None = None
    attestation: dict[str, Any] | None = None
    remediation: str
    sources: list[Source]
    effective_date: date | None = None
    version: str = "1.0.0"
    enabled: bool = True
    pack: str = ""  # Set by the loader from the pack.yaml in the rule's directory
    # Attached by the loader from i18n/<lang>.yaml: {lang: {title, description, remediation, sources: [reference...]}}
    translations: dict[str, dict[str, Any]] = Field(default_factory=dict)


class Asset(BaseModel):
    """Normalized asset produced by a collector. facts holds the flat/nested facts used for rule evaluation."""

    id: str
    type: str
    name: str
    namespace: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    rule_id: str
    pack: str = ""
    rule_title: str
    severity: Severity
    category: str
    asset_id: str
    asset_name: str
    asset_type: str
    status: Status
    message: str                      # Default Chinese text (backward compatibility)
    message_code: str = ""            # for multilingual rendering; see agenteval/i18n.py
    message_params: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediation: str
    sources: list[Source]
    translations: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ScanSummary(BaseModel):
    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    failed_by_severity: dict[str, int] = Field(default_factory=dict)
    score: float = 100.0  # passed / (passed + failed); manual items are excluded


class ScanResult(BaseModel):
    scan_id: str
    source: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    rules_version: str
    asset_count: int
    findings: list[Finding]
    summary: ScanSummary
    disclaimer: str = (
        "This report is an evidence-based assessment of technical controls and does not constitute legal "
        "advice or a determination of regulatory compliance. Rule-to-regulation mappings must be reviewed "
        "by your legal/compliance team."
    )
