# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors

import json

from typer.testing import CliRunner

from agenteval.cli import app

runner = CliRunner()


def test_rules_validate(rules_dir):
    r = runner.invoke(app, ["rules", "validate", "--rules", str(rules_dir)])
    assert r.exit_code == 0, r.output


def test_scan_fails_on_high(rules_dir, sample_snapshot, tmp_path):
    out = tmp_path / "r.json"
    r = runner.invoke(app, ["scan", "--rules", str(rules_dir), "--snapshot", str(sample_snapshot),
                            "-f", "json", "-o", str(out)])
    assert r.exit_code == 1
    assert json.loads(out.read_text(encoding="utf-8"))["findings"]


def test_scan_fail_on_none_exits_zero(rules_dir, sample_snapshot, tmp_path):
    r = runner.invoke(app, ["scan", "--rules", str(rules_dir), "--snapshot", str(sample_snapshot),
                            "-f", "html", "-o", str(tmp_path / "r.html"), "--fail-on", "none", "--lang", "zh"])
    assert r.exit_code == 0
    assert "AI 合规扫描报告" in (tmp_path / "r.html").read_text(encoding="utf-8")


def test_scan_missing_snapshot_is_usage_error(rules_dir):
    r = runner.invoke(app, ["scan", "--rules", str(rules_dir)])
    assert r.exit_code == 2


def test_cli_english_console(rules_dir, sample_snapshot, tmp_path):
    r = runner.invoke(app, ["scan", "--rules", str(rules_dir), "--snapshot", str(sample_snapshot), "--lang", "en",
                            "-o", str(tmp_path / "r.json"), "--fail-on", "none"])
    assert r.exit_code == 0 and "assets" in r.output and "资产" not in r.output
