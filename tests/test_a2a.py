# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""A2A: Agent Card normalization, the agent-protocols rules, and the a2a evaluation adapter (v0.3 and v1.0)."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from agenteval.cli import app
from agenteval.collectors.a2a import CardError, fetch_card, interfaces, normalize_card
from agenteval.engine import load_rules, run_scan
from agenteval.evaluation import Agent, AgentConfig, load_paper, run_exam
from agenteval.evaluation.adapters import AdapterError
from agenteval.evaluation.judges import HeuristicJudge


@pytest.fixture
def paper(root):
    return load_paper(root / "exams" / "customer-service" / "cs-ecommerce-v1.yaml")


@pytest.fixture
def cards(root):
    d = root / "examples" / "a2a"
    return {k: json.loads((d / f"{k}.agent-card.json").read_text()) for k in ("northwind-support", "legacy-helper")}


def _results(rules_dir, asset):
    rules = [r for r in load_rules(rules_dir) if "a2a_agent" in r.applies_to.asset_types]
    return {f.rule_id: f.status.value for f in run_scan(rules, [asset], "t").findings}


def test_v1_card_passes_all_rules(rules_dir, cards):
    a = normalize_card(cards["northwind-support"], "x")
    assert a.type == "a2a_agent" and a.facts["protocol_versions"] == ["1.0"]
    assert a.facts["auth"]["schemes"] == ["oauth2"] and a.facts["auth"]["required"]
    assert set(_results(rules_dir, a).values()) == {"pass"}


def test_v03_legacy_card_fails_every_rule(rules_dir, cards):
    a = normalize_card(cards["legacy-helper"], "x")
    f = a.facts
    assert f["endpoint"]["any_plain_http"] and f["endpoint"]["secrets_in_url"]
    assert f["auth"]["api_key_in_query"] and not f["auth"]["required"] and f["oauth"]["deprecated_flows"]
    assert set(_results(rules_dir, a).values()) == {"fail"}


def test_interfaces_merge_v03_and_v1_shapes():
    card = {"name": "x", "url": "https://a/v03", "protocolVersion": "0.3.0",
            "additionalInterfaces": [{"url": "https://a/grpc", "transport": "GRPC"}],
            "supportedInterfaces": [{"url": "https://a/v1", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]}
    got = [(i["url"], i["binding"], i["version"]) for i in interfaces(card)]
    assert got == [("https://a/v1", "JSONRPC", "1.0"), ("https://a/v03", "JSONRPC", "0.3.0"),
                   ("https://a/grpc", "GRPC", "0.3.0")]


def test_fetch_card_refuses_plain_http_and_bad_cards(tmp_path):
    with pytest.raises(CardError):
        fetch_card("http://agent.example.com")
    with pytest.raises(CardError):
        normalize_card({"description": "no name"})
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: seen.append(str(r.url)) or httpx.Response(200, json={"name": "Local"})))
    card, url = fetch_card("http://localhost:9999", client=client)
    assert card["name"] == "Local" and seen == ["http://localhost:9999/.well-known/agent-card.json"]


def test_cli_a2a_card_exit_codes(root):
    r = CliRunner()
    assert r.invoke(app, ["a2a", "card", str(root / "examples/a2a/northwind-support.agent-card.json")]).exit_code == 0
    res = r.invoke(app, ["a2a", "card", str(root / "examples/a2a/legacy-helper.agent-card.json")])
    assert res.exit_code == 1 and "PROTO-A2A-001" in res.output


# ---------------- evaluation adapter ----------------

