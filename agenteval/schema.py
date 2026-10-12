# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""JSON Schema validation for AgentEval's YAML inputs.

Schemas live in ``agenteval/schemas/*.schema.json`` (Draft 2020-12) and are
exported for editors with ``agenteval schemas --out schemas/``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_NAMES = ("rule", "pack", "translations", "attestations", "exam", "system", "policy")


@lru_cache(maxsize=None)
def schema(name: str) -> dict:
    text = resources.files("agenteval").joinpath(f"schemas/{name}.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


@lru_cache(maxsize=None)
def validator(name: str) -> Draft202012Validator:
    """``rule`` validates one rule; ``rule-file`` validates a whole rules file."""
    if name == "rule":
        s = schema("rule")
        return Draft202012Validator({"$schema": s["$schema"], "$defs": s["$defs"], "$ref": "#/$defs/rule"})
    return Draft202012Validator(schema("rule" if name == "rule-file" else name))


def _plain(v: Any) -> Any:
    """YAML turns unquoted dates into date objects; validate them as ISO strings."""
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def errors(name: str, data: Any, where: str = "") -> list[str]:
    """Return human-readable validation errors (empty list when valid)."""
    out = []
    for e in sorted(validator(name).iter_errors(_plain(data)), key=lambda e: list(e.path)):
        path = "/".join(str(p) for p in e.path) or "<root>"
        out.append(f"{where} {path}: {e.message}".strip())
    return out


class SchemaError(ValueError):
    def __init__(self, errs: list[str]):
        self.errors = errs
        super().__init__("Schema validation failed:\n  " + "\n  ".join(errs))


def check(name: str, data: Any, where: str = "") -> None:
    errs = errors(name, data, where)
    if errs:
        raise SchemaError(errs)
