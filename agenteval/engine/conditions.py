# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Safe condition expression evaluator (does not use eval).

Syntax:
  {"all": [cond, ...]}   all must hold
  {"any": [cond, ...]}   any must hold
  {"not": cond}          negation
  {"field": "a.b.c", "op": "eq", "value": ...}   leaf condition

Missing fields resolve to MISSING. Except for exists/not_exists/falsy/empty, every leaf condition returns False
for MISSING, i.e. "no evidence means no pass" (fail-closed).
"""

from __future__ import annotations

import re
from typing import Any


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover
        return "<missing>"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()


class ConditionError(ValueError):
    pass


def resolve(facts: dict[str, Any], path: str) -> Any:
    cur: Any = facts
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return MISSING
    return cur


def _cmp(a: Any, b: Any, fn) -> bool:
    try:
        return bool(fn(a, b))
    except TypeError:
        return False


def _leaf(actual: Any, op: str, expected: Any) -> bool:
    if op == "exists":
        return actual is not MISSING and actual is not None
    if op == "not_exists":
        return actual is MISSING or actual is None
    if op == "falsy":
        return not actual
    if op == "empty":
        return actual is MISSING or actual is None or (hasattr(actual, "__len__") and len(actual) == 0)
    if actual is MISSING:
        return False
    if op == "truthy":
        return bool(actual)
    if op == "not_empty":
        return actual is not None and hasattr(actual, "__len__") and len(actual) > 0
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return isinstance(expected, list) and actual in expected
    if op == "not_in":
        return isinstance(expected, list) and actual not in expected
    if op == "contains":
        return isinstance(actual, (list, str, dict)) and expected in actual
    if op == "not_contains":
        return isinstance(actual, (list, str, dict)) and expected not in actual
    if op == "regex":
        return isinstance(actual, str) and re.search(str(expected), actual) is not None
    if op == "gt":
        return _cmp(actual, expected, lambda a, b: a > b)
    if op == "gte":
        return _cmp(actual, expected, lambda a, b: a >= b)
    if op == "lt":
        return _cmp(actual, expected, lambda a, b: a < b)
    if op == "lte":
        return _cmp(actual, expected, lambda a, b: a <= b)
    raise ConditionError(f"Unknown operator: {op}")


def evaluate(cond: dict[str, Any], facts: dict[str, Any], evidence: dict[str, Any] | None = None) -> bool:
    """Evaluate a condition; if evidence is given, record the fields consulted and their actual values."""
    if "all" in cond:
        # No short-circuiting, so that complete evidence is collected
        results = [evaluate(c, facts, evidence) for c in cond["all"]]
        return all(results)
    if "any" in cond:
        results = [evaluate(c, facts, evidence) for c in cond["any"]]
        return any(results)
    if "not" in cond:
        return not evaluate(cond["not"], facts, evidence)
    if "field" in cond and "op" in cond:
        actual = resolve(facts, cond["field"])
        if evidence is not None:
            evidence[cond["field"]] = None if actual is MISSING else actual
        return _leaf(actual, cond["op"], cond.get("value"))
    raise ConditionError(f"Invalid condition: {cond!r}")
