# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Certificate generation, registry loading and gate description text."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agenteval.schema import check

from agenteval.config import REPO_ROOT

from .models import AISystem, CertPolicy, Decision
from .signing import sign

CERT_TYPE = "AISystemAssuranceCertificate"
CERT_VERSION = "1.0"

GATE_TEXT = {
    "sec_asset_scanned": {"zh": "扫描覆盖该系统资产 {asset}", "en": "Scan covers the system asset {asset}"},
    "sec_packs_assessed": {"zh": "已评估登记的全部规则包", "en": "All registered rule packs were assessed"},
    "sec_score": {"zh": "安全合规得分 {score}% > {threshold}%", "en": "Security compliance score {score}% > {threshold}%"},
    "sec_no_blocking": {"zh": "无 {severities} 级未修复项", "en": "No open {severities} findings"},
    "sec_no_pending": {"zh": "无待人工证明项", "en": "No pending attestations"},
    "sec_fresh": {"zh": "扫描证据在 {max} 天内（当前 {age} 天）", "en": "Scan evidence within {max} days (now {age} days)"},
    "q_run": {"zh": "存在 {agent} × {exam} 的评测记录", "en": "Evaluation exists for {agent} × {exam}"},
    "q_score": {"zh": "评测综合得分 {score} > {threshold}", "en": "Evaluation score {score} > {threshold}"},
    "q_no_critical": {"zh": "评测无关键项失败（当前 {n}）", "en": "No critical evaluation failures (now {n})"},
    "q_no_errors": {"zh": "评测无会话错误（当前 {n}）", "en": "No evaluation session errors (now {n})"},
    "q_fresh": {"zh": "评测证据在 {max} 天内（当前 {age} 天）", "en": "Evaluation evidence within {max} days (now {age} days)"},
    "q_llm_judge": {"zh": "使用 LLM 裁判（当前 {judge}；否则仅颁发 DEMO 证书）",
                    "en": "LLM judge used (now {judge}; otherwise a DEMO certificate only)"},
}


def gate_text(code: str, params: dict[str, Any], lang: str = "zh") -> str:
    tpl = GATE_TEXT.get(code, {}).get(lang) or code
    p = {k: ("—" if v is None else ", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in params.items()}
    try:
        return tpl.format(**p)
    except (KeyError, IndexError):
        return tpl


# ---------------- Registry ----------------

def certification_dir() -> Path:
    env = os.environ.get("AGENTEVAL_CERT_DIR")
    if env:
        return Path(env)
    cwd = Path.cwd() / "certification"
    return cwd if cwd.exists() else REPO_ROOT / "certification"


def load_policy(directory: Path | None = None) -> CertPolicy:
    p = Path(directory or certification_dir()) / "policy.yaml"
    if not p.exists():
        return CertPolicy()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    check("policy", data, str(p))
    return CertPolicy.model_validate(data)


def load_systems(directory: Path | None = None) -> dict[str, AISystem]:
    d = Path(directory or certification_dir()) / "systems"
    out: dict[str, AISystem] = {}
    for f in sorted(d.glob("*.y*ml")) if d.exists() else []:
        s = load_system_file(f)
        if s.id in out:
            raise ValueError(f"{f.name}: duplicate system ID {s.id}")
        out[s.id] = s
    return out


def load_system_file(path: Path) -> AISystem:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    check("system", data, str(path))
    return AISystem.model_validate(data)


# ---------------- Certificates ----------------

def add_months(dt: datetime, months: int) -> datetime:
    m = dt.month - 1 + months
    y, m = dt.year + m // 12, m % 12 + 1
    days = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return dt.replace(year=y, month=m, day=min(dt.day, days[m - 1]))


def new_cert_id(now: datetime) -> str:
    return f"AEV-{now:%Y%m}-{secrets.token_hex(4).upper()}"


def build_certificate(system: AISystem, decision: Decision, policy: CertPolicy, verify_url_template: str | None,
                      now: datetime | None = None) -> dict[str, Any]:
    if not decision.eligible:
        raise ValueError("Not eligible for certification")
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    cid = new_cert_id(now)
    sec, q = decision.security, decision.quality
    payload = {
        "type": CERT_TYPE,
        "version": CERT_VERSION,
        "id": cid,
        "demo": decision.demo,
        "issuer": {"name": policy.issuer_name, "name_zh": policy.issuer_name_zh},
        "subject": {
            "system_id": system.id, "name": system.name, "name_zh": system.name_zh,
            "organization": system.organization, "industry": system.industry,
            "description": system.description, "description_zh": system.description_zh,
            "asset_id": system.asset_id, "model": system.model, "agent_name": system.agent_name,
        },
        "assurance": {
            "security": {
                "score": sec.score, "threshold": policy.min_security_score,
                "scan_id": sec.scan_id, "rules_version": sec.rules_version,
                "scanned_at": sec.scanned_at.isoformat() if sec.scanned_at else None,
                "checks_passed": sec.passed, "checks_failed": sec.failed,
                "packs": [p.model_dump() for p in sec.packs],
            },
            "quality": {
                "score": q.score, "threshold": policy.min_quality_score, "grade": q.grade,
                "run_id": q.run_id, "exam": q.exam, "exam_version": q.exam_version, "judge": q.judge,
                "evaluated_at": q.evaluated_at.isoformat() if q.evaluated_at else None,
                "cases_passed": q.cases_passed, "cases": q.cases, "critical_failures": q.critical_failures,
                "dimensions": q.dimensions,
            },
        },
        "criteria": {
            "security_score_gt": policy.min_security_score, "quality_score_gt": policy.min_quality_score,
            "no_open_severities": policy.block_on_severities,
            "no_pending_attestations": policy.require_no_pending_attestations,
            "zero_critical_eval_failures": policy.require_zero_critical_eval_failures,
            "llm_judge_required": policy.require_llm_judge,
        },
        "issued_at": now.isoformat(),
        "expires_at": add_months(now, policy.validity_months).isoformat(),
        # Optional public verification page, e.g. "https://trust.example.com/certs/{id}"; None = offline only
        "verify_url": verify_url_template.format(id=cid) if verify_url_template else None,
        "disclaimer": (
            "Issued by the AgentEval toolkit from automated technical controls, submitted attestations and a "
            "standardized agent evaluation. It is not a government certification, a legal opinion, or a PCI DSS "
            "validation by a Qualified Security Assessor. Rule-to-regulation mappings are pending legal review."
        ),
        "disclaimer_zh": (
            "本证书由 AgentEval 工具依据自动化技术控制检查、提交的证明材料与标准化 Agent 评测签发，"
            "不是政府认证、法律意见，也不等同于 PCI DSS 合格评估师（QSA）的合规验证；规则与法规的映射尚待法务审核。"
        ),
    }
    return payload


def issue(system: AISystem, decision: Decision, policy: CertPolicy, key, verify_url_template: str | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    return sign(build_certificate(system, decision, policy, verify_url_template, now), key)


def certificate_state(envelope: dict, stored_status: str | None = None, now: datetime | None = None) -> str:
    """Current certificate state: revoked/superseded per the registry; otherwise based on the validity period."""
    if stored_status in ("revoked", "superseded"):
        return stored_status
    now = now or datetime.now(timezone.utc)
    exp = datetime.fromisoformat(envelope["certificate"]["expires_at"])
    return "expired" if now > exp else "active"
