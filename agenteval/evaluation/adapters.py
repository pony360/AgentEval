# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Adapters for the agent under test.

Four built-in kinds:
  openai   OpenAI-compatible /chat/completions (supports tool calls; tool results are filled in from the paper's tool_mocks)
  webhook  generic HTTP endpoint; adapts in-house support APIs via request templates and field-path mapping
  a2a      any Agent2Agent (A2A) agent over JSON-RPC, protocol v0.3 or v1.0, discovered from its Agent Card
  demo     local rule-based bot for demos and offline tests

Secrets are injected only via environment variables (api_key_env / ${ENV_VAR} in headers) and never stored in plain text in config files.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
import yaml
from pydantic import BaseModel, Field

from .messages import CodedError
from .models import AgentReply, Case, ExamPaper, ToolCall


class AdapterError(CodedError):
    pass


# ---------------- Configuration ----------------

class OpenAIConfig(BaseModel):
    base_url: str
    model: str
    api_key_env: str | None = None
    system_prompt: str = ""
    inject_paper_context: bool = True   # Inject the paper's business context into the system prompt (useful for raw models / simple agents)
    send_tools: bool = True
    temperature: float | None = 0.0
    timeout_s: float = 60.0
    max_tool_rounds: int = 3


class WebhookResponseMap(BaseModel):
    text_path: str
    tool_calls_path: str | None = None
    session_id_path: str | None = None


class WebhookConfig(BaseModel):
    url: str
    method: Literal["POST", "PUT"] = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = Field(default_factory=lambda: {"message": "{{message}}", "session_id": "{{session_id}}"})
    response: WebhookResponseMap
    timeout_s: float = 60.0


class A2AConfig(BaseModel):
    agent_card_url: str | None = None      # card URL, or the agent's base URL (/.well-known/agent-card.json is appended)
    url: str | None = None                 # explicit JSON-RPC endpoint; skips discovery
    protocol_version: Literal["auto", "0.3", "1.0"] = "auto"
    headers: dict[str, str] = Field(default_factory=dict)   # ${ENV_VAR} placeholders are expanded
    timeout_s: float = 60.0
    poll_interval_s: float = 1.0
    send_context: bool = False             # prepend the paper's business context to the first message
    tool_calls_key: str = "tool_calls"     # metadata / DataPart key the agent uses to report tool calls


class DemoRule(BaseModel):
    match: str
    reply: str
    tool_calls: list[ToolCall] = Field(default_factory=list)


class DemoConfig(BaseModel):
    rules: list[DemoRule]
    default: str = "Sorry, I can't help with that."
    latency_ms: float = 300.0


class AgentConfig(BaseModel):
    name: str
    description: str = ""
    adapter: Literal["openai", "webhook", "a2a", "demo"]
    openai: OpenAIConfig | None = None
    webhook: WebhookConfig | None = None
    a2a: A2AConfig | None = None
    demo: DemoConfig | None = None


def load_agent_config(path: str | Path) -> AgentConfig:
    with Path(path).open(encoding="utf-8") as fh:
        cfg = AgentConfig.model_validate(yaml.safe_load(fh))
    if getattr(cfg, cfg.adapter) is None:
        raise AdapterError("config_section_missing", path=path, adapter=cfg.adapter)
    return cfg


# ---------------- Helpers ----------------

_ENV = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def expand_env(value: str) -> str:
    def sub(m):
        v = os.environ.get(m.group(1))
        if v is None:
            raise AdapterError("env_missing", name=m.group(1))
        return v
    return _ENV.sub(sub, value)


def get_path(data: Any, path: str) -> Any:
    """Get a value by dotted path; list indices are supported: choices.0.message.content"""
    cur = data
    for part in path.split("."):
        if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def render_template(tpl: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(tpl, str):
        for key, val in ctx.items():
            if tpl == "{{" + key + "}}":
                return val           # whole-value placeholder: keep the original type (e.g. the history list)
        return re.sub(r"\{\{(\w+)\}\}", lambda m: str(ctx.get(m.group(1), m.group(0))), tpl)
    if isinstance(tpl, dict):
        return {k: render_template(v, ctx) for k, v in tpl.items()}
    if isinstance(tpl, list):
        return [render_template(v, ctx) for v in tpl]
    return tpl


def _parse_tool_calls(raw: Any) -> list[ToolCall]:
    out: list[ToolCall] = []
    for item in raw or []:
        if isinstance(item, str):
            out.append(ToolCall(name=item))
        elif isinstance(item, dict):
            fn = item.get("function") or item
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            if fn.get("name"):
                out.append(ToolCall(name=fn["name"], arguments=args if isinstance(args, dict) else {}))
    return out


# ---------------- Sessions ----------------

class AgentSession(Protocol):
    def send(self, user_text: str) -> AgentReply: ...
    def close(self) -> None: ...


class OpenAISession:
    def __init__(self, cfg: OpenAIConfig, paper: ExamPaper, case: Case, client: httpx.Client):
        self.cfg, self.case, self.client = cfg, case, client
        system = cfg.system_prompt
        if cfg.inject_paper_context and paper.context:
            system = (system + "\n\n" if system else "") + paper.context
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        self.tools = [{"type": "function", "function": t} for t in paper.tools] if cfg.send_tools else []
        key = os.environ.get(cfg.api_key_env) if cfg.api_key_env else None
        if cfg.api_key_env and not key:
            raise AdapterError("env_missing", name=cfg.api_key_env)
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}

    def _call(self) -> dict:
        body: dict[str, Any] = {"model": self.cfg.model, "messages": self.messages}
        if self.tools:
            body["tools"] = self.tools
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        try:
            r = self.client.post(url, json=body, headers=self.headers, timeout=self.cfg.timeout_s)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            raise AdapterError("agent_call_failed", error=e) from e

    def send(self, user_text: str) -> AgentReply:
        self.messages.append({"role": "user", "content": user_text})
        t0 = time.perf_counter()
        calls: list[ToolCall] = []
        msg = self._call()
        rounds = 0
        while msg.get("tool_calls") and rounds < self.cfg.max_tool_rounds:
            rounds += 1
            self.messages.append({"role": "assistant", "content": msg.get("content") or "",
                                  "tool_calls": msg["tool_calls"]})
            for tc, parsed in zip(msg["tool_calls"], _parse_tool_calls(msg["tool_calls"])):
                calls.append(parsed)
                result = self.case.tool_mocks.get(parsed.name, {"error": "tool not available in test environment"})
                self.messages.append({"role": "tool", "tool_call_id": tc.get("id", parsed.name),
                                      "content": json.dumps(result, ensure_ascii=False)})
            msg = self._call()
        text = msg.get("content") or ""
        self.messages.append({"role": "assistant", "content": text})
        return AgentReply(text=text, tool_calls=calls, latency_ms=(time.perf_counter() - t0) * 1000)

    def close(self) -> None:
        pass


class WebhookSession:
    def __init__(self, cfg: WebhookConfig, case: Case, client: httpx.Client):
        self.cfg, self.case, self.client = cfg, case, client
        self.session_id = f"agenteval-{uuid.uuid4().hex[:12]}"
        self.history: list[dict[str, str]] = []
        self.headers = {k: expand_env(v) for k, v in cfg.headers.items()}

    def send(self, user_text: str) -> AgentReply:
        ctx = {"message": user_text, "session_id": self.session_id, "case_id": self.case.id,
               "history": list(self.history)}
        body = render_template(self.cfg.body, ctx)
        t0 = time.perf_counter()
        try:
            r = self.client.request(self.cfg.method, expand_env(self.cfg.url), json=body,
                                    headers=self.headers, timeout=self.cfg.timeout_s)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise AdapterError("agent_call_failed", error=e) from e
        latency = (time.perf_counter() - t0) * 1000
        text = get_path(data, self.cfg.response.text_path)
        if not isinstance(text, str):
            raise AdapterError("text_path_missing", path=self.cfg.response.text_path)
        calls = _parse_tool_calls(get_path(data, self.cfg.response.tool_calls_path)) \
            if self.cfg.response.tool_calls_path else []
        if self.cfg.response.session_id_path:
            sid = get_path(data, self.cfg.response.session_id_path)
            if isinstance(sid, str) and sid:
                self.session_id = sid
        self.history += [{"role": "user", "content": user_text}, {"role": "assistant", "content": text}]
        return AgentReply(text=text, tool_calls=calls, latency_ms=latency)

    def close(self) -> None:
        pass


_TERMINAL = {"completed", "failed", "canceled", "rejected"}
_PAUSED = {"input-required", "auth-required"}


def _a2a_state(raw: Any) -> str:
    """Normalize v0.3 ('input-required') and v1.0 ('TASK_STATE_INPUT_REQUIRED') task states."""
    s = str(raw or "").lower()
    if s.startswith("task_state_"):
        s = s[len("task_state_"):]
    return s.replace("_", "-")


class A2ASession:
    """One A2A conversation per exam case: contextId carries the turns; taskId continues paused tasks."""

    def __init__(self, cfg: A2AConfig, paper: ExamPaper, case: Case, client: httpx.Client):
        self.cfg, self.paper, self.client = cfg, paper, client
        self.headers = {k: expand_env(v) for k, v in cfg.headers.items()}
        self.endpoint, self.version = self._resolve()
        if self.endpoint.startswith("http://") and not _is_local_url(self.endpoint):
            raise AdapterError("a2a_insecure_url", url=self.endpoint)
        if self.version == "1.0":
            self.headers["A2A-Version"] = "1.0"
        self.context_id: str | None = None
        self.task_id: str | None = None
        self.first = True

    def _resolve(self) -> tuple[str, str]:
        from agenteval.collectors.a2a import CardError, fetch_card, interfaces

        want = self.cfg.protocol_version
        if self.cfg.url:
            return expand_env(self.cfg.url), ("0.3" if want == "auto" else want)
        if not self.cfg.agent_card_url:
            raise AdapterError("a2a_no_interface", card="(no url or agent_card_url configured)")
        try:
            card, where = fetch_card(expand_env(self.cfg.agent_card_url), client=self.client,
                                     timeout=self.cfg.timeout_s, headers=self.headers)
        except CardError as e:
            raise AdapterError("agent_call_failed", error=e) from e
        options = [i for i in interfaces(card) if i["binding"] in ("JSONRPC", "JSON-RPC", "JSONRPC2")]
        if want != "auto":
            options = [i for i in options if i["version"].startswith(want)]
        if not options:
            raise AdapterError("a2a_no_interface", card=where)
        best = max(options, key=lambda i: (i["version"].startswith("1"), i["version"]))
        return best["url"], ("1.0" if best["version"].startswith("1") else "0.3")

    # ---- JSON-RPC ----
    def _rpc(self, method_v03: str, method_v1: str, params: dict) -> Any:
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex,
                "method": method_v1 if self.version == "1.0" else method_v03, "params": params}
        try:
            r = self.client.post(self.endpoint, json=body, headers=self.headers, timeout=self.cfg.timeout_s)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise AdapterError("agent_call_failed", error=e) from e
        if not isinstance(data, dict):
            raise AdapterError("agent_call_failed", error="response is not a JSON-RPC object")
        if data.get("error"):
            err = data["error"] if isinstance(data["error"], dict) else {"message": data["error"]}
            raise AdapterError("a2a_rpc_error", rpc_code=err.get("code", "?"), message=err.get("message", ""))
        return data.get("result")

    def _message(self, text: str) -> dict:
        msg: dict[str, Any] = {"messageId": uuid.uuid4().hex}
        if self.version == "1.0":
            msg.update(role="ROLE_USER", parts=[{"text": text}])
        else:
            msg.update(kind="message", role="user", parts=[{"kind": "text", "text": text}])
        if self.context_id:
            msg["contextId"] = self.context_id
        if self.task_id:
            msg["taskId"] = self.task_id
        return msg

    # ---- response parsing ----
    @staticmethod
    def _unwrap(result: Any) -> tuple[str, dict]:
        if not isinstance(result, dict):
            raise AdapterError("agent_call_failed", error="empty A2A result")
        for key in ("task", "message"):                       # v1.0 oneof wrapper
            if isinstance(result.get(key), dict) and len(result) == 1:
                return key, result[key]
        kind = result.get("kind")
        if kind in ("task", "message"):
            return kind, result
        return ("task", result) if "status" in result or "artifacts" in result else ("message", result)

    def _parts(self, parts: Any, texts: list[str], calls: list[ToolCall]) -> None:
        for part in parts or []:
            if not isinstance(part, dict):
                continue
            if part.get("kind", "text") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            data = part.get("data")
            if isinstance(data, dict) and self.cfg.tool_calls_key in data:
                calls.extend(_parse_tool_calls(data[self.cfg.tool_calls_key]))

    def _meta(self, obj: dict, calls: list[ToolCall]) -> None:
        meta = obj.get("metadata")
        if isinstance(meta, dict) and self.cfg.tool_calls_key in meta:
            calls.extend(_parse_tool_calls(meta[self.cfg.tool_calls_key]))

    def _read_task(self, task: dict, texts: list[str], calls: list[ToolCall]) -> str:
        self._meta(task, calls)
        for art in task.get("artifacts") or []:
            if isinstance(art, dict):
                self._meta(art, calls)
                self._parts(art.get("parts"), texts, calls)
        status = task.get("status") or {}
        msg = status.get("message") if isinstance(status, dict) else None
        if isinstance(msg, dict) and not texts:
            self._meta(msg, calls)
            self._parts(msg.get("parts"), texts, calls)
        return _a2a_state(status.get("state") if isinstance(status, dict) else None)

    def send(self, user_text: str) -> AgentReply:
        if self.first and self.cfg.send_context and self.paper.context:
            user_text = f"{self.paper.context}\n\n{user_text}"
        self.first = False
        params: dict[str, Any] = {"message": self._message(user_text)}
        if self.version != "1.0":
            params["configuration"] = {"blocking": True}
        t0 = time.perf_counter()
        kind, obj = self._unwrap(self._rpc("message/send", "SendMessage", params))
        texts: list[str] = []
        calls: list[ToolCall] = []
        self.context_id = obj.get("contextId") or self.context_id
        if kind == "message":
            self._meta(obj, calls)
            self._parts(obj.get("parts"), texts, calls)
            self.task_id = None
        else:
            state = self._read_task(obj, texts, calls)
            deadline = t0 + self.cfg.timeout_s
            while state not in _TERMINAL | _PAUSED:
                if time.perf_counter() > deadline:
                    raise AdapterError("a2a_timeout", seconds=self.cfg.timeout_s)
                time.sleep(self.cfg.poll_interval_s)
                texts, calls = [], []
                _, obj = self._unwrap(self._rpc("tasks/get", "GetTask", {"id": obj.get("id")}))
                state = self._read_task(obj, texts, calls)
            if state in ("failed", "rejected", "canceled") and not texts:
                raise AdapterError("a2a_task_failed", state=state)
            self.task_id = obj.get("id") if state in _PAUSED else None
        return AgentReply(text="\n".join(t for t in texts if t), tool_calls=calls,
                          latency_ms=(time.perf_counter() - t0) * 1000)

    def close(self) -> None:
        pass


