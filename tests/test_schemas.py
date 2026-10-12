# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Every shipped YAML file must validate against its JSON Schema."""

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from agenteval.schema import SCHEMA_NAMES, SchemaError, check, errors, schema

ROOT = Path(__file__).resolve().parent.parent


def _files():
    for f in ROOT.glob("rules/**/*.yaml"):
        yield f, "translations" if "i18n" in f.parts else "pack" if f.name == "pack.yaml" else "rule-file"
    for f in ROOT.glob("exams/**/*.yaml"):
        yield f, "exam"
    yield ROOT / "certification/policy.yaml", "policy"
    for f in ROOT.glob("certification/systems/*.yaml"):
        yield f, "system"
    for f in ROOT.glob("examples/attestations*.yaml"):
        yield f, "attestations"


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schemas_are_valid_draft_2020_12(name):
    Draft202012Validator.check_schema(schema(name))


@pytest.mark.parametrize("path,name", list(_files()), ids=lambda x: str(x).split("agenteval/")[-1])
def test_shipped_yaml_validates(path, name):
    assert errors(name, yaml.safe_load(path.read_text(encoding="utf-8")), path.name) == []


def test_modelines_point_at_existing_schemas():
    for path, _ in _files():
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("# yaml-language-server: $schema="), path
        assert (path.parent / first.split("=", 1)[1]).resolve().exists(), path


def test_bad_inputs_are_rejected():
    with pytest.raises(SchemaError):
        check("pack", {"id": "x", "name": "X", "tier": "global", "jurisdiction": "US"})
    with pytest.raises(SchemaError):
        check("attestations", {"attestations": [{"rule_id": "FED-GOV-A01", "attested_by": "a"}]})
    assert errors("rule", {"id": "bad id"})
    assert errors("system", {"id": "s", "name": "n", "organization": "o", "asset_id": "deployment/ns/x",
                             "agent_name": "a", "exam": "e", "rule_packs": []})
