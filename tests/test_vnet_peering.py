"""Tests pour `cetic vnet-peering` — list / get / create / delete."""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app

PEERING_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
VNET_A_ID = "11111111-2222-3333-4444-555555555555"
VNET_B_ID = "66666666-7777-8888-9999-000000000000"


def _peering(status: str = "active") -> dict[str, Any]:
    return {
        "id": PEERING_ID,
        "name": "web-to-db",
        "tenant_id": "tenant-uuid",
        "vnet_a_id": VNET_A_ID,
        "vnet_b_id": VNET_B_ID,
        "vnet_a_name": "web",
        "vnet_b_name": "db",
        "vnet_a_cidr": "10.0.0.0/24",
        "vnet_b_cidr": "10.1.0.0/24",
        "vpc_a_name": "vpc-prod",
        "vpc_b_name": "vpc-data",
        "status": status,
        "error_message": None,
        "tags": [],
        "created_at": "2026-06-25T10:00:00Z",
    }


def test_list(runner, mock_api) -> None:
    mock_api.get("/v1/vnet-peerings").mock(
        return_value=httpx.Response(200, json=[_peering()])
    )
    result = runner.invoke(app, ["vnet-peering", "list"])
    assert result.exit_code == 0, result.output
    assert "web-to-db" in result.output
    assert "active" in result.output


def test_get(runner, mock_api) -> None:
    mock_api.get(f"/v1/vnet-peerings/{PEERING_ID}").mock(
        return_value=httpx.Response(200, json=_peering())
    )
    result = runner.invoke(app, ["vnet-peering", "get", PEERING_ID])
    assert result.exit_code == 0, result.output
    assert "web-to-db" in result.output


def test_create_sends_correct_body(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_peering())

    mock_api.post("/v1/vnet-peerings").mock(side_effect=_handler)
    result = runner.invoke(app, [
        "vnet-peering", "create",
        "--name", "web-to-db",
        "--vnet-a", VNET_A_ID,
        "--vnet-b", VNET_B_ID,
    ])
    assert result.exit_code == 0, result.output
    assert captured == {
        "name": "web-to-db",
        "vnet_a_id": VNET_A_ID,
        "vnet_b_id": VNET_B_ID,
    }
    assert "créé" in result.output


def test_delete_with_yes_flag(runner, mock_api) -> None:
    mock_api.delete(f"/v1/vnet-peerings/{PEERING_ID}").mock(
        return_value=httpx.Response(202)
    )
    result = runner.invoke(app, ["vnet-peering", "delete", PEERING_ID, "--yes"])
    assert result.exit_code == 0, result.output
    assert "supprimé" in result.output
