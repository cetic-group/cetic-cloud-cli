"""Tests des commandes `update` (PATCH) — vm / bucket / vnet / scale sets (#545 tâche 2)."""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app

RID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
VPC = "11111111-1111-1111-1111-111111111111"


def _capture(captured: dict, status: int = 200):
    def _h(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(status, json={"id": RID, "name": "x", "is_public": True})
    return _h


def test_vm_update_body(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.patch(f"/v1/vm-instances/{RID}").mock(side_effect=_capture(cap))
    r = runner.invoke(app, ["vm", "update", RID, "--name", "web", "--tag", "prod", "--tag", "eu"])
    assert r.exit_code == 0, r.output
    assert cap == {"name": "web", "tags": ["prod", "eu"]}


def test_vm_update_nothing(runner, mock_api) -> None:
    r = runner.invoke(app, ["vm", "update", RID])
    assert r.exit_code == 0
    assert "Rien à modifier" in r.output


def test_bucket_update_public(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.patch(f"/v1/buckets/{RID}").mock(side_effect=_capture(cap))
    r = runner.invoke(app, ["bucket", "update", RID, "--public"])
    assert r.exit_code == 0, r.output
    assert cap == {"is_public": True}


def test_vnet_update_body(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.patch(f"/v1/vpcs/{VPC}/vnets/{RID}").mock(side_effect=_capture(cap))
    r = runner.invoke(app, ["vpc", "vnet", "update", VPC, RID, "--name", "db", "--no-snat"])
    assert r.exit_code == 0, r.output
    assert cap == {"name": "db", "snat": False}


def test_ct_scale_set_update_body(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.patch(f"/v1/container-scale-sets/{RID}").mock(side_effect=_capture(cap))
    r = runner.invoke(app, ["ct-scale-set", "update", RID, "--min", "2", "--max", "8", "--desired", "4"])
    assert r.exit_code == 0, r.output
    assert cap == {"min_instances": 2, "max_instances": 8, "desired_instances": 4}


def test_vm_scale_set_update_body(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.patch(f"/v1/vm-scale-sets/{RID}").mock(side_effect=_capture(cap))
    r = runner.invoke(app, ["vm-scale-set", "update", RID, "--name", "fleet", "--auto-repair", "--tag", "x"])
    assert r.exit_code == 0, r.output
    assert cap == {"name": "fleet", "auto_repair": True, "tags": ["x"]}


def test_container_update_body(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.patch(f"/v1/containers/{RID}").mock(side_effect=_capture(cap))
    r = runner.invoke(app, ["container", "update", RID, "--name", "api", "--tag", "prod"])
    assert r.exit_code == 0, r.output
    assert cap == {"name": "api", "tags": ["prod"]}


def test_vpc_update_body(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.patch(f"/v1/vpcs/{RID}").mock(side_effect=_capture(cap))
    r = runner.invoke(app, ["vpc", "update", RID, "--name", "prod-vpc", "--tag", "a", "--tag", "b"])
    assert r.exit_code == 0, r.output
    assert cap == {"name": "prod-vpc", "tags": ["a", "b"]}
