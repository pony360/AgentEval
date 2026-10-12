# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Rule loading and validation: YAML → JSON Schema validation → Pydantic models."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from agenteval.models import Rule, RulePack
from agenteval.schema import errors as schema_errors


class RuleLoadError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Failed to load rules:\n  " + "\n  ".join(errors))


def _iter_rule_docs(path: Path):
    files = sorted(path.rglob("*.y*ml")) if path.is_dir() else [path]
    for f in files:
        if f.suffix not in (".yaml", ".yml") or "i18n" in f.parts or f.name == "pack.yaml":
            continue
        with f.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if data is None:
            continue
        # A file may hold a single rule or {"rules": [...]}
        docs = data.get("rules", [data]) if isinstance(data, dict) else data
        for i, doc in enumerate(docs):
            yield f, i, doc


def load_rules(path: str | Path, include_disabled: bool = False) -> list[Rule]:
    path = Path(path)
    if not path.exists():
        raise RuleLoadError([f"Rule path does not exist: {path}"])

    errors: list[str] = []
    rules: list[Rule] = []
    seen: dict[str, str] = {}

    for f, i, doc in _iter_rule_docs(path):
        loc = f"{f.name}[{i}]"
        errs = schema_errors("rule", doc, loc)
        if errs:
            errors.extend(errs)
            continue
        rule = Rule.model_validate(doc)
        rule.pack = _pack_for(path, f)
        if rule.id in seen:
            errors.append(f"{loc}: duplicate rule ID {rule.id} (first seen in {seen[rule.id]})")
            continue
        seen[rule.id] = loc
        if rule.enabled or include_disabled:
            rules.append(rule)

    if not errors:
        errors.extend(_attach_translations(path, rules))
    if errors:
        raise RuleLoadError(errors)
    return sorted(rules, key=lambda r: r.id)


def _pack_for(root: Path, f: Path) -> str:
    """Rule pack a rule belongs to: the nearest pack.yaml found walking up from the file's directory."""
    if not root.is_dir():
        return ""
    d = f.parent
    while True:
        meta = d / "pack.yaml"
        if meta.exists():
            with meta.open(encoding="utf-8") as fh:
                return str((yaml.safe_load(fh) or {}).get("id", d.name))
        if d == root or root not in d.parents:
            return "default"
        d = d.parent


def load_packs(path: str | Path) -> dict[str, RulePack]:
    path = Path(path)
    packs: dict[str, RulePack] = {}
    if path.is_dir():
        for meta in sorted(path.rglob("pack.yaml")):
            with meta.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            errs = schema_errors("pack", data, str(meta))
            if errs:
                raise RuleLoadError(errs)
            p = RulePack.model_validate(data)
            if p.id in packs:
                raise RuleLoadError([f"{meta}: duplicate pack id {p.id}"])
            packs[p.id] = p
    return packs


def _attach_translations(path: Path, rules: list[Rule]) -> list[str]:
    """Read <rules_dir>/**/i18n/<lang>.yaml and attach translations by rule ID."""
    errors: list[str] = []
    if not path.is_dir():
        return errors
    by_id = {r.id: r for r in rules}
    for f in sorted(path.rglob("i18n/*.y*ml")):
        lang = f.stem
        with f.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        errs = schema_errors("translations", data, f.name)
        if errs:
            errors.extend(errs)
            continue
        for rid, tr in data.items():
            rule = by_id.get(rid)
            if rule is None:
                continue  # Ignore rules that are disabled or belong to another pack
            refs = tr.get("sources") or []
            if len(refs) > len(rule.sources):
                errors.append(f"{f.name}: {rid} has more translated sources than the original rule")
                continue
            rule.translations[lang] = tr
    return errors


def rules_version(rules: list[Rule]) -> str:
    """Rule set fingerprint, used in reports to trace which rule version a scan used."""
    h = hashlib.sha256()
    for r in sorted(rules, key=lambda r: r.id):
        h.update(f"{r.id}@{r.version}".encode())
    return h.hexdigest()[:12]
