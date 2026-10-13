# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Normalize MCP client configuration files into `mcp_server` assets.

Reads the common JSON shapes: `{"mcpServers": {...}}` (Claude Desktop, Cursor,
Claude Code `.mcp.json`) and `{"servers": {...}}` (VS Code `mcp.json`). Each server
entry is either a local stdio command (`command`, `args`, `env`) or a remote server
(`url`, optional `type: http|sse|streamable-http`, `headers`).

Security note: only the *names* of environment variables and headers that hold
literal secrets are recorded, never their values.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agenteval.models import Asset

SECRET_NAME = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL|AUTH)", re.I)
# Values that reference something else instead of holding the secret itself.
REFERENCE = re.compile(r"^\s*(\$\{[^}]+\}|\$[A-Z_][A-Z0-9_]*|\{\{[^}]+\}\}|<[^>]+>)\s*$")
AUTH_HEADERS = {"authorization", "x-api-key", "api-key", "x-auth-token", "proxy-authorization"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
RUNNERS = {"npx": "npx", "bunx": "npx", "pnpm": "npx", "uvx": "uvx", "uv": "uvx", "pipx": "pipx",
           "docker": "docker", "podman": "docker", "node": "node", "python": "python", "python3": "python",
           "deno": "deno"}
SHELLS = {"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh"}
DOWNLOAD_EXEC = re.compile(r"(curl|wget|iwr|invoke-webrequest)[^|;&]*\|\s*(sudo\s+)?(sh|bash|zsh|python3?|iex)\b", re.I)
VERSIONED_NPM = re.compile(r"^(@[^/]+/)?[^@/\s]+@\d[\w.\-+]*$")
VERSIONED_PY = re.compile(r"^[A-Za-z0-9_.\-\[\]]+(==|@)\d[\w.\-+]*$")


class MCPConfigError(ValueError):
    pass


def _servers(doc: Any) -> dict[str, dict]:
    if not isinstance(doc, dict):
        raise MCPConfigError("MCP config must be a JSON object")
    servers = doc.get("mcpServers", doc.get("servers"))
    if not isinstance(servers, dict):
        raise MCPConfigError('MCP config has no "mcpServers" or "servers" object')
    return {k: v for k, v in servers.items() if isinstance(v, dict)}


def _literal_secrets(mapping: dict[str, Any] | None, prefix: str) -> list[str]:
    out = []
    for k, v in (mapping or {}).items():
        if isinstance(v, str) and v and not REFERENCE.match(v):
            if SECRET_NAME.search(k) or (prefix == "header" and k.lower() in AUTH_HEADERS):
                if not re.fullmatch(r"(Bearer|Basic|Token)\s+(\$\{[^}]+\}|\$[A-Z_][A-Z0-9_]*)", v.strip()):
                    out.append(f"{prefix}:{k}")
    return out


def _package(runner: str | None, args: list[str]) -> tuple[str | None, bool | None]:
    """Return (package, pinned). pinned is None when the runner does not fetch packages."""
    if runner in ("npx", "uvx", "pipx"):
        skip_next = {"--package", "-p", "--from"}
        pkg, i = None, 0
        while i < len(args):
            a = args[i]
            if a in skip_next and i + 1 < len(args):
                pkg = args[i + 1]
                break
            if a.startswith("-") or a in ("run", "dlx", "exec", "tool"):
                i += 1
                continue
            pkg = a
            break
        if not pkg:
            return None, False
        pinned = bool(VERSIONED_NPM.match(pkg)) if runner == "npx" else bool(VERSIONED_PY.match(pkg))
        return pkg, pinned
    if runner == "docker":
        flags_with_value = {"-e", "--env", "-v", "--volume", "--name", "--network", "--net", "-p", "--publish",
                            "--mount", "-u", "--user", "-w", "--workdir", "--env-file", "--entrypoint"}
        it = iter(range(len(args)))
        image = None
        for i in it:
            a = args[i]
            if a in ("run", "container"):
                continue
            if a in flags_with_value:
                next(it, None)
                continue
            if a.startswith("-"):
                continue
            image = a
            break
        if not image:
            return None, False
        pinned = "@sha256:" in image or (":" in image.rsplit("/", 1)[-1] and not image.endswith(":latest"))
        return image, pinned
    return None, None


def normalize_server(name: str, entry: dict, config_name: str) -> Asset:
    url = entry.get("url") or entry.get("serverUrl")
    typ = str(entry.get("type") or entry.get("transport") or ("http" if url else "stdio")).lower()
    remote = bool(url) and typ != "stdio"
    host = urlparse(url).hostname if url else None
    local = (host or "") in LOCAL_HOSTS
    headers = entry.get("headers") or {}
    env = entry.get("env") or {}
    command = str(entry.get("command") or "")
    args = [str(a) for a in entry.get("args") or []]
    if command and not args and " " in command:
        try:
            parts = shlex.split(command)
            command, args = parts[0], parts[1:]
        except ValueError:
            pass
    exe = Path(command).name.lower()
    runner = RUNNERS.get(exe, "shell" if exe in SHELLS else ("other" if exe else None))
    package, pinned = _package(runner, args)
    joined = " ".join([command, *args])
    facts: dict[str, Any] = {
        "transport": "stdio" if not remote else ("sse" if typ == "sse" else "http"),
        "remote": remote,
        "localhost": local,
        "url": url,
        "tls": {"enabled": bool(url) and (url.startswith("https://") or local)},
        "auth": {
            "header_present": any(k.lower() in AUTH_HEADERS for k in headers),
            "oauth_declared": isinstance(entry.get("oauth"), dict) or str(entry.get("auth", "")).lower() == "oauth",
        },
        "secrets": {"plaintext": sorted(_literal_secrets(env, "env") + _literal_secrets(headers, "header"))},
        "env": {"names": sorted(env)},
        "command": {
            "runner": runner,
            "download_exec": bool(DOWNLOAD_EXEC.search(joined)),
            "shell": runner == "shell",
        },
        "package": {"name": package, "pinned": pinned, "fetches_remote_code": runner in ("npx", "uvx", "pipx", "docker")},
        "docker": {
            "privileged": runner == "docker" and "--privileged" in args,
            "host_network": runner == "docker" and any(a in ("--network=host", "--net=host") for a in args)
            or (runner == "docker" and any(args[i] in ("--network", "--net") and i + 1 < len(args) and args[i + 1] == "host"
                                           for i in range(len(args)))),
            "host_root_mount": runner == "docker" and any(re.match(r"^/:", a) or a.startswith("source=/,")
                                                          for a in args),
        },
        "disabled": bool(entry.get("disabled")),
    }
    facts["auth"]["declared"] = facts["auth"]["header_present"] or facts["auth"]["oauth_declared"]
    return Asset(id=f"mcp/{config_name}/{name}", type="mcp_server", name=name, namespace=None, facts=facts)


def collect_mcp_configs(paths: list[str | Path]) -> list[Asset]:
    assets: list[Asset] = []
    for p in paths:
        p = Path(p)
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise MCPConfigError(f"Cannot read MCP config {p}: {e}") from e
        cfg = re.sub(r"[^a-z0-9.\-]+", "-", p.stem.lower()) or "mcp"
        for name, entry in sorted(_servers(doc).items()):
            asset = normalize_server(name, entry, cfg)
            if not asset.facts["disabled"]:
                assets.append(asset)
    return assets
