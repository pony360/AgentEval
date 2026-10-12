# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The AgentEval Authors
"""Read-only Kubernetes collector.

Calls only list/get APIs; see deploy/k8s/scanner-rbac.yaml for the required permissions.
Note: the collector never reads Secret contents; plaintext-secret detection relies only on variable names with env.value in Pod templates.
"""

from __future__ import annotations

from typing import Any


def _load_client(context: str | None, in_cluster: bool):
    try:
        from kubernetes import client, config
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("The kubernetes dependency is not installed; run: pip install 'agenteval[kubernetes]'") from e
    if in_cluster:
        config.load_incluster_config()
    else:
        config.load_kube_config(context=context)
    return client


def _items(api_client, resp) -> list[dict[str, Any]]:
    return [api_client.sanitize_for_serialization(i) for i in resp.items]


def fetch_snapshot(context: str | None = None, namespaces: list[str] | None = None,
                   in_cluster: bool = False) -> dict[str, Any]:
    client = _load_client(context, in_cluster)
    api_client = client.ApiClient()
    apps = client.AppsV1Api(api_client)
    core = client.CoreV1Api(api_client)
    net = client.NetworkingV1Api(api_client)

    def gather(all_ns_fn, ns_fn, kind: str | None = None):
        if namespaces:
            out = []
            for ns in namespaces:
                out.extend(_items(api_client, ns_fn(ns)))
        else:
            out = _items(api_client, all_ns_fn())
        if kind:
            for o in out:
                o.setdefault("kind", kind)
        return out

    workloads = (
        gather(apps.list_deployment_for_all_namespaces, apps.list_namespaced_deployment, "Deployment")
        + gather(apps.list_stateful_set_for_all_namespaces, apps.list_namespaced_stateful_set, "StatefulSet")
    )

    cluster_name = context
    if not cluster_name:
        try:
            from kubernetes import config
            _, active = config.list_kube_config_contexts()
            cluster_name = (active or {}).get("context", {}).get("cluster")
        except Exception:
            cluster_name = None

    return {
        "cluster": {"name": cluster_name or "in-cluster"},
        "workloads": workloads,
        "services": gather(core.list_service_for_all_namespaces, core.list_namespaced_service),
        "ingresses": gather(net.list_ingress_for_all_namespaces, net.list_namespaced_ingress),
        "network_policies": gather(net.list_network_policy_for_all_namespaces, net.list_namespaced_network_policy),
    }
