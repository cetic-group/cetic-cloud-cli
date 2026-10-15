"""Tests pour `cetic ip` — allocate (label/description/quantity), update, list (colonne Nom)."""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app

IP_ID = "11111111-2222-3333-4444-555555555555"


def _ip(ip_id: str = IP_ID, address: str = "203.0.113.10", label: str | None = None,
        description: str | None = None) -> dict[str, Any]:
    return {
        "id": ip_id, "pool_id": "pool-1", "region": "RNN",
        "ip_address": address, "status": "allocated",
        "container_id": None, "vm_instance_id": None,
        "label": label, "description": description,
        "created_at": "2026-06-01T10:00:00Z",
    }


def test_allocate_with_label_description(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_ip(label=captured.get("label")))

    mock_api.post("/v1/public-ips").mock(side_effect=_handler)
    result = runner.invoke(app, [
        "ip", "allocate", "--region", "RNN",
        "--label", "passerelle-prod", "--description", "IP fixe de la passerelle",
    ])
    assert result.exit_code == 0, result.output
    assert captured["label"] == "passerelle-prod"
    assert captured["description"] == "IP fixe de la passerelle"
    assert "quantity" not in captured  # endpoint single, pas batch


def test_allocate_quantity_uses_batch(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=[
            _ip("aaaaaaaa-0000-0000-0000-000000000001", "203.0.113.11", "ip-fixe-api-1"),
            _ip("aaaaaaaa-0000-0000-0000-000000000002", "203.0.113.12", "ip-fixe-api-2"),
            _ip("aaaaaaaa-0000-0000-0000-000000000003", "203.0.113.13", "ip-fixe-api-3"),
        ])

    mock_api.post("/v1/public-ips/batch").mock(side_effect=_handler)
    result = runner.invoke(app, [
        "ip", "allocate", "--region", "RNN", "--quantity", "3", "--label", "ip-fixe-api",
    ])
    assert result.exit_code == 0, result.output
    assert captured["quantity"] == 3
    assert captured["label"] == "ip-fixe-api"
    assert "203.0.113.13" in result.output


def test_allocate_quantity_out_of_range(runner, mock_api) -> None:
    result = runner.invoke(app, ["ip", "allocate", "--region", "RNN", "--quantity", "9"])
    assert result.exit_code != 0


def test_update_label(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_ip(label="nouveau-nom"))

    mock_api.patch(f"/v1/public-ips/{IP_ID}").mock(side_effect=_handler)
    result = runner.invoke(app, ["ip", "update", IP_ID, "--label", "nouveau-nom"])
    assert result.exit_code == 0, result.output
    assert captured == {"label": "nouveau-nom"}  # description absente → non envoyée


def test_update_requires_a_flag(runner, mock_api) -> None:
    result = runner.invoke(app, ["ip", "update", IP_ID])
    assert result.exit_code != 0


def test_list_shows_label_column(runner, mock_api) -> None:
    mock_api.get("/v1/public-ips").mock(
        return_value=httpx.Response(200, json=[_ip(label="passerelle-prod")])
    )
    result = runner.invoke(app, ["ip", "list"])
    assert result.exit_code == 0, result.output
    assert "passerelle-prod" in result.output


# ── #1612 — type d'IP publique ───────────────────────────────────────────────

_POOLS = [{"id": "p1", "region": "RNN", "name": "a", "cidr": "x", "available_ips": 3,
           "type": "edge"}]


def test_allocate_with_type_sends_it(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={**_ip(), "type": "firewall"})

    mock_api.get("/v1/public-ips/pools").mock(return_value=httpx.Response(200, json=_POOLS))
    mock_api.post("/v1/public-ips").mock(side_effect=_handler)
    result = runner.invoke(app, ["ip", "allocate", "--region", "RNN", "--type", "Firewall"])
    assert result.exit_code == 0, result.output
    assert captured["type"] == "firewall"
    assert "Via firewall (cloud privé)" in result.output
    assert "pare-feu" not in result.output


def test_allocate_batch_with_type_sends_it(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=[{**_ip(), "type": "edge"}])

    mock_api.get("/v1/public-ips/pools").mock(return_value=httpx.Response(200, json=_POOLS))
    mock_api.post("/v1/public-ips/batch").mock(side_effect=_handler)
    result = runner.invoke(app, ["ip", "allocate", "-r", "RNN", "-n", "2", "-t", "edge"])
    assert result.exit_code == 0, result.output
    assert captured["type"] == "edge"
    assert captured["quantity"] == 2


def test_allocate_without_type_lets_the_platform_choose(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_ip())

    mock_api.post("/v1/public-ips").mock(side_effect=_handler)
    result = runner.invoke(app, ["ip", "allocate", "--region", "RNN"])
    assert result.exit_code == 0, result.output
    assert "type" not in captured


def test_allocate_invalid_type_is_rejected_locally(runner, mock_api) -> None:
    route = mock_api.post("/v1/public-ips")
    result = runner.invoke(app, ["ip", "allocate", "--region", "RNN", "--type", "routed"])
    assert result.exit_code == 1
    assert "--type invalide" in result.output
    assert not route.called


def test_types_aggregates_pools_in_platform_order(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/public-ips/pools").mock(return_value=httpx.Response(200, json=[
        {"id": "p1", "region": "RNN", "name": "a", "cidr": "x", "available_ips": 3, "type": "firewall"},
        {"id": "p2", "region": "RNN", "name": "b", "cidr": "y", "available_ips": 5, "type": "edge"},
        {"id": "p3", "region": "RNN", "name": "c", "cidr": "z", "available_ips": 2, "type": "firewall"},
    ]))
    result = runner.invoke(app, ["ip", "types", "--region", "RNN"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [(r["type"], r["available"], r["default"]) for r in rows] == [
        ("firewall", 5, "oui"), ("edge", 5, ""),
    ]


def test_list_shows_type_column(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/public-ips").mock(return_value=httpx.Response(200, json=[
        {**_ip(), "type": "edge"},
    ]))
    result = runner.invoke(app, ["ip", "list"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["type"] == "edge"


def test_allocate_with_type_refused_before_allocating_on_older_platform(runner, mock_api) -> None:
    """Plateforme sans types : le champ serait ignoré et une IP d'un autre type
    allouée et facturée. On refuse AVANT d'allouer."""
    mock_api.get("/v1/public-ips/pools").mock(return_value=httpx.Response(200, json=[
        {"id": "p1", "region": "RNN", "name": "a", "cidr": "x", "available_ips": 3},
    ]))
    route = mock_api.post("/v1/public-ips")
    result = runner.invoke(app, ["ip", "allocate", "-r", "RNN", "--type", "firewall"])
    assert result.exit_code == 1
    assert "ne propose pas encore le choix du type" in result.output
    assert not route.called


def test_types_on_older_platform_says_so(runner, mock_api) -> None:
    mock_api.get("/v1/public-ips/pools").mock(return_value=httpx.Response(200, json=[
        {"id": "p1", "region": "RNN", "name": "a", "cidr": "x", "available_ips": 3},
    ]))
    result = runner.invoke(app, ["ip", "types", "-r", "RNN"])
    assert result.exit_code == 0, result.output
    assert "ne propose pas encore le choix du type" in result.output
