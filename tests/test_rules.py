# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors

import pytest

from agenteval.engine import RuleLoadError, load_rules
from agenteval.models import RuleType


def test_bundled_rules_valid(rules_dir):
    rules = load_rules(rules_dir)
    assert len(rules) >= 12
    ids = [r.id for r in rules]
    assert len(ids) == len(set(ids))
    for r in rules:
        assert r.sources, r.id
        if r.type == RuleType.automated:
            assert r.check
        else:
            assert r.attestation and r.attestation["evidence_required"]


def test_schema_rejects_bad_rule(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        "id: bad\ntitle: x\nseverity: urgent\ntype: automated\n", encoding="utf-8")
    with pytest.raises(RuleLoadError) as e:
        load_rules(tmp_path)
    assert any("severity" in m or "required" in m for m in e.value.errors)


def test_automated_rule_requires_check(tmp_path):
    (tmp_path / "r.yaml").write_text("""
id: TST-001
title: test rule
description: desc
severity: low
type: automated
category: t
applies_to: {asset_types: [inference_service]}
remediation: fix
sources: [{framework: X, reference: Y}]
""", encoding="utf-8")
    with pytest.raises(RuleLoadError):
        load_rules(tmp_path)


def test_duplicate_ids_rejected(tmp_path, rules_dir):
    src = (rules_dir / "us-federal" / "security.yaml").read_text(encoding="utf-8")
    (tmp_path / "a.yaml").write_text(src, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(src, encoding="utf-8")
    with pytest.raises(RuleLoadError) as e:
        load_rules(tmp_path)
    assert any("duplicate" in m for m in e.value.errors)
