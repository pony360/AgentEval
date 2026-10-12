# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Normalize a snapshot of raw Kubernetes objects into assets and facts (pure functions, easy to test offline).

Snapshot format (same as the items field of `kubectl get -o json`, camelCase):
{
  "cluster": {"name": "prod-gpu-1"},
  "workloads": [Deployment/StatefulSet ...],
  "services": [...], "ingresses": [...], "network_policies": [...]
}

Security note: this module records only the *names* of sensitive environment variables and never writes their values to facts or reports.
"""

from __future__ import annotations

import os
import re
from typing import Any

from agenteval.models import Asset

# Label/annotation prefix for AgentEval hints, e.g. agenteval.io/auth: oidc.
# Override with AGENTEVAL_ANNOTATION_PREFIX (a trailing "/" is added if missing).
ANN = os.environ.get("AGENTEVAL_ANNOTATION_PREFIX", "agenteval.io/").rstrip("/") + "/"

SERVER_PATTERNS: dict[str, str] = {
    "vllm": r"vllm",
    "tgi": r"text-generation-inference",
    "triton": r"tritonserver",
    "ollama": r"ollama",
    "llamacpp": r"llama[.\-_]?cpp",
    "sglang": r"sglang",
    "ray-serve": r"rayproject/ray",
    "kserve": r"kserve",
}
GENERATIVE_SERVERS = {"vllm", "tgi", "ollama", "llamacpp", "sglang"}
AUTH_SIDECAR = re.compile(r"oauth2-proxy|envoy|kong|authz|istio/proxy", re.I)
GUARD_SIDECAR = re.compile(r"guard|guardrails|nemo-guardrails", re.I)
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)


# ---------------- Helpers ----------------

def _meta(obj: dict) -> dict:
    return obj.get("metadata") or {}


def _labels_match(selector: dict | None, labels: dict, empty_matches_all: bool) -> bool:
    """Label subset matching.

    - Service.spec.selector is a flat dict; empty/missing selects no Pods;
    - NetworkPolicy.spec.podSelector is a LabelSelector; {} selects all Pods;
    - selectors with matchExpressions are treated as non-matching for now (conservative; full support in Phase 2).
    """
    if not selector:
        return empty_matches_all and selector is not None
    if selector.get("matchExpressions"):
        return False
    match = (selector.get("matchLabels") or {}) if "matchLabels" in selector else selector
    if not match:
        return empty_matches_all
    return all(labels.get(k) == v for k, v in match.items())


def _image_tag(ref: str) -> str | None:
    if "@" in ref:
        return None
    last = ref.rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else None


def detect_server(images: list[str]) -> str | None:
    for img in images:
        for server, pat in SERVER_PATTERNS.items():
            if re.search(pat, img, re.I):
                return server
    return None


def _model_from_args(container: dict) -> str | None:
    args = (container.get("command") or []) + (container.get("args") or [])
    for i, a in enumerate(args):
        if a in ("--model", "--model-id", "--model_name") and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--model=") or a.startswith("--model-id="):
            return a.split("=", 1)[1]
        if a == "serve" and i + 1 < len(args) and not args[i + 1].startswith("-"):
            return args[i + 1]
    for e in container.get("env") or []:
        if e.get("name") in ("MODEL_ID", "MODEL_NAME", "MODEL") and e.get("value"):
            return e["value"]
    return None


# ---------------- Main logic ----------------

def _workload_to_asset(wl: dict, services: list[dict], ingresses: list[dict],
                       netpols: list[dict]) -> Asset | None:
    meta = _meta(wl)
    ns = meta.get("namespace", "default")
    name = meta.get("name", "unknown")
    tpl = (wl.get("spec") or {}).get("template") or {}
    tpl_meta = _meta(tpl)
    pod_spec = tpl.get("spec") or {}
    containers = pod_spec.get("containers") or []
    pod_labels = tpl_meta.get("labels") or {}
    labels = {**(meta.get("labels") or {}), **pod_labels}
    ann = {**(meta.get("annotations") or {}), **(tpl_meta.get("annotations") or {})}

    images = [c.get("image", "") for c in containers + (pod_spec.get("initContainers") or [])]
    server = detect_server(images)
    if not server and labels.get(ANN + "inference") != "true":
        return None  # not an inference workload

    # --- Authentication ---
    auth_method = ann.get(ANN + "auth")
    auth_enabled = bool(auth_method and auth_method.lower() != "none")
    if not auth_method:
        for c in containers:
            args = (c.get("command") or []) + (c.get("args") or [])
            env_names = {e.get("name") for e in c.get("env") or []}
            if any(a == "--api-key" or a.startswith("--api-key=") for a in args) or \
                    env_names & {"VLLM_API_KEY", "API_KEY"}:
                auth_enabled, auth_method = True, "api-key"
            elif AUTH_SIDECAR.search(c.get("image", "")):
                auth_enabled, auth_method = True, "proxy-sidecar"

    # --- Exposure and TLS ---
    svcs = [s for s in services if _meta(s).get("namespace") == ns
            and _labels_match((s.get("spec") or {}).get("selector"), pod_labels, empty_matches_all=False)]
    svc_names = {_meta(s).get("name") for s in svcs}
    svc_types = sorted({(s.get("spec") or {}).get("type", "ClusterIP") for s in svcs})
    ings = []
    for ing in ingresses:
        if _meta(ing).get("namespace") != ns:
            continue
        backends = set()
        for rule in (ing.get("spec") or {}).get("rules") or []:
            for p in ((rule.get("http") or {}).get("paths")) or []:
                backends.add((((p.get("backend") or {}).get("service")) or {}).get("name"))
        if backends & svc_names:
            ings.append(ing)
    public = bool(ings) or any(t in ("LoadBalancer", "NodePort") for t in svc_types)
    hosts = sorted({r.get("host") for i in ings for r in (i.get("spec") or {}).get("rules") or [] if r.get("host")})
    if ings:
        tls_enabled = all((i.get("spec") or {}).get("tls") for i in ings)
    else:
        tls_enabled = ann.get(ANN + "tls") == "true"

    # --- NetworkPolicy ---
    np_present = any(
        _meta(np).get("namespace") == ns and _labels_match((np.get("spec") or {}).get("podSelector", {}), pod_labels, empty_matches_all=True)
        for np in netpols
    )

    # --- Images ---
    main_images = [c.get("image", "") for c in containers]
    digest_pinned = all("@sha256:" in i for i in main_images) if main_images else False
    any_latest = any(_image_tag(i) in (None, "latest") and "@sha256:" not in i for i in main_images)

    # --- SecurityContext ---
    pod_sc = pod_spec.get("securityContext") or {}
    def _non_root(c):
        sc = c.get("securityContext") or {}
        if sc.get("runAsNonRoot") is True or pod_sc.get("runAsNonRoot") is True:
            return True
        uid = sc.get("runAsUser", pod_sc.get("runAsUser"))
        return isinstance(uid, int) and uid > 0
    run_as_non_root = all(_non_root(c) for c in containers) if containers else False
    privileged = any((c.get("securityContext") or {}).get("privileged") is True for c in containers)
    # Kubernetes allows privilege escalation by default; it must be explicitly set to false
    allow_pe = any((c.get("securityContext") or {}).get("allowPrivilegeEscalation") is not False for c in containers)

    # --- Plaintext secrets (names only) ---
    plaintext = sorted({
        f"{c.get('name')}:{e.get('name')}"
        for c in containers for e in c.get("env") or []
        if e.get("value") and SECRET_NAME.search(e.get("name", ""))
    })

    # --- Resource limits ---
    limits_set = all(((c.get("resources") or {}).get("limits") or {}).get("memory") for c in containers) \
        if containers else False

    # --- Content safety ---
    cs = ann.get(ANN + "content-safety")
    cs_enabled = bool(cs and cs.lower() != "none")
    if not cs:
        cs_enabled = any(GUARD_SIDECAR.search(i) for i in main_images[1:])
        cs = "sidecar" if cs_enabled else None

    model_name = ann.get(ANN + "model") or next((m for c in containers if (m := _model_from_args(c))), None)

    facts: dict[str, Any] = {
        "kind": wl.get("kind", "Deployment"),
        "server": server or "custom",
        "replicas": (wl.get("spec") or {}).get("replicas"),
        "owner": labels.get(ANN + "owner") or labels.get("owner") or labels.get("team"),
        "risk_tier": ann.get(ANN + "risk-tier", "standard"),
        "customer_facing": ann.get(ANN + "customer-facing") == "true",
        "ai_disclosure": {"enabled": ann.get(ANN + "ai-disclosure") == "true"},
        "model": {
            "name": model_name,
            "source": ann.get(ANN + "model-source"),
            "card_url": ann.get(ANN + "model-card"),
            "provenance_verified": ann.get(ANN + "provenance-verified") == "true",
            "generative": (server in GENERATIVE_SERVERS) or ann.get(ANN + "generative") == "true",
            "fine_tuned": ann.get(ANN + "fine-tuned") == "true",
        },
        "auth": {"enabled": auth_enabled, "method": auth_method},
        "exposure": {"public": public, "service_types": svc_types, "ingress_hosts": hosts},
        "tls": {"enabled": tls_enabled},
        "network_policy": {"present": np_present},
        "image": {"refs": main_images, "all_digest_pinned": digest_pinned, "any_latest": any_latest},
        "security_context": {
            "run_as_non_root": run_as_non_root,
            "privileged": privileged,
            "allow_privilege_escalation": allow_pe,
        },
        "secrets": {"plaintext_env": plaintext},
        "resources": {"limits_set": limits_set},
        "logging": {"request_logging": ann.get(ANN + "request-logging") == "true",
                    "pii_redaction": ann.get(ANN + "log-redaction") == "true"},
        "monitoring": {"enabled": ann.get("prometheus.io/scrape") == "true" or ann.get(ANN + "monitoring") == "true"},
        "content_safety": {"enabled": cs_enabled, "method": cs},
    }
    kind = facts["kind"].lower()
    return Asset(id=f"{kind}/{ns}/{name}", type="inference_service", name=name, namespace=ns, facts=facts)


def normalize_snapshot(snapshot: dict) -> list[Asset]:
    services = snapshot.get("services") or []
    ingresses = snapshot.get("ingresses") or []
    netpols = snapshot.get("network_policies") or []
    assets = [a for wl in snapshot.get("workloads") or []
              if (a := _workload_to_asset(wl, services, ingresses, netpols)) is not None]

    cluster_name = (snapshot.get("cluster") or {}).get("name", "cluster")
    cluster = Asset(
        id=f"cluster/{cluster_name}", type="cluster", name=cluster_name,
        facts={
            "inference_service_count": len(assets),
            "namespaces": sorted({a.namespace for a in assets if a.namespace}),
        },
    )
    return [cluster, *assets]
