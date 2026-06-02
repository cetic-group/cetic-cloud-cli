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
