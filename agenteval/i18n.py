# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Multilingual report support (zh / en).

- Engine-generated messages are stored as message_code + message_params and formatted per language at render time;
- rule titles, descriptions, remediation and source references come from rules/**/i18n/<lang>.yaml;
- missing translations fall back to the source (English) text.
"""

from __future__ import annotations

from typing import Any

from agenteval.models import ScanResult

LANGS = ("zh", "en")
DEFAULT_LANG = "en"  # source language of rules and engine messages

MESSAGES: dict[str, dict[str, str]] = {
    "ok": {"zh": "符合要求", "en": "Compliant"},
    "check_failed": {"zh": "不符合: {title}", "en": "Non-compliant: {title}"},
    "not_applicable": {"zh": "前置条件不满足，规则不适用", "en": "Precondition not met; rule not applicable"},
    "att_missing": {"zh": "等待人工证明，需提供: {required}",
                    "en": "Awaiting manual attestation; required evidence: {required}"},
    "att_incomplete": {"zh": "证明材料不完整，缺少: {missing}",
                       "en": "Attestation incomplete; missing: {missing}"},
    "att_expired": {"zh": "证明已过期（{age} 天 > {max_age} 天），需重新评估",
                    "en": "Attestation expired ({age} days > {max_age} days); re-assessment required"},
    "att_no_date": {"zh": "证明缺少 attested_at 日期，无法判断有效期",
                    "en": "Attestation has no attested_at date; validity cannot be determined"},
    "att_ok": {"zh": "已提供有效的人工证明", "en": "Valid manual attestation provided"},
    "eval_error": {"zh": "规则求值错误: {error}", "en": "Rule evaluation error: {error}"},
}

DISCLAIMER = {
    "zh": "本报告为基于证据的技术控制项评估，不构成法律意见或合规认定。规则与法规的映射需经法务/合规团队审核。",
    "en": ("This report is an evidence-based assessment of technical controls and does not constitute legal "
           "advice or a determination of regulatory compliance. Rule-to-regulation mappings must be reviewed "
           "by your legal/compliance team."),
}

UI: dict[str, dict[str, str]] = {
    "zh": {
        "html_lang": "zh-CN", "title": "AI 合规扫描报告", "scan_id": "扫描 ID", "source": "来源",
        "rules_version": "规则集", "score": "自动项通过率", "assets": "资产", "failed": "不通过",
        "manual": "待人工证明", "passed": "通过", "col_status": "状态", "col_severity": "严重度",
        "col_rule": "规则", "col_asset": "资产", "col_detail": "说明与整改",
        "remediation": "整改建议与依据", "evidence": "证据", "unverified": "（映射待审核）",
        "status_fail": "不通过", "status_pass": "通过", "status_manual": "待证明",
        "status_not_applicable": "不适用", "status_error": "错误",
        "sev_critical": "严重", "sev_high": "高", "sev_medium": "中", "sev_low": "低", "sev_info": "信息",
        "list_sep": "、",
    },
    "en": {
        "html_lang": "en", "title": "AI Compliance Scan Report", "scan_id": "Scan ID", "source": "Source",
        "rules_version": "Rule set", "score": "Automated pass rate", "assets": "Assets", "failed": "Failed",
        "manual": "Awaiting attestation", "passed": "Passed", "col_status": "Status", "col_severity": "Severity",
        "col_rule": "Rule", "col_asset": "Asset", "col_detail": "Details & remediation",
        "remediation": "Remediation & basis", "evidence": "Evidence", "unverified": " (mapping pending review)",
        "status_fail": "Fail", "status_pass": "Pass", "status_manual": "Pending",
        "status_not_applicable": "N/A", "status_error": "Error",
        "sev_critical": "Critical", "sev_high": "High", "sev_medium": "Medium", "sev_low": "Low", "sev_info": "Info",
        "list_sep": ", ",
    },
}


def check_lang(lang: str) -> str:
    if lang not in LANGS:
        raise ValueError(f"Unsupported language: {lang} (choose from: {', '.join(LANGS)})")
    return lang


def format_message(code: str, params: dict[str, Any], lang: str, title: str = "") -> str:
    tpl = MESSAGES.get(code, {}).get(lang) or MESSAGES.get(code, {}).get(DEFAULT_LANG)
    if not tpl:
        return ""
    sep = UI[lang]["list_sep"]
    p = {k: (sep.join(map(str, v)) if isinstance(v, list) else v) for k, v in params.items()}
    return tpl.format(title=title, **p)


def localize_rule(rule_dict: dict, translations: dict, lang: str) -> dict:
    """Replace a rule's (model_dump dict) title, description, remediation and sources with the target language."""
    check_lang(lang)
    if lang == DEFAULT_LANG:
        return rule_dict
    tr = translations.get(lang) or {}
    out = dict(rule_dict)
    for k in ("title", "description", "remediation"):
        if tr.get(k):
            out[k] = tr[k]
    refs = tr.get("sources") or []
    out["sources"] = [{**s, "reference": refs[i]} if i < len(refs) and refs[i] else s
                      for i, s in enumerate(rule_dict.get("sources", []))]
    return out


def localize_result(result: ScanResult, lang: str) -> ScanResult:
    """Return a copy with fields replaced by the target language (the original is not modified)."""
    check_lang(lang)
    if lang == DEFAULT_LANG:
        return result
    out = result.model_copy(deep=True)
    out.disclaimer = DISCLAIMER[lang]
    for f in out.findings:
        tr = f.translations.get(lang) or {}
        if tr.get("title"):
            f.rule_title = tr["title"]
        if tr.get("remediation"):
            f.remediation = tr["remediation"]
        refs = tr.get("sources") or []
        for i, s in enumerate(f.sources):
            if i < len(refs) and refs[i]:
                s.reference = refs[i]
        if f.message_code:
            f.message = format_message(f.message_code, f.message_params, lang, f.rule_title)
    return out
