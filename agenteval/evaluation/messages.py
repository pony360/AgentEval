# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Evaluation runtime message catalog (zh/en).

Messages that reach reports (assertion details, session errors, judge failure reasons) are stored as code + params and
formatted per language at render time, so English reports contain no Chinese and Chinese reports contain no English prompts.
"""

from __future__ import annotations

from typing import Any

MESSAGES: dict[str, dict[str, str]] = {
    # assertion details
    "matched": {"zh": "命中: {items}", "en": "Matched: {items}"},
    "missing": {"zh": "缺少: {items}", "en": "Missing: {items}"},
    "forbidden": {"zh": "出现禁止内容: {items}", "en": "Forbidden content found: {items}"},
    "regex_hit": {"zh": "匹配到: {text}", "en": "Matched: {text}"},
    "tool_calls": {"zh": "实际调用: {names}", "en": "Actual calls: {names}"},
    "no_tool_calls": {"zh": "实际调用: 无", "en": "Actual calls: none"},
    "latency": {"zh": "{ms} 毫秒", "en": "{ms} ms"},
    "chars": {"zh": "{n} 字符", "en": "{n} chars"},
    "not_executed": {"zh": "未执行：会话出错", "en": "Not executed: session error"},
    # session / adapter errors
    "env_missing": {"zh": "环境变量未设置: {name}", "en": "Environment variable not set: {name}"},
    "agent_call_failed": {"zh": "调用被测 Agent 失败: {error}", "en": "Call to agent under test failed: {error}"},
    "text_path_missing": {"zh": "响应中未找到文本字段 {path}", "en": "Text field not found in response: {path}"},
    "config_section_missing": {"zh": "{path}: adapter={adapter} 但缺少同名配置段",
                               "en": "{path}: adapter={adapter} but the matching config section is missing"},
    # judges
    "judge_failed": {"zh": "裁判调用失败: {error}", "en": "Judge call failed: {error}"},
    "judge_bad_response": {"zh": "裁判响应格式错误: {error}", "en": "Malformed judge response: {error}"},
    "judge_no_score": {"zh": "裁判未返回有效分数", "en": "Judge returned no valid score"},
    "agent_error_min": {"zh": "Agent 出错，按最低分计", "en": "Agent error; scored minimum"},
    "heuristic_hits": {"zh": "启发式：命中 {hits}/{total} 个参考信号 {items}",
                       "en": "Heuristic: matched {hits}/{total} reference signals {items}"},
    "heuristic_neutral": {"zh": "启发式：未定义参考信号，给中性分", "en": "Heuristic: no signals defined; neutral score"},
}


def fmt(code: str, params: dict[str, Any] | None, lang: str = "zh") -> str:
    tpl = (MESSAGES.get(code) or {}).get(lang) or (MESSAGES.get(code) or {}).get("zh")
    if tpl is None:
        return code
    try:
        return tpl.format(**(params or {}))
    except (KeyError, IndexError):
        return tpl


class CodedError(RuntimeError):
    """Exception carrying a message code: str() is Chinese; reports can re-format it per language."""

    def __init__(self, code: str, **params: Any):
        self.code, self.params = code, {k: str(v) for k, v in params.items()}
        super().__init__(fmt(code, self.params, "zh"))