def _is_local_url(url: str) -> bool:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


class DemoSession:
    def __init__(self, cfg: DemoConfig):
        self.cfg = cfg
        self.rules = [(re.compile(r.match, re.I), r) for r in cfg.rules]

    def send(self, user_text: str) -> AgentReply:
        for pat, rule in self.rules:
            if pat.search(user_text):
                return AgentReply(text=rule.reply, tool_calls=rule.tool_calls, latency_ms=self.cfg.latency_ms)
        return AgentReply(text=self.cfg.default, latency_ms=self.cfg.latency_ms)

    def close(self) -> None:
        pass


class Agent:
    """Create an independent session per case from the config."""

    def __init__(self, cfg: AgentConfig, client: httpx.Client | None = None):
        self.cfg = cfg
        self.client = client or httpx.Client(follow_redirects=False)

    @property
    def name(self) -> str:
        return self.cfg.name

    def new_session(self, paper: ExamPaper, case: Case) -> AgentSession:
        if self.cfg.adapter == "openai":
            return OpenAISession(self.cfg.openai, paper, case, self.client)
        if self.cfg.adapter == "webhook":
            return WebhookSession(self.cfg.webhook, case, self.client)
        if self.cfg.adapter == "a2a":
            return A2ASession(self.cfg.a2a, paper, case, self.client)
        return DemoSession(self.cfg.demo)
