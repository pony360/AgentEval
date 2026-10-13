# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Normalize A2A (Agent2Agent) Agent Cards into `a2a_agent` assets.

Supports both the v0.3 card shape (top-level `url`, `preferredTransport`,
`additionalInterfaces`, `security`) and the v1.0 shape (`supportedInterfaces[]`
with `protocolBinding` / `protocolVersion`, `capabilities.extendedAgentCard`).
Only metadata is recorded; nothing is sent to the agent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from agenteval.models import Asset

WELL_KNOWN = "/.well-known/agent-card.json"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# v1.0 proto-JSON wraps each scheme in a oneof; map wrapper names to the classic `type` values.
_SCHEME_WRAPPERS = {
    "apiKeySecurityScheme": "apiKey",
    "httpAuthSecurityScheme": "http",
    "oauth2SecurityScheme": "oauth2",
    "openIdConnectSecurityScheme": "openIdConnect",
    "mtlsSecurityScheme": "mutualTLS",
}


class CardError(ValueError):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "agent"


def _is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


def card_url_for(target: str) -> str:
    """Accept a card URL or an agent base URL; return the URL to fetch."""
    if target.endswith(".json"):
        return target
    return target.rstrip("/") + WELL_KNOWN


def fetch_card(target: str, client: httpx.Client | None = None, timeout: float = 15.0,
               headers: dict[str, str] | None = None) -> tuple[dict, str]:
    """Load a card from a local file or over HTTPS (plain HTTP only for localhost)."""
    p = Path(target)
    if not target.startswith(("http://", "https://")):
        if not p.exists():
            raise CardError(f"Agent Card not found: {target}")
        return json.loads(p.read_text(encoding="utf-8")), str(p)
    url = card_url_for(target)
    if url.startswith("http://") and not _is_local(url):
        raise CardError(f"Refusing to fetch an Agent Card over plain HTTP: {url}")
    own = client is None
    client = client or httpx.Client(follow_redirects=False)
    try:
        r = client.get(url, headers=headers or {}, timeout=timeout)
        r.raise_for_status()
        return r.json(), url
    except (httpx.HTTPError, ValueError) as e:
        raise CardError(f"Could not fetch Agent Card {url}: {e}") from e
    finally:
        if own:
            client.close()


def interfaces(card: dict) -> list[dict[str, str]]:
    """All declared endpoints as {url, binding, version}, newest card shape first."""
    out: list[dict[str, str]] = []
    for i in card.get("supportedInterfaces") or []:
        if isinstance(i, dict) and i.get("url"):
            out.append({"url": i["url"], "binding": str(i.get("protocolBinding") or "JSONRPC").upper(),
                        "version": str(i.get("protocolVersion") or "1.0")})
    if card.get("url"):
        out.append({"url": card["url"], "binding": str(card.get("preferredTransport") or "JSONRPC").upper(),
                    "version": str(card.get("protocolVersion") or "0.3")})
    for i in card.get("additionalInterfaces") or []:
        if isinstance(i, dict) and i.get("url"):
            out.append({"url": i["url"], "binding": str(i.get("transport") or "JSONRPC").upper(),
                        "version": str(card.get("protocolVersion") or "0.3")})
    seen, uniq = set(), []
    for i in out:
        key = (i["url"], i["binding"], i["version"])
        if key not in seen:
            seen.add(key)
            uniq.append(i)
    return uniq


def _schemes(card: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, raw in (card.get("securitySchemes") or {}).items():
        if not isinstance(raw, dict):
            continue
        if "type" in raw:
            out[name] = raw
            continue
        for wrapper, typ in _SCHEME_WRAPPERS.items():
            if isinstance(raw.get(wrapper), dict):
                out[name] = {"type": typ, **raw[wrapper]}
                break
    return out


def _requirements(card: dict) -> list | None:
    for key in ("security", "securityRequirements"):
        if key in card:
            return card.get(key) or []
    return None


def normalize_card(card: dict, source: str = "") -> Asset:
    if not isinstance(card, dict) or not card.get("name"):
        raise CardError(f"Not an A2A Agent Card (missing name): {source}")
    ifaces = interfaces(card)
    urls = [i["url"] for i in ifaces]
    schemes = _schemes(card)
    types = sorted({str(s.get("type")) for s in schemes.values()})
    reqs = _requirements(card)
    flows = {f for s in schemes.values() if s.get("type") == "oauth2" for f in (s.get("flows") or {})}
    api_key_in_query = any(s.get("type") == "apiKey" and str(s.get("in", s.get("location", ""))).lower() == "query"
                           for s in schemes.values())
    query_secrets = [u for u in urls if any(k.lower() in ("key", "api_key", "apikey", "token", "access_token")
                                            for k in parse_qs(urlparse(u).query))]
    caps = card.get("capabilities") or {}
    provider = card.get("provider") or {}
    host = (urlparse(urls[0]).hostname if urls else None) or "local"
    facts: dict[str, Any] = {
        "name": card.get("name"),
        "version": card.get("version"),
        "protocol_versions": sorted({i["version"] for i in ifaces}),
        "bindings": sorted({i["binding"] for i in ifaces}),
        "endpoint": {
            "urls": urls,
            "all_https": bool(urls) and all(u.startswith("https://") or _is_local(u) for u in urls),
            "any_plain_http": any(u.startswith("http://") and not _is_local(u) for u in urls),
            "secrets_in_url": bool(query_secrets),
        },
        "auth": {
            "schemes": types,
            "declared": bool(schemes),
            # No explicit requirement list: treat declared schemes as required (common in practice).
            "required": bool(reqs) if reqs is not None else bool(schemes),
            "api_key_in_query": api_key_in_query,
            "mutual_tls": "mutualTLS" in types,
        },
        "oauth": {"flows": sorted(flows), "deprecated_flows": bool(flows & {"implicit", "password"})},
        "card": {"signed": bool(card.get("signatures")), "source": source},
        "capabilities": {
            "streaming": bool(caps.get("streaming")),
            "push_notifications": bool(caps.get("pushNotifications")),
            "extended_card": bool(caps.get("extendedAgentCard") or card.get("supportsAuthenticatedExtendedCard")),
        },
        "provider": {"organization": provider.get("organization"), "url": provider.get("url")},
        "skills": {"count": len(card.get("skills") or []),
                   "ids": [s.get("id") for s in card.get("skills") or [] if isinstance(s, dict)]},
        "documentation_url": card.get("documentationUrl"),
    }
    return Asset(id=f"a2a/{host}/{_slug(card['name'])}", type="a2a_agent", name=card["name"],
                 namespace=None, facts=facts)


def collect_a2a_cards(targets: list[str], client: httpx.Client | None = None) -> list[Asset]:
    return [normalize_card(*fetch_card(t, client=client)) for t in targets]
