"""Tests — commandes de catalogue compute (plans, templates, templates custom).

Couvre la cascade v0.22.0 : `cetic {container,vm,scale-set,vm-scale-set} plans`,
`templates`, `custom-templates`, et `cetic k8s plans|versions|templates`.

Pattern : respx (`mock_api`) qui capture l'URL + les query params appelés, +
assertions sur `result.output` (table) et sur le JSON brut (CCP_OUTPUT=json).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app


# ---------------------------------------------------------------------------
# Fixtures de payload
# ---------------------------------------------------------------------------


def _compute_plan(key: str = "small", kind: str = "compute") -> dict[str, Any]:
    return {
        "key": key,
        "name": key.title(),
        "kind": kind,
        "family": "standard",
        "cores": 2,
        "memory_mb": 2048,
        "disk_gb": 40,
        "price_eur_month": 7.99,
        "is_default": key == "small",
        "available_for_ccks": True,
    }


def _lxc_template(key: str = "debian-12") -> dict[str, Any]:
    return {"key": key, "display_name": key.replace("-", " ").title(), "is_default": key == "debian-12"}


def _custom_template(
    tid: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    template_type: str = "container",
) -> dict[str, Any]:
    return {
        "id": tid,
        "name": f"snap-{template_type}",
        "description": None,
        "template_type": template_type,
        "region": "RNN",
        "status": "ready",
        "error_message": None,
        "disk_gb": 20,
        "source_instance_id": "11111111-1111-1111-1111-111111111111",
        "source_instance_type": template_type,
        "created_at": "2026-06-05T10:00:00Z",
        "updated_at": "2026-06-05T10:05:00Z",
    }


def _k8s_template(version: str = "1.34.6", region: str = "RNN", os_label: str = "Debian 13") -> dict[str, Any]:
    return {
        "os_key": f"kube-v{version.replace('.', '-')}",
        "os_label": os_label,
        "display_name": f"{os_label} — Kubernetes v{version}",
        "k8s_version": version,
        "region": region,
        "vmid": 9100,
        "built_at": "2026-06-01T08:00:00Z",
    }


# ---------------------------------------------------------------------------
# Plans compute — kind par sous-app
# ---------------------------------------------------------------------------


def test_container_plans_filters_kind_container(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_compute_plan("small"), _compute_plan("medium")])

    mock_api.get("/v1/compute/plans").mock(side_effect=_capture)
    result = runner.invoke(app, ["container", "plans"])
    assert result.exit_code == 0, result.stdout
    assert captured["params"].get("kind") == "container"
    assert "small" in result.stdout
    assert "medium" in result.stdout


def test_vm_plans_filters_kind_vm(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_compute_plan("small")])

    mock_api.get("/v1/compute/plans").mock(side_effect=_capture)
    result = runner.invoke(app, ["vm", "plans"])
    assert result.exit_code == 0, result.stdout
    assert captured["params"].get("kind") == "vm"


def test_k8s_plans_filters_kind_k8s_node(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_compute_plan("medium", kind="compute")])

    mock_api.get("/v1/compute/plans").mock(side_effect=_capture)
    result = runner.invoke(app, ["k8s", "plans"])
    assert result.exit_code == 0, result.stdout
    assert captured["params"].get("kind") == "k8s_node"


def test_scale_set_plans_filters_kind_container(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_compute_plan("small")])

    mock_api.get("/v1/compute/plans").mock(side_effect=_capture)
    result = runner.invoke(app, ["scale-set", "plans"])
    assert result.exit_code == 0, result.stdout
    assert captured["params"].get("kind") == "container"


def test_vm_scale_set_plans_filters_kind_vm(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_compute_plan("small")])

    mock_api.get("/v1/compute/plans").mock(side_effect=_capture)
    result = runner.invoke(app, ["vm-scale-set", "plans"])
    assert result.exit_code == 0, result.stdout
    assert captured["params"].get("kind") == "vm"


def test_container_plans_json_exposes_price(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/compute/plans").mock(
        return_value=httpx.Response(200, json=[_compute_plan("small")])
    )
    result = runner.invoke(app, ["container", "plans"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data[0]["key"] == "small"
    assert data[0]["prix_mois"] == 7.99
    assert data[0]["vcpu"] == 2


# ---------------------------------------------------------------------------
# Templates système (LXC + QEMU)
# ---------------------------------------------------------------------------


def test_container_templates_default_excludes_infra(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_lxc_template("debian-12")])

    mock_api.get("/v1/templates").mock(side_effect=_capture)
    result = runner.invoke(app, ["container", "templates"])
    assert result.exit_code == 0, result.stdout
    # Pas de include_infra par défaut.
    assert "include_infra" not in captured["params"]
    assert "debian-12" in result.stdout


def test_container_templates_include_infra_flag(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_lxc_template("cl-lb-base")])

    mock_api.get("/v1/templates").mock(side_effect=_capture)
    result = runner.invoke(app, ["container", "templates", "--include-infra"])
    assert result.exit_code == 0, result.stdout
    assert captured["params"].get("include_infra") == "true"


def test_vm_templates_hits_qemu_endpoint(runner, mock_api):
    mock_api.get("/v1/qemu-templates").mock(
        return_value=httpx.Response(200, json=[{"key": "ubuntu-24-04", "display_name": "Ubuntu 24.04", "is_default": True}])
    )
    result = runner.invoke(app, ["vm", "templates"])
    assert result.exit_code == 0, result.stdout
    assert "ubuntu-24-04" in result.stdout


def test_scale_set_templates_hits_lxc_endpoint(runner, mock_api):
    mock_api.get("/v1/templates").mock(
        return_value=httpx.Response(200, json=[_lxc_template("debian-12")])
    )
    result = runner.invoke(app, ["scale-set", "templates"])
    assert result.exit_code == 0, result.stdout
    assert "debian-12" in result.stdout


def test_vm_scale_set_templates_hits_qemu_endpoint(runner, mock_api):
    mock_api.get("/v1/qemu-templates").mock(
        return_value=httpx.Response(200, json=[{"key": "ubuntu-24-04", "display_name": "Ubuntu 24.04", "is_default": True}])
    )
    result = runner.invoke(app, ["vm-scale-set", "templates"])
    assert result.exit_code == 0, result.stdout
    assert "ubuntu-24-04" in result.stdout


# ---------------------------------------------------------------------------
# Templates custom — filtre client-side par type + ID non tronqué
# ---------------------------------------------------------------------------


FULL_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_container_custom_templates_filters_container_type(runner, mock_api):
    payload = [_custom_template(FULL_ID, "container"), _custom_template("ffffffff-1111-2222-3333-444444444444", "vm")]
    mock_api.get("/v1/custom-templates").mock(return_value=httpx.Response(200, json=payload))
    result = runner.invoke(app, ["container", "custom-templates"])
    assert result.exit_code == 0, result.stdout
    # Seul le container apparaît ; pas le vm.
    assert "snap-container" in result.stdout
    assert "snap-vm" not in result.stdout
    # ID complet, jamais tronqué (table).
    assert FULL_ID in result.stdout


def test_vm_custom_templates_filters_vm_type(runner, mock_api):
    payload = [_custom_template(FULL_ID, "container"), _custom_template("ffffffff-1111-2222-3333-444444444444", "vm")]
    mock_api.get("/v1/custom-templates").mock(return_value=httpx.Response(200, json=payload))
    result = runner.invoke(app, ["vm", "custom-templates"])
    assert result.exit_code == 0, result.stdout
    assert "snap-vm" in result.stdout
    assert "snap-container" not in result.stdout


# ---------------------------------------------------------------------------
# K8s — versions + templates
# ---------------------------------------------------------------------------


def test_k8s_versions_dedups_by_version_region(runner, mock_api):
    payload = [
        _k8s_template("1.34.6", "RNN", "Debian 13"),
        _k8s_template("1.34.6", "RNN", "Ubuntu 24.04"),  # même version+région, 2 OS
        _k8s_template("1.33.2", "RNN", "Debian 13"),
    ]
    mock_api.get("/v1/k8s/templates").mock(return_value=httpx.Response(200, json=payload))
    result = runner.invoke(app, ["k8s", "versions"])
    assert result.exit_code == 0, result.stdout
    assert "1.34.6" in result.stdout
    assert "1.33.2" in result.stdout
    # Les 2 OS sont agrégés sur la ligne 1.34.6.
    assert "Debian 13" in result.stdout
    assert "Ubuntu 24.04" in result.stdout


def test_k8s_versions_region_filter_passed(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_k8s_template()])

    mock_api.get("/v1/k8s/templates").mock(side_effect=_capture)
    result = runner.invoke(app, ["k8s", "versions", "--region", "PAR"])
    assert result.exit_code == 0, result.stdout
    assert captured["params"].get("region") == "PAR"


def test_k8s_templates_shows_os_key(runner, mock_api):
    mock_api.get("/v1/k8s/templates").mock(
        return_value=httpx.Response(200, json=[_k8s_template("1.34.6", "RNN", "Debian 13")])
    )
    result = runner.invoke(app, ["k8s", "templates"])
    assert result.exit_code == 0, result.stdout
    assert "kube-v1-34-6" in result.stdout
    assert "1.34.6" in result.stdout


# ---------------------------------------------------------------------------
# Erreurs API → exit 1 + message
# ---------------------------------------------------------------------------


def test_container_plans_api_error(runner, mock_api):
    mock_api.get("/v1/compute/plans").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )
    result = runner.invoke(app, ["container", "plans"])
    assert result.exit_code == 1
    assert "boom" in result.stdout or "Erreur" in result.stdout
