# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""AI system certification: automatically issue a digital certificate when both security (scan) and quality (evaluation) meet the policy gates."""

from .assess import assess_quality, assess_security, decide
from .issuer import (
    build_certificate, certificate_state, gate_text, issue, load_policy, load_system_file, load_systems,
)
from .models import AISystem, CertPolicy, Decision
from .signing import default_key_path, key_id, load_or_create_key, load_public_key, public_pem, sign, verify

__all__ = [
    "assess_quality", "assess_security", "decide", "build_certificate", "certificate_state", "gate_text",
    "issue", "load_policy", "load_system_file", "load_systems", "AISystem", "CertPolicy", "Decision",
    "default_key_path", "key_id", "load_or_create_key", "load_public_key", "public_pem", "sign", "verify",
]
