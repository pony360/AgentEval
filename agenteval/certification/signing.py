# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Ed25519 signing and verification.

In production, keep the private key in a KMS/HSM and point AGENTEVAL_SIGNING_KEY at the mounted PEM file.
In development, if no key exists, a local key is generated under ./.agenteval/ (mode 0600) for demo use only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ALG = "Ed25519"


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def default_key_path() -> Path:
    return Path(os.environ.get("AGENTEVAL_SIGNING_KEY", Path.cwd() / ".agenteval" / "signing-key.pem"))


def generate_key(path: Path) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    return key


def load_or_create_key(path: Path | None = None) -> Ed25519PrivateKey:
    path = Path(path or default_key_path())
    if not path.exists():
        return generate_key(path)
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"{path} is not an Ed25519 private key")
    return key


def public_pem(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def key_id(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def load_public_key(pem: str | bytes) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(pem.encode() if isinstance(pem, str) else pem)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Not an Ed25519 public key")
    return key


def sign(payload: dict[str, Any], key: Ed25519PrivateKey) -> dict[str, Any]:
    sig = key.sign(canonical(payload))
    return {"certificate": payload,
            "signature": {"alg": ALG, "key_id": key_id(key), "value": base64.b64encode(sig).decode()}}


def verify(envelope: dict[str, Any], public_key: Ed25519PublicKey) -> bool:
    try:
        s = envelope["signature"]
        if s.get("alg") != ALG or s.get("key_id") != key_id(public_key):
            return False
        public_key.verify(base64.b64decode(s["value"]), canonical(envelope["certificate"]))
        return True
    except (KeyError, TypeError, ValueError, InvalidSignature):
        return False
