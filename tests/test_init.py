# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors

from typer.testing import CliRunner

from agenteval.cli import app


def test_init_copies_starter_content_and_repoints_modelines(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner()
    res = r.invoke(app, ["init", "ws"])
    assert res.exit_code == 0, res.output
    ws = tmp_path / "ws"
    for d in ("rules", "exams", "certification", "examples"):
        assert (ws / d).is_dir()
    first = (ws / "rules/us-federal/security.yaml").read_text(encoding="utf-8").splitlines()[0]
    assert first == "# yaml-language-server: $schema=../../.schemas/rule.schema.json"
    assert (ws / ".schemas/rule.schema.json").exists()
    # Second run keeps existing files
    (ws / "certification/policy.yaml").write_text("min_security_score: 95\n", encoding="utf-8")
    assert "0 files copied" in r.invoke(app, ["init", "ws"]).output
    assert (ws / "certification/policy.yaml").read_text(encoding="utf-8") == "min_security_score: 95\n"
    monkeypatch.chdir(ws)
    assert r.invoke(app, ["rules", "validate"]).exit_code == 0
    assert r.invoke(app, ["eval", "validate"]).exit_code == 0
