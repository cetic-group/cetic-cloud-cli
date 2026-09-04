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


# ---------------------------------------------------------------------------
# VNets — colonne « Sortie » (mode de sortie internet), issue cetic-cloud-cli#48
# ---------------------------------------------------------------------------


def _vnet(vnet_id: str, name: str, *, snat: bool) -> dict[str, Any]:
    return {"id": vnet_id, "vpc_id": VPC_ID, "name": name,
            "cidr": "10.0.0.0/24", "snat": snat}


def test_vnet_list_shows_egress_labels(runner, mock_api) -> None:
    mock_api.get(f"/v1/vpcs/{VPC_ID}/vnets").mock(return_value=httpx.Response(200, json=[
        _vnet("aaaaaaaa-0000-0000-0000-000000000001", "bureau", snat=True),
        _vnet("aaaaaaaa-0000-0000-0000-000000000002", "atelier", snat=False),
    ]))
    result = runner.invoke(app, ["vpc", "vnet", "list", VPC_ID])
    assert result.exit_code == 0, result.output
    assert "Sortie" in result.output
    assert "Sortie internet" in result.output
    assert "Réseau isolé" in result.output
    # Le jargon `SNAT` ne sort plus côté client.
    assert "SNAT" not in result.output


def test_vnet_list_json_keeps_raw_snat_boolean(runner, monkeypatch, mock_api) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get(f"/v1/vpcs/{VPC_ID}/vnets").mock(return_value=httpx.Response(200, json=[
        _vnet("aaaaaaaa-0000-0000-0000-000000000002", "atelier", snat=False),
    ]))
    result = runner.invoke(app, ["vpc", "vnet", "list", VPC_ID])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data[0]["snat"] is False
    assert data[0]["egress"] == "Réseau isolé"


def test_fmt_egress_labels() -> None:
    from cetic.commands.vpc import fmt_egress

    assert fmt_egress(True) == "Sortie internet"
    assert fmt_egress(False) == "Réseau isolé"
    # `snat` absent de la réponse → on n'annonce pas une sortie qui n'est pas dite.
    assert fmt_egress(None) == "Réseau isolé"