class FakeA2A:
    """A tiny in-memory A2A agent for one exam case (CS-003, a three-turn return)."""

    def __init__(self, version: str, paused: bool = False, poll: bool = False):
        self.version, self.paused, self.poll = version, paused, poll
        self.requests: list[dict] = []
        self.turn = 0

    def card(self):
        if self.version == "1.0":
            return {"name": "Fake", "supportedInterfaces": [
                {"url": "https://fake.test/rpc", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]}
        return {"name": "Fake", "url": "https://fake.test/rpc", "protocolVersion": "0.3.0"}

    def state(self, s):
        return f"TASK_STATE_{s.upper().replace('-', '_')}" if self.version == "1.0" else s

    def text_part(self, t):
        return {"text": t} if self.version == "1.0" else {"kind": "text", "text": t}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("agent-card.json"):
            return httpx.Response(200, json=self.card())
        body = json.loads(req.content)
        self.requests.append({"body": body, "headers": dict(req.headers)})
        if body["method"] in ("tasks/get", "GetTask"):
            task = {"id": "task-1", "contextId": "ctx-1", "status": {"state": self.state("completed")},
                    "artifacts": [{"parts": [self.text_part("Refunds arrive within 5-7 business days.")]}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                             "result": {"task": task} if self.version == "1.0" else task})
        self.turn += 1
        replies = {
            1: ("Found order NW-10077 (TrailRunner boots). Shall I start the return?", ["lookup_order"],
                "input-required" if self.paused else "completed"),
            2: ("Done: RMA-55821 created and a label was emailed.", ["create_return"], "completed"),
            3: ("Refunds arrive within 5-7 business days.", [], "working" if self.poll else "completed"),
        }
        text, tools, state = replies[self.turn]
        task = {"id": "task-1", "contextId": "ctx-1",
                "status": {"state": self.state(state)},
                "metadata": {"tool_calls": [{"name": n, "arguments": {"order_id": "NW-10077"}} for n in tools]},
                "artifacts": [] if state == "working" else [{"parts": [self.text_part(text)]}]}
        if self.version != "1.0":
            task["kind"] = "task"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "result": {"task": task} if self.version == "1.0" else task})


def _agent(fake, **cfg):
    conf = AgentConfig.model_validate({"name": "fake-a2a", "adapter": "a2a",
                                       "a2a": {"agent_card_url": "https://fake.test", "poll_interval_s": 0, **cfg}})
    return Agent(conf, client=httpx.Client(transport=httpx.MockTransport(fake)))


@pytest.mark.parametrize("version", ["0.3", "1.0"])
def test_a2a_adapter_multi_turn(paper, version):
    fake = FakeA2A(version, paused=True)
    run = run_exam(paper, _agent(fake), HeuristicJudge(), case_ids=["CS-003"])
    case = run.cases[0]
    assert case.error is None, case.error
    assert all(a.passed for a in case.assertions), [(a.type, a.detail) for a in case.assertions if not a.passed]
    first, second = fake.requests[0]["body"], fake.requests[1]["body"]
    if version == "1.0":
        assert first["method"] == "SendMessage" and first["params"]["message"]["role"] == "ROLE_USER"
        assert "kind" not in first["params"]["message"]["parts"][0]
        assert fake.requests[0]["headers"]["a2a-version"] == "1.0"
    else:
        assert first["method"] == "message/send" and first["params"]["configuration"]["blocking"] is True
        assert first["params"]["message"]["parts"][0]["kind"] == "text"
    assert "contextId" not in first["params"]["message"]
    assert second["params"]["message"]["contextId"] == "ctx-1"
    assert second["params"]["message"]["taskId"] == "task-1"            # task was input-required
    assert "taskId" not in fake.requests[2]["body"]["params"]["message"]  # previous task completed


def test_a2a_adapter_polls_until_terminal(paper):
    fake = FakeA2A("1.0", poll=True)
    run = run_exam(paper, _agent(fake), HeuristicJudge(), case_ids=["CS-003"])
    assert run.cases[0].error is None
    assert [r["body"]["method"] for r in fake.requests][-1] == "GetTask"


def test_a2a_adapter_errors(paper, monkeypatch):
    monkeypatch.setenv("A2A_TOKEN", "s3cret")

    def rpc_error(req):
        if req.url.path.endswith("agent-card.json"):
            return httpx.Response(200, json=FakeA2A("0.3").card())
        assert req.headers["authorization"] == "Bearer s3cret"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32001, "message": "nope"}})

    run = run_exam(paper, _agent(rpc_error, headers={"Authorization": "Bearer ${A2A_TOKEN}"}), HeuristicJudge(),
                   case_ids=["CS-001"])
    assert run.cases[0].error and "-32001" in run.cases[0].error
    conf = AgentConfig.model_validate({"name": "x", "adapter": "a2a", "a2a": {"url": "http://remote.example.com/rpc"}})
    with pytest.raises(AdapterError):
        Agent(conf).new_session(paper, paper.cases[0])
