# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Runtime configuration and shared loaders."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from agenteval.schema import check

REPO_ROOT = Path(__file__).resolve().parent.parent
# Starter content (rules, exams, certification, examples) copied into the wheel at build time.
STARTER_ROOT = Path(__file__).resolve().parent / "_starter"
STARTER_DIRS = ("rules", "exams", "certification", "examples")


def content_dir(name: str, env_var: str) -> Path:
    """Resolve a content directory: $ENV, then ./<name>, then a source checkout, then the bundled starter copy."""
    env = os.environ.get(env_var)
    if env:
        return Path(env)
    for candidate in (Path.cwd() / name, REPO_ROOT / name, STARTER_ROOT / name):
        if candidate.exists():
            return candidate
    return Path.cwd() / name


def starter_root() -> Path:
    """Directory holding the bundled starter content (the wheel copy, or the source checkout)."""
    return STARTER_ROOT if STARTER_ROOT.exists() else REPO_ROOT


def default_rules_dir() -> Path:
    return content_dir("rules", "AGENTEVAL_RULES_DIR")


def load_attestations(path: str | Path | list | None) -> list[dict]:
    """Read one or more attestation files and merge them."""
    if not path:
        return []
    if isinstance(path, (list, tuple)):
        return [a for p in path for a in load_attestations(p)]
    with Path(path).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    check("attestations", data, str(path))
    return data.get("attestations", []) if isinstance(data, dict) else data
