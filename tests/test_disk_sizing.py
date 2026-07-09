"""Tests --disk-gb (create) et resize-disk (agrandissement) — issue #577.

Couvre : container, vm, ct-scale-set, vm-scale-set, k8s (cluster + pool),
registry, db (pg/mysql/redis/mongo).
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cetic.main import app


PW = "supersecret"  # 8+ chars (politique CCP)


def _capture_post(mock_api, path: str, status: int = 201, extra: dict | None = None):
    """Installe un handler POST qui capture le body et renvoie un objet minimal."""
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content) if request.content else {}
        payload = {"id": "new-id", "name": "x", "status": "creating"}
        if extra:
            payload.update(extra)
        return httpx.Response(status, json=payload)

    mock_api.post(path).mock(side_effect=_handler)
    return captured


# ---------------------------------------------------------------------------
# --disk-gb au create
# ---------------------------------------------------------------------------

CREATE_CASES = [
    (["container", "create"], "/v1/containers", "--vnet"),
    (["vm", "create"], "/v1/vm-instances", "--vnet"),
    (["ct-scale-set", "create"], "/v1/container-scale-sets", "--vnet"),
    (["vm-scale-set", "create"], "/v1/vm-scale-sets", "--vnet"),
]


def _base_args(prefix: list[str], vnet_flag: str) -> list[str]:
    return [
        *prefix,
        "--name", "demo",
        "--region", "PAR",
        vnet_flag, "vnet-123",
        "--root-password", PW,
    ]


@pytest.mark.parametrize("prefix,endpoint,vnet_flag", CREATE_CASES)
def test_disk_gb_sent_when_provided(runner, mock_api, prefix, endpoint, vnet_flag):
    captured = _capture_post(mock_api, endpoint)
    result = runner.invoke(app, [*_base_args(prefix, vnet_flag), "--disk-gb", "40"])
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["disk_gb"] == 40


@pytest.mark.parametrize("prefix,endpoint,vnet_flag", CREATE_CASES)
def test_disk_gb_absent_by_default(runner, mock_api, prefix, endpoint, vnet_flag):
    captured = _capture_post(mock_api, endpoint)
    result = runner.invoke(app, _base_args(prefix, vnet_flag))
    assert result.exit_code == 0, result.stdout
    assert "disk_gb" not in captured["body"]


def test_registry_create_storage_gb_sent_when_provided(runner, mock_api):
    captured = _capture_post(
        mock_api, "/v1/registries",
        extra={"url": "https://demo.registry.cloud.cetic-group.com"},
    )
    result = runner.invoke(
        app,
        ["registry", "create", "-n", "demo", "-r", "PAR", "--storage-gb", "100"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["storage_gb"] == 100


def test_registry_create_storage_gb_absent_by_default(runner, mock_api):
    captured = _capture_post(
        mock_api, "/v1/registries",
        extra={"url": "https://demo.registry.cloud.cetic-group.com"},
    )
    result = runner.invoke(app, ["registry", "create", "-n", "demo", "-r", "PAR"])
    assert result.exit_code == 0, result.stdout
    assert "storage_gb" not in captured["body"]


def test_k8s_create_disk_gb_goes_into_initial_pool(runner, mock_api):
    mock_api.get("/v1/k8s/templates").mock(
        return_value=httpx.Response(200, json=[
            {"os_key": "kube-v1-31-0", "os": "flatcar", "k8s_version": "v1.31.0", "region": "PAR"},
        ])
    )
    captured = _capture_post(mock_api, "/v1/k8s/clusters")
    result = runner.invoke(
        app,
        [
            "k8s", "create",
            "--name", "prod", "--region", "PAR",
            "--vpc", "vpc-1", "--vnet", "vnet-1",
            "--disk-gb", "60",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["initial_pool"]["disk_gb"] == 60


def test_k8s_pool_create_disk_gb_sent(runner, mock_api):
    captured = _capture_post(mock_api, "/v1/k8s/clusters/cl-1/node-pools")
    result = runner.invoke(
        app,
        ["k8s", "pool", "create", "cl-1", "--name", "gpu", "--disk-gb", "80"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["disk_gb"] == 80


def test_k8s_pool_create_disk_gb_absent_by_default(runner, mock_api):
    captured = _capture_post(mock_api, "/v1/k8s/clusters/cl-1/node-pools")
    result = runner.invoke(app, ["k8s", "pool", "create", "cl-1", "--name", "gpu"])
    assert result.exit_code == 0, result.stdout
    assert "disk_gb" not in captured["body"]


# ---------------------------------------------------------------------------
# resize-disk (grow-only)
# ---------------------------------------------------------------------------


def test_container_resize_disk(runner, mock_api):
    captured = _capture_post(
        mock_api, "/v1/containers/ct-1/resize-disk", extra={"status": "resizing"}
    )
    result = runner.invoke(app, ["container", "resize-disk", "ct-1", "--disk-gb", "80", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"disk_gb": 80}
    assert "resizing" in result.stdout


def test_container_resize_disk_prompts_without_yes(runner, mock_api):
    result = runner.invoke(app, ["container", "resize-disk", "ct-1", "--disk-gb", "80"], input="n\n")
    assert result.exit_code != 0
    assert not any(call.request.method == "POST" for call in mock_api.calls)


def test_vm_resize_disk(runner, mock_api):
    captured = _capture_post(
        mock_api, "/v1/vm-instances/vm-1/resize-disk", extra={"status": "resizing"}
    )
    result = runner.invoke(app, ["vm", "resize-disk", "vm-1", "--disk-gb", "100", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"disk_gb": 100}


REG_UUID = "11111111-2222-3333-4444-555555555555"


def test_registry_resize_disk(runner, mock_api):
    # `resize-disk` accepte ID|NAME — un UUID évite le lookup GET /v1/registries.
    captured = _capture_post(
        mock_api, f"/v1/registries/{REG_UUID}/resize-disk", extra={"status": "resizing"}
    )
    result = runner.invoke(
        app, ["registry", "resize-disk", REG_UUID, "--storage-gb", "200", "--yes"]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"storage_gb": 200}


DB_RESIZE_CASES = [
    (["db", "pg", "resize-disk"], "pg"),
    (["db", "mysql", "resize-disk"], "mysql"),
    (["db", "redis", "resize-disk"], "valkey"),
    (["db", "mongo", "resize-disk"], "ferretdb"),
]


@pytest.mark.parametrize("prefix,engine", DB_RESIZE_CASES)
def test_db_resize_disk(runner, mock_api, prefix, engine):
    captured = _capture_post(
        mock_api, f"/v1/db/{engine}/db-1/resize-disk", extra={"status": "resizing"}
    )
    result = runner.invoke(app, [*prefix, "db-1", "--storage-gb", "50", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"storage_gb": 50}
