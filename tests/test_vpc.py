"""Tests pour `cetic vpc` — create --cidr (body POST) + colonne CIDR en list."""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app

VPC_ID = "11111111-2222-3333-4444-555555555555"


def _vpc(vpc_id: str = VPC_ID, name: str = "prod", region: str = "RNN",
         cidr: str | None = None) -> dict[str, Any]:
    return {
        "id": vpc_id, "name": name, "region": region,
        "cidr": cidr, "status": "active", "vnets": [],
    }


def test_create_with_cidr(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_vpc(cidr=captured.get("cidr")))

    mock_api.post("/v1/vpcs").mock(side_effect=_handler)
    result = runner.invoke(app, [
        "vpc", "create", "--name", "prod", "--region", "RNN", "--cidr", "10.10.0.0/16",
    ])
    assert result.exit_code == 0, result.output
    assert captured == {"name": "prod", "region": "RNN", "cidr": "10.10.0.0/16"}


def test_create_without_cidr_omits_field(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_vpc())

    mock_api.post("/v1/vpcs").mock(side_effect=_handler)
    result = runner.invoke(app, ["vpc", "create", "--name", "prod", "--region", "RNN"])
    assert result.exit_code == 0, result.output
    assert captured == {"name": "prod", "region": "RNN"}  # cidr absent → non envoyé


def test_list_shows_cidr_column(runner, mock_api) -> None:
    mock_api.get("/v1/vpcs").mock(return_value=httpx.Response(200, json=[
        _vpc(cidr="10.10.0.0/16"),
        _vpc("66666666-7777-8888-9999-000000000000", "staging", cidr=None),
    ]))
    result = runner.invoke(app, ["vpc", "list"])
    assert result.exit_code == 0, result.output
    assert "CIDR" in result.output
    assert "10.10.0.0/16" in result.output
