"""Tests pour `cetic vpc-peering` — list / get / create / delete."""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app

PEERING_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
VPC_A_ID = "11111111-2222-3333-4444-555555555555"
VPC_B_ID = "66666666-7777-8888-9999-000000000000"


def _peering(
    peering_id: str = PEERING_ID,
    name: str = "prod-to-staging",
    vpc_a_id: str = VPC_A_ID,
    vpc_b_id: str = VPC_B_ID,
    status: str = "active",
) -> dict[str, Any]:
    return {
        "id": peering_id,
        "name": name,
        "tenant_id": "tenant-uuid",
        "vpc_a_id": vpc_a_id,
        "vpc_b_id": vpc_b_id,
        "status": status,
        "error_message": None,
        "tags": [],
        "created_at": "2026-06-24T10:00:00Z",
    }


def test_list(runner, mock_api) -> None:
    mock_api.get("/v1/vpc-peerings").mock(
        return_value=httpx.Response(200, json=[_peering()])
    )
    result = runner.invoke(app, ["vpc-peering", "list"])
    assert result.exit_code == 0, result.output
    assert "prod-to-staging" in result.output
    assert "active" in result.output


def test_get(runner, mock_api) -> None:
    mock_api.get(f"/v1/vpc-peerings/{PEERING_ID}").mock(
        return_value=httpx.Response(200, json=_peering())
    )
    result = runner.invoke(app, ["vpc-peering", "get", PEERING_ID])
    assert result.exit_code == 0, result.output
    assert "prod-to-staging" in result.output


def test_create_sends_correct_body(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_peering())

    mock_api.post("/v1/vpc-peerings").mock(side_effect=_handler)
    result = runner.invoke(app, [
        "vpc-peering", "create",
        "--name", "prod-to-staging",
        "--vpc-a", VPC_A_ID,
        "--vpc-b", VPC_B_ID,
    ])
    assert result.exit_code == 0, result.output
    assert captured == {
        "name": "prod-to-staging",
        "vpc_a_id": VPC_A_ID,
        "vpc_b_id": VPC_B_ID,
    }
    assert "créé" in result.output


def test_delete_with_yes_flag(runner, mock_api) -> None:
    mock_api.delete(f"/v1/vpc-peerings/{PEERING_ID}").mock(
        return_value=httpx.Response(202)
    )
    result = runner.invoke(app, ["vpc-peering", "delete", PEERING_ID, "--yes"])
    assert result.exit_code == 0, result.output
    assert "supprimé" in result.output


def test_list_columns(runner, mock_api) -> None:
    mock_api.get("/v1/vpc-peerings").mock(
        return_value=httpx.Response(200, json=[_peering()])
    )
    result = runner.invoke(app, ["vpc-peering", "list"])
    assert result.exit_code == 0, result.output
    # Verify column headers are present
    assert "VPC A" in result.output
    assert "VPC B" in result.output
    assert "Statut" in result.output
