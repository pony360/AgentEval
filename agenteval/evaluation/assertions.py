# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Deterministic assertions: reproducible checks on each agent reply."""

from __future__ import annotations

import re

from .messages import fmt
from .models import AgentReply, Assertion, AssertionResult


def _norm(s: object) -> str:
    return str(s).casefold()


def _args_match(expected: dict, actual: dict) -> bool:
    return all(_norm(actual.get(k, "")) == _norm(v) for k, v in expected.items())


def check(a: Assertion, reply: AgentReply, turn: int) -> AssertionResult:
    text = reply.text or ""
    low = text.casefold()
    names = [c.name for c in reply.tool_calls]
    passed, code, params = False, "", {}

    if a.type == "contains":
        passed = _norm(a.value) in low
    elif a.type == "contains_any":
        hits = [v for v in a.value if _norm(v) in low]
        passed = bool(hits)
        if hits:
            code, params = "matched", {"items": hits}
    elif a.type == "contains_all":
        miss = [v for v in a.value if _norm(v) not in low]
        passed = not miss
        if miss:
            code, params = "missing", {"items": miss}
    elif a.type == "not_contains":
        vals = a.value if isinstance(a.value, list) else [a.value]
        hits = [v for v in vals if _norm(v) in low]
        passed = not hits
        if hits:
            code, params = "forbidden", {"items": hits}
    elif a.type == "regex":
        passed = re.search(str(a.value), text, re.I | re.S) is not None
    elif a.type == "not_regex":
        m = re.search(str(a.value), text, re.I | re.S)
        passed = m is None
        if m:
            code, params = "regex_hit", {"text": repr(m.group(0)[:60])}
    elif a.type == "tool_called":
        matches = [c for c in reply.tool_calls if c.name == a.value]
        if a.args_contains:
            matches = [c for c in matches if _args_match(a.args_contains, c.arguments)]
        passed = bool(matches)
        code, params = ("tool_calls", {"names": names}) if names else ("no_tool_calls", {})
    elif a.type == "tool_not_called":
        passed = a.value not in names
        code, params = ("tool_calls", {"names": names}) if names else ("no_tool_calls", {})
    elif a.type == "max_latency_ms":
        passed = reply.latency_ms <= float(a.value)
        code, params = "latency", {"ms": f"{reply.latency_ms:.0f}"}
    elif a.type == "min_chars":
        passed = len(text) >= int(a.value)
        code, params = "chars", {"n": len(text)}
    elif a.type == "max_chars":
        passed = len(text) <= int(a.value)
        code, params = "chars", {"n": len(text)}

    return AssertionResult(turn=turn, type=a.type, value=a.value, dimension=a.dimension,
                           weight=a.weight, critical=a.critical, passed=passed,
                           detail=fmt(code, params) if code else "", detail_code=code, detail_params=params)
