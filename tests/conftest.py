# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def rules_dir() -> Path:
    return ROOT / "rules"


@pytest.fixture
def sample_snapshot() -> Path:
    return ROOT / "examples" / "snapshots" / "sample-cluster.json"


@pytest.fixture
def sample_attestations() -> Path:
    return ROOT / "examples" / "attestations.yaml"
