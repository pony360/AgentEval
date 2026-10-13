# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Rendering of certificates and public verification pages (zh/en), plus optional PDF export."""

from __future__ import annotations

from datetime import datetime
from importlib import resources
from pathlib import Path

from jinja2 import Environment, select_autoescape
from markupsafe import Markup

UI = {
    "zh": {
        "html_lang": "zh-CN", "title": "AI 系统安全与质量认证证书", "cert_no": "证书编号",
        "certifies": "兹证明", "statement": "已通过 AgentEval 工具的安全合规扫描与标准化质量评测，满足本认证全部颁证条件。",
        "security": "安全", "security_sub": "国家、区域及行业安全规范", "quality": "质量", "quality_sub": "标准化评测指标",
        "score": "得分", "threshold": "阈值", "tier": "层级", "pack": "规则包", "checks": "通过 / 适用", "status": "结论",
        "national": "国家", "regional": "区域", "industry": "行业", "baseline": "基线", "unknown": "其他",
        "st_passed": "通过", "st_failed": "未通过", "st_not_applicable": "不适用", "st_not_assessed": "未评估",
        "exam": "试卷", "grade": "等级", "judge": "裁判", "cases": "样本通过", "dimension": "维度",
        "issued": "颁发日期", "expires": "有效期至", "evidence": "证据", "scan": "扫描", "rules": "规则集", "run": "评测",
        "subject": "被认证系统", "asset": "部署资产", "model": "模型", "agent": "评测对象",
        "verify": "扫码或访问以下地址验证证书状态", "verify_offline": "离线验证：使用签发方公钥运行",  "signature": "Ed25519 数字签名 · 公钥指纹",
        "demo": "演示证书 · 不作正式认证用途", "demo_reason": "评测使用离线启发式裁判，正式颁证需使用 LLM 裁判。",
        "state_revoked": "已吊销", "state_expired": "已过期", "state_superseded": "已被新证书替代",
        "print": "打印 / 另存为 PDF",
        # verification page
        "v_title": "证书验证", "v_valid": "有效", "v_invalid_sig": "签名无效", "v_not_found": "未找到该证书",
        "v_sig_ok": "签名校验通过", "v_sig_bad": "签名校验失败", "v_view": "查看证书", "v_reason": "原因",
        "v_status": "状态", "v_org": "所属组织",
    },
    "en": {
        "html_lang": "en", "title": "AI System Assurance Certificate", "cert_no": "Certificate No.",
        "certifies": "This certifies that",
        "statement": "has passed the AgentEval security compliance scan and standardized quality evaluation, and meets every certification criterion.",
        "security": "Security", "security_sub": "National, regional and industry security requirements",
        "quality": "Quality", "quality_sub": "Standardized evaluation metrics",
        "score": "Score", "threshold": "Threshold", "tier": "Tier", "pack": "Rule pack", "checks": "Passed / applicable",
        "status": "Result", "national": "National", "regional": "Regional", "industry": "Industry", "baseline": "Baseline", "unknown": "Other",
        "st_passed": "Passed", "st_failed": "Failed", "st_not_applicable": "Not applicable", "st_not_assessed": "Not assessed",
        "exam": "Exam", "grade": "Grade", "judge": "Judge", "cases": "Cases passed", "dimension": "Dimension",
        "issued": "Issued", "expires": "Valid until", "evidence": "Evidence", "scan": "Scan", "rules": "Rule set", "run": "Eval run",
        "subject": "Certified system", "asset": "Deployment", "model": "Model", "agent": "Agent evaluated",
        "verify": "Scan or visit to verify the certificate status", "verify_offline": "Verify offline with the issuer public key:",  "signature": "Ed25519 signature · key fingerprint",
        "demo": "DEMO certificate · not valid for certification",
        "demo_reason": "The evaluation used the offline heuristic judge; a formal certificate requires an LLM judge.",
        "state_revoked": "Revoked", "state_expired": "Expired", "state_superseded": "Superseded by a newer certificate",
        "print": "Print / Save as PDF",
        "v_title": "Certificate verification", "v_valid": "Valid", "v_invalid_sig": "Invalid signature",
        "v_not_found": "Certificate not found", "v_sig_ok": "Signature verified", "v_sig_bad": "Signature check failed",
        "v_view": "View certificate", "v_reason": "Reason", "v_status": "Status", "v_org": "Organization",
    },
}


def qr_svg(url: str) -> Markup:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=1)
    svg = img.to_string(encoding="unicode")
    return Markup(svg)


def _env() -> Environment:
    env = Environment(autoescape=select_autoescape(default=True, default_for_string=True))
    env.filters["date"] = lambda s: datetime.fromisoformat(s).strftime("%Y-%m-%d") if s else "—"
    return env


def _tpl(name: str) -> str:
    return resources.files("agenteval").joinpath(f"reports/templates/{name}").read_text(encoding="utf-8")


def render_certificate(envelope: dict, lang: str = "en", state: str = "active", printable_button: bool = True) -> str:
    if lang not in UI:
        raise ValueError(f"Unsupported language: {lang}")
    c = envelope["certificate"]
    return _env().from_string(_tpl("certificate.html.j2")).render(
        c=c, sig=envelope["signature"], t=UI[lang], lang=lang, state=state,
        qr=qr_svg(c["verify_url"]) if c.get("verify_url") else None, printable_button=printable_button)


def to_pdf(html: str, out: Path) -> Path:
    """Print an A4 landscape PDF with Chromium (requires playwright and its browser)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("PDF export requires playwright: pip install playwright && playwright install chromium") from e
    out = Path(out)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.set_content(html, wait_until="load")
        page.emulate_media(media="print")
        page.pdf(path=str(out), format="A4", landscape=True, print_background=True, prefer_css_page_size=True)
        b.close()
    return out
