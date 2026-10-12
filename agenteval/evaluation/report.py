# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Evaluation report: JSON / HTML (zh/en UI). Paper content is shown as-is in the language it was written in."""

from __future__ import annotations

import json
from importlib import resources

from jinja2 import Environment, select_autoescape

from .messages import fmt
from .models import EvalRun

UI = {
    "zh": {
        "html_lang": "zh-CN", "title": "AI Agent 评测报告", "paper": "试卷", "agent": "被测 Agent",
        "judge": "裁判", "run": "评测 ID", "overall": "综合得分", "grade": "等级", "passed": "通过",
        "not_passed": "未通过", "passing": "及格线", "cases": "样本", "case_pass_rate": "样本通过率",
        "critical": "关键项失败", "latency": "延迟 p50 / p95", "dimensions": "维度得分", "dimension": "维度",
        "weight": "权重", "score": "得分", "items_label": "评分项", "case_results": "样本明细",
        "category": "类别", "difficulty": "难度", "status": "状态", "transcript": "对话记录",
        "assertions": "断言", "rubric": "评分细则", "user": "用户", "agent_role": "Agent", "tools": "工具调用",
        "error": "错误", "na": "无评分项", "case_pass": "通过", "case_fail": "未通过",
        "heuristic_note": "本次使用离线启发式裁判（按参考信号词打分），主观维度仅供冒烟参考；正式评测请使用 LLM 裁判。",
        "disclaimer": "评测结果反映被测 Agent 在本试卷样本上的表现，不代表其在所有真实场景中的表现。",
    },
    "en": {
        "html_lang": "en", "title": "AI Agent Evaluation Report", "paper": "Exam", "agent": "Agent under test",
        "judge": "Judge", "run": "Run ID", "overall": "Overall score", "grade": "Grade", "passed": "Passed",
        "not_passed": "Not passed", "passing": "Passing score", "cases": "Cases", "case_pass_rate": "Case pass rate",
        "critical": "Critical failures", "latency": "Latency p50 / p95", "dimensions": "Dimension scores",
        "dimension": "Dimension", "weight": "Weight", "score": "Score", "items_label": "items",
        "case_results": "Case results", "category": "Category", "difficulty": "Difficulty", "status": "Status",
        "transcript": "Transcript", "assertions": "Assertions", "rubric": "Rubric", "user": "User",
        "agent_role": "Agent", "tools": "Tool calls", "error": "Error", "na": "no scored items",
        "case_pass": "Pass", "case_fail": "Fail",
        "heuristic_note": ("This run used the offline heuristic judge (reference-signal matching). Subjective "
                           "dimensions are a smoke-test signal only; use an LLM judge for formal evaluation."),
        "disclaimer": ("Results reflect the agent's performance on this exam's samples and do not guarantee "
                       "performance in all real-world scenarios."),
    },
}


def to_json(run: EvalRun) -> str:
    return run.model_dump_json(indent=2)


def localize(run: EvalRun, lang: str) -> EvalRun:
    """Re-format system-generated messages (assertion details, errors, heuristic rationales) for a language; returns a copy."""
    if lang not in UI:
        raise ValueError(f"Unsupported language: {lang}")
    out = run.model_copy(deep=True)
    for c in out.cases:
        if c.error_code:
            c.error = fmt(c.error_code, c.error_params, lang)
        for a in c.assertions:
            if a.detail_code:
                a.detail = fmt(a.detail_code, a.detail_params, lang)
        for g in c.rubric:
            if g.rationale_code:
                g.rationale = fmt(g.rationale_code, g.rationale_params, lang)
    return out


def to_html(run: EvalRun, lang: str = "zh") -> str:
    run = localize(run, lang)
    env = Environment(autoescape=select_autoescape(default=True, default_for_string=True))
    env.filters["tojson"] = lambda v: json.dumps(v, ensure_ascii=False, default=str)
    tpl = resources.files("agenteval").joinpath("reports/templates/eval_report.html.j2").read_text(encoding="utf-8")
    return env.from_string(tpl).render(r=run, t=UI[lang])


def render(run: EvalRun, fmt: str = "html", lang: str = "zh") -> str:
    if fmt == "json":
        return to_json(localize(run, lang))
    if fmt == "html":
        return to_html(run, lang)
    raise ValueError(f"Unsupported format: {fmt}")
