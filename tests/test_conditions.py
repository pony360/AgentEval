# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors

import pytest

from agenteval.engine import ConditionError, evaluate

FACTS = {"a": {"b": 1, "s": "vllm-openai", "l": [], "x": None}, "flag": True, "tags": ["x", "y"]}


@pytest.mark.parametrize("cond,expected", [
    ({"field": "a.b", "op": "eq", "value": 1}, True),
    ({"field": "a.b", "op": "ne", "value": 1}, False),
    ({"field": "a.b", "op": "gt", "value": 0}, True),
    ({"field": "a.s", "op": "regex", "value": "^vllm"}, True),
    ({"field": "tags", "op": "contains", "value": "y"}, True),
    ({"field": "a.b", "op": "in", "value": [1, 2]}, True),
    ({"field": "a.l", "op": "empty"}, True),
    ({"field": "a.missing", "op": "empty"}, True),
    ({"field": "tags", "op": "not_empty"}, True),
    ({"field": "a.x", "op": "exists"}, False),
    ({"field": "a.missing", "op": "not_exists"}, True),
    ({"all": [{"field": "flag", "op": "truthy"}, {"field": "a.b", "op": "eq", "value": 1}]}, True),
    ({"any": [{"field": "flag", "op": "falsy"}, {"field": "a.b", "op": "eq", "value": 1}]}, True),
    ({"not": {"field": "flag", "op": "truthy"}}, False),
])
def test_ops(cond, expected):
    assert evaluate(cond, FACTS) is expected


def test_missing_field_fails_closed():
    # without evidence, eq/ne/gt etc. must never be satisfied
    for op in ("eq", "ne", "gt", "lt", "truthy", "contains", "regex", "not_empty"):
        assert evaluate({"field": "nope.deep", "op": op, "value": 1}, FACTS) is False


def test_type_mismatch_does_not_raise():
    assert evaluate({"field": "a.s", "op": "gt", "value": 3}, FACTS) is False


def test_evidence_collected():
    ev = {}
    evaluate({"all": [{"field": "a.b", "op": "eq", "value": 2}, {"field": "flag", "op": "truthy"}]}, FACTS, ev)
    assert ev == {"a.b": 1, "flag": True}


def test_invalid_condition():
    with pytest.raises(ConditionError):
        evaluate({"bogus": 1}, FACTS)
