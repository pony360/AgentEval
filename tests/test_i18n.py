# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors

import json
import re
from datetime import date

from agenteval.collectors import collect_from_snapshot_file
from agenteval.config import load_attestations
from agenteval.engine import load_rules, run_scan
from agenteval.reports import render

CJK = re.compile(r"[　-〿一-鿿＀-￯]")


def _result(rules_dir, sample_snapshot, sample_attestations):
    assets, _ = collect_from_snapshot_file(sample_snapshot)
    return run_scan(load_rules(rules_dir), assets, "test",
                    load_attestations(sample_attestations), today=date(2026, 10, 1))


def test_every_rule_is_english_with_chinese_translation(rules_dir):
    for r in load_rules(rules_dir):
        assert not CJK.search(f"{r.title} {r.description} {r.remediation}"), f"{r.id} source text must be English"
        zh = r.translations.get("zh")
        assert zh, f"{r.id} is missing a Chinese translation"
        for k in ("title", "description", "remediation"):
            assert zh.get(k), f"{r.id} is missing zh.{k}"
        assert len(zh.get("sources", [])) == len(r.sources), f"{r.id} has a mismatched number of translated sources"


def test_english_reports_have_no_chinese(rules_dir, sample_snapshot, sample_attestations):
    result = _result(rules_dir, sample_snapshot, sample_attestations)
    for fmt in ("html", "sarif", "json"):
        text = render(result, fmt, "en")
        if fmt == "json":
            # JSON keeps the raw translations field (may be empty for Chinese rules); check display fields only
            doc = json.loads(text)
            shown = [doc["disclaimer"]] + [
                f"{f['rule_title']} {f['message']} {f['remediation']} " + " ".join(s["reference"] for s in f["sources"])
                for f in doc["findings"]]
            text = "\n".join(shown)
        hits = CJK.findall(text)
        assert not hits, f"{fmt} 英文报告残留中文: {''.join(hits)[:40]}"


def test_english_messages(rules_dir, sample_snapshot, sample_attestations):
    result = _result(rules_dir, sample_snapshot, sample_attestations)
    html = render(result, "html", "en")
    assert "AI Compliance Scan Report" in html
    assert "Attestation incomplete; missing: pre_deployment_testing" in html
    assert "Non-compliant: Secrets must not be stored in plaintext environment variables" in html


def test_english_is_default_and_chinese_available(rules_dir, sample_snapshot, sample_attestations):
    result = _result(rules_dir, sample_snapshot, sample_attestations)
    assert "AI Compliance Scan Report" in render(result, "html")
    zh = render(result, "html", "zh")
    assert "AI 合规扫描报告" in zh and "推理端点必须启用身份认证" in zh
    # localization must not modify the original result
    assert not CJK.search(result.findings[0].message)
