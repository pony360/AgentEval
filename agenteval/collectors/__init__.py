# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Collectors: fetch raw configuration snapshots from the target environment and normalize them into assets."""

from __future__ import annotations

import json
from pathlib import Path

from agenteval.models import Asset

from .normalize import normalize_snapshot


def collect_from_snapshot_file(path: str | Path) -> tuple[list[Asset], dict]:
    """Offline mode: read JSON exported by `agenteval snapshot` (or a hand-built test snapshot)."""
    with Path(path).open(encoding="utf-8") as fh:
        snapshot = json.load(fh)
    return normalize_snapshot(snapshot), snapshot


def collect_from_kubernetes(context: str | None = None, namespaces: list[str] | None = None,
                            in_cluster: bool = False) -> tuple[list[Asset], dict]:
    from .kubernetes import fetch_snapshot  # Lazy import: offline mode still works without the kubernetes dependency

    snapshot = fetch_snapshot(context=context, namespaces=namespaces, in_cluster=in_cluster)
    return normalize_snapshot(snapshot), snapshot


__all__ = ["normalize_snapshot", "collect_from_snapshot_file", "collect_from_kubernetes"]
