"""Tests pour `cetic lb` — listeners HTTPS + Let's Encrypt au create,
acme-providers/acme-retry, gestion backends.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cetic.commands.lb import _parse_backend_spec, _parse_credentials
from cetic.main import app

LB_ID = "11111111-2222-3333-4444-555555555555"
LISTENER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BACKEND_ID = "99999999-8888-7777-6666-555555555555"
VNET_ID = "22222222-3333-4444-5555-666666666666"
CT_ID = "33333333-4444-5555-6666-777777777777"


def _lb(lb_id: str = LB_ID, *, listeners: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": lb_id,
        "name": "web-lb",
        "region": "RNN",
        "vnet_id": VNET_ID,
        "plan": "small",
        "vip_address": "10.0.0.10",
        "public_ip_address": None,
        "public_ip_id": None,
        "status": "provisioning",
        "error_message": None,
        "tags": [],
        "listeners": listeners or [],
        "created_at": "2026-06-01T10:00:00Z",
        "updated_at": "2026-06-01T10:00:00Z",
    }


def _listener(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": LISTENER_ID,
        "protocol": "https",
        "listen_port": 443,
        "algorithm": "roundrobin",
        "health_check_enabled": True,
        "health_check_path": None,
        "backends": [],
        "domain": "www.example.com",
        "acme_challenge": "http01",
        "acme_status": "pending",
        "acme_last_error": None,
        "acme_dns_provider": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1-2. create avec listener https + ACME
# ---------------------------------------------------------------------------


def test_create_https_listener_acme_http01(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_lb(listeners=[_listener()]))

    mock_api.post("/v1/load-balancers").mock(side_effect=_handler)
    result = runner.invoke(app, [
        "lb", "create", "--name", "web-lb", "--region", "RNN", "--vnet", VNET_ID,
        "--listener-protocol", "https", "--listener-port", "443",
        "--domain", "www.example.com", "--acme-challenge", "http01",
    ])
    assert result.exit_code == 0, result.output
    listeners = captured["listeners"]
    assert len(listeners) == 1
    ls = listeners[0]
    assert ls["protocol"] == "https"
    assert ls["listen_port"] == 443
    assert ls["domain"] == "www.example.com"
    assert ls["acme_challenge"] == "http01"
    # http01 → pas de provider/credentials DNS
    assert "acme_dns_provider" not in ls
    assert "acme_dns_credentials" not in ls


def test_create_https_listener_acme_dns01(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_lb(listeners=[_listener(acme_challenge="dns01")]))

    mock_api.post("/v1/load-balancers").mock(side_effect=_handler)
    result = runner.invoke(app, [
        "lb", "create", "--name", "web-lb", "--region", "RNN", "--vnet", VNET_ID,
        "--listener-protocol", "https", "--listener-port", "443",
        "--domain", "www.example.com", "--acme-challenge", "dns01",
        "--acme-dns-provider", "cloudflare",
        "--acme-dns-credential", "api_token=xyz123",
    ])
    assert result.exit_code == 0, result.output
    ls = captured["listeners"][0]
    assert ls["acme_challenge"] == "dns01"
    assert ls["acme_dns_provider"] == "cloudflare"
    assert ls["acme_dns_credentials"] == {"api_token": "xyz123"}


# ---------------------------------------------------------------------------
# 3-4. validations
# ---------------------------------------------------------------------------


def test_create_listener_protocol_without_port_errors(runner, mock_api) -> None:
    result = runner.invoke(app, [
        "lb", "create", "--name", "web-lb", "--region", "RNN", "--vnet", VNET_ID,
        "--listener-protocol", "https",
    ])
    assert result.exit_code != 0


def test_create_acme_challenge_requires_https(runner, mock_api) -> None:
    result = runner.invoke(app, [
        "lb", "create", "--name", "web-lb", "--region", "RNN", "--vnet", VNET_ID,
        "--listener-protocol", "http", "--listener-port", "80",
        "--domain", "www.example.com", "--acme-challenge", "http01",
    ])
    assert result.exit_code != 0
    assert "https" in result.output.lower()


def test_create_dns01_requires_provider_and_credential(runner, mock_api) -> None:
    result = runner.invoke(app, [
        "lb", "create", "--name", "web-lb", "--region", "RNN", "--vnet", VNET_ID,
        "--listener-protocol", "https", "--listener-port", "443",
        "--domain", "www.example.com", "--acme-challenge", "dns01",
    ])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# 5-6. helpers purs
# ---------------------------------------------------------------------------


def test_parse_backend_spec_container() -> None:
    assert _parse_backend_spec(f"container:{CT_ID}:8080") == {
        "container_id": CT_ID, "port": 8080, "weight": 1,
    }


def test_parse_backend_spec_vm_with_weight() -> None:
    assert _parse_backend_spec(f"vm:{CT_ID}:3000:5") == {
        "vm_instance_id": CT_ID, "port": 3000, "weight": 5,
    }


def test_parse_backend_spec_invalid_raises() -> None:
    with pytest.raises(ValueError):
        _parse_backend_spec("bogus:format")
    with pytest.raises(ValueError):
        _parse_backend_spec(f"container:{CT_ID}:notaport")


def test_parse_credentials_valid() -> None:
    assert _parse_credentials(["api_token=abc", "zone=def"]) == {
        "api_token": "abc", "zone": "def",
    }


def test_parse_credentials_invalid_raises() -> None:
    with pytest.raises(ValueError):
        _parse_credentials(["no_equals_sign"])


# ---------------------------------------------------------------------------
# 7-8. acme commands
# ---------------------------------------------------------------------------


def test_acme_providers_renders_catalog(runner, mock_api) -> None:
    mock_api.get("/v1/load-balancers/acme/dns-providers").mock(
        return_value=httpx.Response(200, json={
            "cloudflare": {"label": "Cloudflare", "fields": ["api_token"]},
            "ovh": {"label": "OVH", "fields": ["application_key", "consumer_key"]},
        })
    )
    result = runner.invoke(app, ["lb", "acme-providers"])
    assert result.exit_code == 0, result.output
    assert "Cloudflare" in result.output
    assert "cloudflare" in result.output


def test_acme_retry_posts_correct_path(runner, mock_api) -> None:
    called: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        called["url"] = str(request.url)
        return httpx.Response(200, json=_lb(listeners=[_listener()]))

    mock_api.post(
        f"/v1/load-balancers/{LB_ID}/listeners/{LISTENER_ID}/acme/retry"
    ).mock(side_effect=_handler)
    result = runner.invoke(app, ["lb", "acme-retry", LB_ID, LISTENER_ID])
    assert result.exit_code == 0, result.output
    assert f"/listeners/{LISTENER_ID}/acme/retry" in called["url"]


# ---------------------------------------------------------------------------
# 9. backend management
# ---------------------------------------------------------------------------


def test_backend_add_container(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_lb(listeners=[_listener()]))

    mock_api.post(
        f"/v1/load-balancers/{LB_ID}/listeners/{LISTENER_ID}/backends"
    ).mock(side_effect=_handler)
    result = runner.invoke(app, [
        "lb", "backend", "add", LB_ID, LISTENER_ID,
        "--container", CT_ID, "--port", "8080",
    ])
    assert result.exit_code == 0, result.output
    assert captured["container_id"] == CT_ID
    assert captured["port"] == 8080
    assert "vm_instance_id" not in captured


def test_backend_add_requires_exactly_one_target(runner, mock_api) -> None:
    result = runner.invoke(app, [
        "lb", "backend", "add", LB_ID, LISTENER_ID, "--port", "8080",
    ])
    assert result.exit_code != 0


def test_backend_update_sends_patch(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_lb(listeners=[_listener()]))

    mock_api.patch(
        f"/v1/load-balancers/{LB_ID}/listeners/{LISTENER_ID}/backends/{BACKEND_ID}"
    ).mock(side_effect=_handler)
    result = runner.invoke(app, [
        "lb", "backend", "update", LB_ID, LISTENER_ID, BACKEND_ID,
        "--weight", "10",
    ])
    assert result.exit_code == 0, result.output
    assert captured == {"weight": 10}


def test_backend_remove_with_yes_calls_delete(runner, mock_api) -> None:
    called: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        called["method"] = request.method
        return httpx.Response(204)

    mock_api.delete(
        f"/v1/load-balancers/{LB_ID}/listeners/{LISTENER_ID}/backends/{BACKEND_ID}"
    ).mock(side_effect=_handler)
    result = runner.invoke(app, [
        "lb", "backend", "remove", LB_ID, LISTENER_ID, BACKEND_ID, "--yes",
    ])
    assert result.exit_code == 0, result.output
    assert called["method"] == "DELETE"


# ---------------------------------------------------------------------------
# 10. lb get shows listeners
# ---------------------------------------------------------------------------


def test_get_shows_listener_table(runner, mock_api) -> None:
    ls = _listener(backends=[{"id": BACKEND_ID, "container_id": CT_ID,
                              "vm_instance_id": None, "port": 8080, "weight": 1}])
    mock_api.get(f"/v1/load-balancers/{LB_ID}").mock(
        return_value=httpx.Response(200, json=_lb(listeners=[ls]))
    )
    result = runner.invoke(app, ["lb", "get", LB_ID])
    assert result.exit_code == 0, result.output
    assert "443" in result.output
    assert "https" in result.output
    assert "www.example.com" in result.output
