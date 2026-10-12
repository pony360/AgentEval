# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Runtime configuration and shared loaders (used by both the CLI and the API)."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from agenteval.schema import check

REPO_ROOT = Path(__file__).resolve().parent.parent


def default_rules_dir() -> Path:
    env = os.environ.get("AGENTEVAL_RULES_DIR")
    if env:
        return Path(env)
    cwd_rules = Path.cwd() / "rules"
    return cwd_rules if cwd_rules.exists() else REPO_ROOT / "rules"


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
