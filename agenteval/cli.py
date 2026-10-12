# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""AgentEval command-line interface.

Exit codes are stable and meant for CI gates:
  0  success / threshold not exceeded / eligible / valid
  1  policy result is negative (failed checks, evaluation not passed, not eligible, invalid certificate)
  2  usage or input error (bad rules, bad paper, unreadable file, missing option)
"""

from __future__ import annotations

import json
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Optional

import typer

from agenteval import __version__
from agenteval.collectors import collect_from_kubernetes, collect_from_snapshot_file
from agenteval.config import default_rules_dir, load_attestations
from agenteval.engine import RuleLoadError, load_rules, run_scan
from agenteval.models import Severity, Status
from agenteval.reports import render

app = typer.Typer(
    help="AgentEval: scan AI infrastructure against rule packs, evaluate AI agents with standard exams, "
         "and issue signed certificates when both pass.",
    no_args_is_help=True,
)
rules_app = typer.Typer(help="Validate and list rule packs.", no_args_is_help=True)
app.add_typer(rules_app, name="rules")


class SourceKind(str, Enum):
    snapshot = "snapshot"
    kubernetes = "kubernetes"


class OutFormat(str, Enum):
    json = "json"
    sarif = "sarif"
    html = "html"


class Lang(str, Enum):
    en = "en"
    zh = "zh"


class FailOn(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    none = "none"


CLI_MESSAGES = {
    "scan_summary": {
        "en": "Scan {id}: {assets} assets · {passed} passed · {failed} failed · {manual} awaiting attestation · pass rate {score}%",
        "zh": "扫描 {id}: 资产 {assets} · 通过 {passed} · 不通过 {failed} · 待证明 {manual} · 通过率 {score}%"},
    "blocking": {"en": "{n} failed checks at severity {sev} or above",
                 "zh": "存在 {n} 个达到 {sev} 及以上的不通过项"},
    "rules_ok": {"en": "✓ {n} rules validated", "zh": "✓ {n} 条规则校验通过"},
    "rules_unverified": {"en": "Note: {n} regulation mappings are pending legal/compliance review (verified=false)",
                         "zh": "注意：{n} 处法规映射尚未经法务/合规审核（verified=false）"},
    "papers_ok": {"en": "✓ {papers} exam papers and {cases} cases validated",
                  "zh": "✓ {papers} 份试卷、{cases} 个样本校验通过"},
    "paper_line": {"en": "{id:<22} v{version:<7} {cases:>3} cases  {title}\n{blank:<22} dimensions {dims}",
                   "zh": "{id:<22} v{version:<7} {cases:>3} 题  {title}\n{blank:<22} 维度 {dims}"},
    "eval_summary": {
        "en": "Eval {id}: {agent} × {paper} → {score} (grade {grade}) · cases passed {cp}/{c} · critical failures {cf} · judge {judge}",
        "zh": "评测 {id}: {agent} × {paper} → {score} 分（{grade}） · 样本通过 {cp}/{c} · 关键项失败 {cf} · 裁判 {judge}"},
    "attest_written": {"en": "Pre-deployment testing attestation written: {path}", "zh": "已生成部署前测试证明: {path}"},
    "attest_skipped": {"en": "Evaluation did not pass; no pre-deployment testing attestation written",
                       "zh": "评测未通过，未生成部署前测试证明"},
    "snapshot_written": {"en": "Exported {n} workloads to {path}", "zh": "已导出 {n} 个工作负载到 {path}"},
    "snapshot_required": {"en": "--snapshot is required when --source=snapshot",
                          "zh": "source=snapshot 时必须提供 --snapshot"},
    "collect_failed": {"en": "Collection failed: {e}", "zh": "采集失败: {e}"},
    "unknown_system": {"en": "Unknown AI system: {ref} (registered: {ids})", "zh": "未登记的 AI 系统: {ref}（可用: {ids}）"},
    "bad_inputs": {"en": "Cannot read scan/eval JSON (produce them with -f json): {e}",
                   "zh": "读取扫描/评测 JSON 失败（请使用 -f json 输出）: {e}"},
    "eligible_demo": {"en": "Eligible — DEMO certificate only", "zh": "满足颁证条件 —— 仅可颁发 DEMO 证书"},
    "eligible": {"en": "Eligible for certification", "zh": "满足颁证条件"},
    "not_eligible": {"en": "Not eligible", "zh": "不满足颁证条件"},
    "issued": {"en": "Certificate issued: {id} → {dir}", "zh": "已签发证书: {id} → {dir}"},
    "sig_ok": {"en": "Signature valid", "zh": "签名有效"},
    "sig_bad": {"en": "Signature INVALID", "zh": "签名无效"},
    "state": {"en": "State: {state}", "zh": "状态: {state}"},
    "offline": {"en": " (offline; revocation not checked)", "zh": "（离线，未检查吊销）"},
    "demo_cert": {"en": "DEMO certificate", "zh": "演示证书"},
    "need_pubkey": {"en": "No public key given and no local signing key at {path}; pass --public-key",
                    "zh": "未指定 --public-key，且本地不存在签名密钥 {path}"},
    "schemas_written": {"en": "Wrote {n} JSON Schemas to {dir}", "zh": "已导出 {n} 个 JSON Schema 到 {dir}"},
    "init_done": {"en": "Starter content in {dir}: {copied} files copied, {skipped} existing files kept",
                  "zh": "入门内容已写入 {dir}：复制 {copied} 个文件，保留已有文件 {skipped} 个"},
    "init_missing": {"en": "Bundled starter content not found in this installation",
                     "zh": "当前安装中找不到内置的入门内容"},
}


def _m(key: str, lang: str, **kw) -> str:
    return CLI_MESSAGES[key].get(lang, CLI_MESSAGES[key]["en"]).format(**kw)


def _default_lang() -> "Lang":
    """English by default; set AGENTEVAL_LANG=zh for Chinese output."""
    return Lang.zh if os.environ.get("AGENTEVAL_LANG", "").lower().startswith("zh") else Lang.en


LANG_OPT = typer.Option(_default_lang, "--lang", "-l", help="Output language for reports and console: en / zh ($AGENTEVAL_LANG).")


def _err(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)


def _load_rules_or_exit(path: Path):
    try:
        return load_rules(path)
    except RuleLoadError as e:
        _err(str(e))
        raise typer.Exit(2)


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def scan(
    source: SourceKind = typer.Option(SourceKind.snapshot, help="Where to collect assets from."),
    snapshot: Optional[Path] = typer.Option(None, help="Offline snapshot JSON (required for --source snapshot)."),
    context: Optional[str] = typer.Option(None, help="kubeconfig context."),
    namespace: list[str] = typer.Option([], "--namespace", "-n", help="Limit to a namespace; repeatable."),
    in_cluster: bool = typer.Option(False, help="Use the in-cluster ServiceAccount."),
    rules: Path = typer.Option(None, help="Rules directory or file (default ./rules or $AGENTEVAL_RULES_DIR)."),
    attestations: list[Path] = typer.Option([], help="Attestation YAML; repeatable, files are merged."),
    fmt: OutFormat = typer.Option(OutFormat.json, "--format", "-f", help="Report format."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Report path (default stdout)."),
    lang: Lang = LANG_OPT,
    fail_on: FailOn = typer.Option(FailOn.high, help="Exit 1 if any failed check is at this severity or above."),
) -> None:
    """Scan a cluster (or snapshot) and evaluate it against the rule packs."""
    rule_set = _load_rules_or_exit(rules or default_rules_dir())
    try:
        atts = load_attestations(list(attestations))
        if source == SourceKind.snapshot:
            if not snapshot:
                _err(_m("snapshot_required", lang.value))
                raise typer.Exit(2)
            assets, _ = collect_from_snapshot_file(snapshot)
            source_label = f"snapshot:{snapshot.name}"
        else:
            assets, _ = collect_from_kubernetes(context=context, namespaces=namespace or None, in_cluster=in_cluster)
            source_label = f"kubernetes:{context or ('in-cluster' if in_cluster else 'current-context')}"
    except typer.Exit:
        raise
    except Exception as e:
        _err(_m("collect_failed", lang.value, e=e))
        raise typer.Exit(2)

    result = run_scan(rule_set, assets, source=source_label, attestations=atts)
    text = render(result, fmt.value, lang.value)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")

    s = result.summary
    typer.secho(_m("scan_summary", lang.value, id=result.scan_id, assets=result.asset_count,
                   passed=s.by_status.get("pass", 0), failed=s.by_status.get("fail", 0),
                   manual=s.by_status.get("manual", 0), score=s.score), err=True)
    if fail_on != FailOn.none:
        threshold = Severity(fail_on.value).rank
        blocking = [f for f in result.findings if f.status == Status.failed and f.severity.rank >= threshold]
        if blocking:
            _err(_m("blocking", lang.value, n=len(blocking), sev=fail_on.value))
            raise typer.Exit(1)


@app.command("snapshot")
def snapshot_cmd(
    context: Optional[str] = typer.Option(None, help="kubeconfig context."),
    namespace: list[str] = typer.Option([], "--namespace", "-n", help="Limit to a namespace; repeatable."),
    in_cluster: bool = typer.Option(False, help="Use the in-cluster ServiceAccount."),
    output: Path = typer.Option(..., "--output", "-o", help="Snapshot output path."),
    lang: Lang = LANG_OPT,
) -> None:
    """Export a read-only configuration snapshot for offline or air-gapped scanning."""
    from agenteval.collectors.kubernetes import fetch_snapshot

    snap = fetch_snapshot(context=context, namespaces=namespace or None, in_cluster=in_cluster)
    output.write_text(json.dumps(snap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    typer.echo(_m("snapshot_written", lang.value, n=len(snap["workloads"]), path=output))


@rules_app.command("validate")
def rules_validate(rules: Path = typer.Option(None, help="Rules directory or file."), lang: Lang = LANG_OPT) -> None:
    """Validate rules, pack metadata and translations against the schemas."""
    rule_set = _load_rules_or_exit(rules or default_rules_dir())
    unverified = sum(1 for r in rule_set for s in r.sources if not s.verified)
    typer.secho(_m("rules_ok", lang.value, n=len(rule_set)), fg=typer.colors.GREEN)
    if unverified:
        typer.secho(_m("rules_unverified", lang.value, n=unverified), fg=typer.colors.YELLOW)


@rules_app.command("list")
def rules_list(rules: Path = typer.Option(None, help="Rules directory or file."), lang: Lang = LANG_OPT) -> None:
    """List rules with their pack, severity and type."""
    for r in _load_rules_or_exit(rules or default_rules_dir()):
        title = (r.translations.get(lang.value) or {}).get("title") or r.title
        typer.echo(f"{r.id:<14} {r.pack:<10} {r.severity.value:<9} {r.type.value:<12} {title}")


@app.command("init")
def init_cmd(directory: Path = typer.Argument(Path("."), help="Where to create rules/, exams/, certification/ and examples/."),
             lang: Lang = LANG_OPT) -> None:
    """Copy the starter rule packs, exam papers, certification policy and examples into a directory to customize."""
    import shutil
    from importlib import resources

    from agenteval.config import STARTER_DIRS, starter_root

    src = starter_root()
    if not all((src / d).is_dir() for d in STARTER_DIRS):
        typer.echo(_m("init_missing", lang.value), err=True)
        raise typer.Exit(2)
    copied = skipped = 0
    for d in STARTER_DIRS:
        for f in sorted((src / d).rglob("*")):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            dest = directory / f.relative_to(src)
            if dest.exists():
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            text = f.read_text(encoding="utf-8") if f.suffix in (".yaml", ".yml") else None
            if text and text.startswith("# yaml-language-server: $schema="):
                # Re-point the editor modeline at the schemas exported to <dir>/.schemas/.
                first, _, rest = text.partition("\n")
                rel = os.path.relpath(directory / ".schemas" / Path(first.split("=", 1)[1]).name, dest.parent)
                dest.write_text(f"# yaml-language-server: $schema={Path(rel).as_posix()}\n{rest}", encoding="utf-8")
            else:
                shutil.copy2(f, dest)
            copied += 1
    schemas_dir = directory / ".schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)
    for f in resources.files("agenteval").joinpath("schemas").iterdir():
        if f.name.endswith(".schema.json"):
            (schemas_dir / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    typer.echo(_m("init_done", lang.value, dir=directory, copied=copied, skipped=skipped))


@app.command("schemas")
def schemas_cmd(out: Path = typer.Option(Path("schemas"), "--out", "-o", help="Directory to write the JSON Schemas to."),
                lang: Lang = LANG_OPT) -> None:
    """Export the JSON Schemas (rules, packs, translations, attestations, exams, systems) for editor validation."""
    from importlib import resources

    out.mkdir(parents=True, exist_ok=True)
    files = [f for f in resources.files("agenteval").joinpath("schemas").iterdir() if f.name.endswith(".schema.json")]
    for f in files:
        (out / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    typer.echo(_m("schemas_written", lang.value, n=len(files), dir=out))


# ======================= Agent evaluation =======================

eval_app = typer.Typer(help="Evaluate AI agents with standard exam papers.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")


class JudgeKind(str, Enum):
    heuristic = "heuristic"
    anthropic = "anthropic"
    openai = "openai"


class EvalFormat(str, Enum):
    html = "html"
    json = "json"


@eval_app.command("papers")
def eval_papers(exams: Path = typer.Option(None, help="Exam library directory (default ./exams or $AGENTEVAL_EXAMS_DIR)."),
                lang: Lang = LANG_OPT) -> None:
    """List exam papers and their dimension weights."""
    from agenteval.evaluation import PaperError, load_papers

    try:
        papers = load_papers(exams)
    except PaperError as e:
        _err(str(e))
        raise typer.Exit(2)
    for p in papers.values():
        dims = ", ".join(f"{d.id}:{int(d.weight * 100)}%" for d in p.dimensions)
        typer.echo(_m("paper_line", lang.value, id=p.id, version=p.version, cases=len(p.cases), title=p.title,
                      blank="", dims=dims))


@eval_app.command("validate")
def eval_validate(exams: Path = typer.Option(None, help="Exam library directory."), lang: Lang = LANG_OPT) -> None:
    """Validate exam papers (schema, dimensions, tools, assertions)."""
    from agenteval.evaluation import PaperError, load_papers

    try:
        papers = load_papers(exams)
    except PaperError as e:
        _err(str(e))
        raise typer.Exit(2)
    n = sum(len(p.cases) for p in papers.values())
    typer.secho(_m("papers_ok", lang.value, papers=len(papers), cases=n), fg=typer.colors.GREEN)


@eval_app.command("run")
def eval_run(
    paper: str = typer.Option(..., "--paper", "-p", help="Exam paper ID or path."),
    agent: Path = typer.Option(..., "--agent", "-a", help="Agent-under-test config YAML."),
    judge: JudgeKind = typer.Option(JudgeKind.heuristic, help="Rubric judge."),
    judge_model: Optional[str] = typer.Option(None, help="LLM judge model (or $AGENTEVAL_JUDGE_MODEL)."),
    judge_base_url: Optional[str] = typer.Option(None, help="Base URL for an OpenAI-compatible judge."),
    judge_key_env: Optional[str] = typer.Option(None, help="Environment variable that holds the judge API key."),
    case: list[str] = typer.Option([], "--case", help="Run only this case ID; repeatable."),
    concurrency: int = typer.Option(1, min=1, max=16, help="Cases to run in parallel."),
    exams: Path = typer.Option(None, help="Exam library directory."),
    fmt: EvalFormat = typer.Option(EvalFormat.html, "--format", "-f", help="Report format."),
    lang: Lang = LANG_OPT,
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Report path (default stdout)."),
    attest_asset: Optional[str] = typer.Option(None, help="On pass, write FED-GOV-A02 pre-deployment testing evidence for this asset."),
    attest_output: Path = typer.Option(Path("attestation-eval.yaml"), help="Where to write that attestation."),
) -> None:
    """Run an exam paper against an agent and score it. Exit 0 pass / 1 not passed / 2 error."""
    import yaml as _yaml

    from agenteval.evaluation import (
        Agent, AdapterError, JudgeError, PaperError, load_agent_config, make_judge, render as render_eval,
        resolve_paper, run_exam, to_attestation,
    )

    try:
        exam = resolve_paper(paper, exams)
        bot = Agent(load_agent_config(agent))
        j = make_judge(judge.value, judge_model, judge_base_url, judge_key_env)
    except (PaperError, AdapterError, JudgeError, OSError, ValueError) as e:
        _err(str(e))
        raise typer.Exit(2)

    run = run_exam(exam, bot, j, concurrency=concurrency, case_ids=case or None)
    text = render_eval(run, fmt.value, lang.value)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")

    s = run.stats
    typer.secho(_m("eval_summary", lang.value, id=run.run_id, agent=run.agent_name, paper=run.paper_id,
                   score=run.overall_score, grade=run.grade, cp=s.cases_passed, c=s.cases,
                   cf=s.critical_failures, judge=run.judge),
                err=True, fg=typer.colors.GREEN if run.passed else typer.colors.RED)
    if attest_asset:
        if run.passed:
            entry = to_attestation(run, attest_asset)
            attest_output.write_text(_yaml.safe_dump({"attestations": [entry]}, allow_unicode=True, sort_keys=False),
                                     encoding="utf-8")
            typer.echo(_m("attest_written", lang.value, path=attest_output), err=True)
        else:
            _err(_m("attest_skipped", lang.value))
    if not run.passed:
        raise typer.Exit(1)


# ======================= Certification =======================

cert_app = typer.Typer(help="Certify an AI system when its scan and evaluation both pass the policy.",
                       no_args_is_help=True)
app.add_typer(cert_app, name="cert")


def _system(ref: str, lang: str):
    from agenteval.certification import load_system_file, load_systems

    p = Path(ref)
    if p.suffix in (".yaml", ".yml") and p.exists():
        return load_system_file(p)
    systems = load_systems()
    if ref not in systems:
        _err(_m("unknown_system", lang, ref=ref, ids=", ".join(sorted(systems))))
        raise typer.Exit(2)
    return systems[ref]


def _decision(system_ref: str, scan: Path, eval_path: Path, rules: Optional[Path], lang: str):
    from agenteval.certification import decide, load_policy
    from agenteval.engine import load_packs
    from agenteval.evaluation.models import EvalRun
    from agenteval.models import ScanResult

    system = _system(system_ref, lang)
    try:
        scan_r = ScanResult.model_validate_json(scan.read_text(encoding="utf-8"))
        run = EvalRun.model_validate_json(eval_path.read_text(encoding="utf-8"))
    except Exception as e:
        _err(_m("bad_inputs", lang, e=e))
        raise typer.Exit(2)
    policy = load_policy()
    return system, policy, decide(system, scan_r, run, load_packs(rules or default_rules_dir()), policy)


def _print_decision(d, lang: str) -> None:
    from agenteval.certification import gate_text

    for g in d.gates:
        mark = "✓" if g.ok else ("!" if g.kind == "demo" else "✕")
        color = typer.colors.GREEN if g.ok else (typer.colors.YELLOW if g.kind == "demo" else typer.colors.RED)
        typer.secho(f"  {mark} {gate_text(g.code, g.params, lang)}", fg=color)
    if d.eligible:
        typer.secho(_m("eligible_demo" if d.demo else "eligible", lang),
                    fg=typer.colors.YELLOW if d.demo else typer.colors.GREEN)
    else:
        typer.secho(_m("not_eligible", lang), fg=typer.colors.RED)


@cert_app.command("check")
def cert_check(
    system: str = typer.Option(..., "--system", "-s", help="Registered system ID or system YAML path."),
    scan: Path = typer.Option(..., help="Scan result JSON (agenteval scan -f json)."),
    eval_path: Path = typer.Option(..., "--eval", help="Evaluation result JSON (agenteval eval run -f json)."),
    rules: Optional[Path] = typer.Option(None, help="Rules directory (for pack tiers)."),
    lang: Lang = LANG_OPT,
) -> None:
    """Check the certification gates. Exit 0 eligible / 1 not eligible / 2 input error."""
    _, _, d = _decision(system, scan, eval_path, rules, lang.value)
    _print_decision(d, lang.value)
    if not d.eligible:
        raise typer.Exit(1)


@cert_app.command("issue")
def cert_issue(
    system: str = typer.Option(..., "--system", "-s", help="Registered system ID or system YAML path."),
    scan: Path = typer.Option(..., help="Scan result JSON."),
    eval_path: Path = typer.Option(..., "--eval", help="Evaluation result JSON."),
    rules: Optional[Path] = typer.Option(None, help="Rules directory (for pack tiers)."),
    out_dir: Path = typer.Option(Path("certificates"), "--out-dir", "-o", help="Output directory."),
    pdf: bool = typer.Option(False, help="Also export PDFs (requires the 'pdf' extra and a Chromium browser)."),
    verify_url: Optional[str] = typer.Option(
        None, help="Public verification URL template with {id}, printed as a QR code ($AGENTEVAL_VERIFY_URL). "
                   "Without it the certificate shows offline verification instructions."),
    lang: Lang = LANG_OPT,
) -> None:
    """Issue a signed certificate if every gate passes: JSON envelope plus English and Chinese HTML."""
    from agenteval.certification import issue, load_or_create_key
    from agenteval.certification.render import render_certificate, to_pdf

    sysobj, policy, d = _decision(system, scan, eval_path, rules, lang.value)
    _print_decision(d, lang.value)
    if not d.eligible:
        raise typer.Exit(1)
    env = issue(sysobj, d, policy, load_or_create_key(), verify_url or os.environ.get("AGENTEVAL_VERIFY_URL"))
    cid = env["certificate"]["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{cid}.json").write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")
    for lg in ("en", "zh"):
        (out_dir / f"{cid}.{lg}.html").write_text(render_certificate(env, lg), encoding="utf-8")
    if pdf:
        for lg in ("en", "zh"):
            to_pdf(render_certificate(env, lg, printable_button=False), out_dir / f"{cid}.{lg}.pdf")
    typer.secho(_m("issued", lang.value, id=cid, dir=out_dir), fg=typer.colors.GREEN)


@cert_app.command("verify")
def cert_verify(
    cert: Path = typer.Argument(..., help="Certificate JSON envelope."),
    public_key: Optional[Path] = typer.Option(None, help="Issuer public key PEM (default: public half of the local signing key)."),
    status_url: Optional[str] = typer.Option(
        None, help="Revocation status URL template with {id}; must return JSON with a 'state' field ($AGENTEVAL_STATUS_URL)."),
    lang: Lang = LANG_OPT,
) -> None:
    """Verify the signature, expiry and (optionally) online revocation state. Exit 0 valid / 1 invalid."""
    from agenteval.certification import certificate_state, default_key_path, load_or_create_key, load_public_key, verify

    env = json.loads(cert.read_text(encoding="utf-8"))
    if public_key:
        pub = load_public_key(public_key.read_bytes())
    elif default_key_path().exists():
        pub = load_or_create_key().public_key()
    else:
        _err(_m("need_pubkey", lang.value, path=default_key_path()))
        raise typer.Exit(2)
    sig_ok = verify(env, pub)
    status_url = status_url or os.environ.get("AGENTEVAL_STATUS_URL")
    stored = None
    if status_url:
        import httpx
        r = httpx.get(status_url.format(id=env["certificate"]["id"]), timeout=15)
        stored = r.json().get("state") if r.status_code == 200 else "not_found"
    state = stored if stored in ("revoked", "superseded", "not_found") else certificate_state(env)
    typer.secho(_m("sig_ok" if sig_ok else "sig_bad", lang.value), fg=typer.colors.GREEN if sig_ok else typer.colors.RED)
    typer.echo(_m("state", lang.value, state=state) + ("" if status_url else _m("offline", lang.value)))
    if env["certificate"].get("demo"):
        typer.secho(_m("demo_cert", lang.value), fg=typer.colors.YELLOW)
    if not (sig_ok and state == "active"):
        raise typer.Exit(1)


@cert_app.command("keygen")
def cert_keygen(
    out: Path = typer.Option(None, help="Private key path (default $AGENTEVAL_SIGNING_KEY or ./.agenteval/signing-key.pem)."),
    public_out: Optional[Path] = typer.Option(None, "--public-out", help="Also write the public key PEM here, to publish for verifiers."),
) -> None:
    """Create (or load) the Ed25519 signing key and print its public key and fingerprint."""
    from agenteval.certification import key_id, load_or_create_key, public_pem

    key = load_or_create_key(out)
    if public_out:
        public_out.parent.mkdir(parents=True, exist_ok=True)
        public_out.write_text(public_pem(key), encoding="utf-8")
    typer.echo(public_pem(key) + f"key_id: {key_id(key)}")


if __name__ == "__main__":  # pragma: no cover
    app()
