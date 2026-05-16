"""Tests pour `cetic appgw` — couvre les 16 commandes + erreurs API.

Couverture :
- 16 commandes (list/get/create/delete/attach-ip/detach-ip/health
  + listener add/list/delete/renew-cert
  + tg create/list/delete + tg member add/remove
  + route create/list/delete)
- Happy path + 1 cas erreur par commande (404, 422, validation locale)
- Outputs : table / json / yaml
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app


# ---------------------------------------------------------------------------
# Fixtures de payloads
# ---------------------------------------------------------------------------

GW_ID = "11111111-2222-3333-4444-555555555555"
GW2_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
LISTENER_ID = "22222222-3333-4444-5555-666666666666"
TG_ID = "33333333-4444-5555-6666-777777777777"
MEMBER_ID = "44444444-5555-6666-7777-888888888888"
ROUTE_ID = "55555555-6666-7777-8888-999999999999"
PUBLIC_IP_ID = "66666666-7777-8888-9999-aaaaaaaaaaaa"
VPC_ID = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"
VNET_ID = "88888888-9999-aaaa-bbbb-cccccccccccc"
CONTAINER_ID = "99999999-aaaa-bbbb-cccc-dddddddddddd"
VM_ID = "abcdef00-1111-2222-3333-444444444444"


def _gw_payload(gid: str = GW_ID, name: str = "web-edge", **overrides: Any) -> dict[str, Any]:
    base = {
        "id": gid,
        "name": name,
        "region": "RNN",
        "plan": "small",
        "vpc_id": VPC_ID,
        "vnet_id": VNET_ID,
        "status": "active",
        "public_ip_address": "203.0.113.10",
        "vip_address": "10.0.0.10",
        "listener_count": 2,
        "route_count": 3,
        "force_https": True,
        "hsts_enabled": False,
        "tags": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Top-level CRUD
# ---------------------------------------------------------------------------


def test_list_happy_path(runner, mock_api):
    mock_api.get("/v1/app-gateways").mock(
        return_value=httpx.Response(
            200,
            json=[_gw_payload(), _gw_payload(GW2_ID, "api-gw", plan="medium")],
        )
    )
    result = runner.invoke(app, ["appgw", "list"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "web-edge" in result.stdout
    assert "api-gw" in result.stdout
    assert "Application Gateways (2)" in result.stdout


def test_list_filters_by_region(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_gw_payload()])

    mock_api.get("/v1/app-gateways").mock(side_effect=_capture)
    result = runner.invoke(app, ["appgw", "list", "--region", "PAR"])
    assert result.exit_code == 0, result.stdout
    assert captured["params"] == {"region": "PAR"}


def test_list_json_format(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/app-gateways").mock(
        return_value=httpx.Response(200, json=[_gw_payload()])
    )
    result = runner.invoke(app, ["appgw", "list"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed[0]["name"] == "web-edge"


def test_list_yaml_format(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "yaml")
    mock_api.get("/v1/app-gateways").mock(
        return_value=httpx.Response(200, json=[_gw_payload()])
    )
    result = runner.invoke(app, ["appgw", "list"])
    assert result.exit_code == 0
    assert "name: web-edge" in result.stdout


def test_list_500_error(runner, mock_api):
    mock_api.get("/v1/app-gateways").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )
    result = runner.invoke(app, ["appgw", "list"])
    assert result.exit_code == 1
    assert "Erreur serveur" in result.stdout


def test_get_by_uuid(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}").mock(
        return_value=httpx.Response(200, json=_gw_payload())
    )
    result = runner.invoke(app, ["appgw", "get", GW_ID])
    assert result.exit_code == 0, result.stdout
    assert "web-edge" in result.stdout


def test_get_by_name_resolves(runner, mock_api):
    # Le resolve_id va lister la collection pour trouver le name
    mock_api.get("/v1/app-gateways").mock(
        return_value=httpx.Response(200, json=[_gw_payload()])
    )
    mock_api.get(f"/v1/app-gateways/{GW_ID}").mock(
        return_value=httpx.Response(200, json=_gw_payload())
    )
    result = runner.invoke(app, ["appgw", "get", "web-edge"])
    assert result.exit_code == 0, result.stdout
    assert "web-edge" in result.stdout


def test_get_404_french(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(app, ["appgw", "get", GW_ID])
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


def test_create_happy_path(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_gw_payload(status="creating"))

    mock_api.post("/v1/app-gateways").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "create",
            "--name", "web-edge",
            "--region", "RNN",
            "--plan", "small",
            "--vpc", VPC_ID,
            "--vnet", VNET_ID,
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert captured["body"]["name"] == "web-edge"
    assert captured["body"]["region"] == "RNN"
    assert captured["body"]["plan"] == "small"
    assert captured["body"]["vpc_id"] == VPC_ID
    assert captured["body"]["vnet_id"] == VNET_ID
    assert "public_ip_id" not in captured["body"]


def test_create_with_public_ip(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_gw_payload())

    mock_api.post("/v1/app-gateways").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "create",
            "-n", "api-gw", "-r", "PAR", "-p", "medium",
            "--vpc", VPC_ID, "--vnet", VNET_ID,
            "--public-ip", PUBLIC_IP_ID,
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["public_ip_id"] == PUBLIC_IP_ID


def test_create_quota_409_french(runner, mock_api):
    mock_api.post("/v1/app-gateways").mock(
        return_value=httpx.Response(
            409, json={"detail": "max_app_gateways=2 limit reached"}
        )
    )
    result = runner.invoke(
        app,
        [
            "appgw", "create",
            "-n", "x", "-r", "RNN",
            "--vpc", VPC_ID, "--vnet", VNET_ID,
        ],
    )
    assert result.exit_code == 1
    assert "Quota atteint" in result.stdout


def test_delete_with_yes_flag(runner, mock_api):
    mock_api.delete(f"/v1/app-gateways/{GW_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["appgw", "delete", GW_ID, "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "supprimée" in result.stdout


def test_delete_aborted_when_no(runner, mock_api):
    result = runner.invoke(app, ["appgw", "delete", GW_ID], input="n\n")
    assert result.exit_code != 0  # Abort
    assert not any(call.request.method == "DELETE" for call in mock_api.calls)


def test_delete_404(runner, mock_api):
    mock_api.delete(f"/v1/app-gateways/{GW_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(app, ["appgw", "delete", GW_ID, "--yes"])
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


def test_attach_ip_happy(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(202, json={})

    mock_api.post(f"/v1/app-gateways/{GW_ID}/attach-ip").mock(side_effect=_capture)
    result = runner.invoke(
        app, ["appgw", "attach-ip", GW_ID, "--public-ip-id", PUBLIC_IP_ID]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"public_ip_id": PUBLIC_IP_ID}
    assert "Attachement" in result.stdout


def test_attach_ip_404(runner, mock_api):
    mock_api.post(f"/v1/app-gateways/{GW_ID}/attach-ip").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(
        app, ["appgw", "attach-ip", GW_ID, "--public-ip-id", PUBLIC_IP_ID]
    )
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


def test_detach_ip_happy(runner, mock_api):
    mock_api.post(f"/v1/app-gateways/{GW_ID}/detach-ip").mock(
        return_value=httpx.Response(202, json={})
    )
    result = runner.invoke(app, ["appgw", "detach-ip", GW_ID])
    assert result.exit_code == 0, result.stdout
    assert "Détachement" in result.stdout


def test_detach_ip_409(runner, mock_api):
    mock_api.post(f"/v1/app-gateways/{GW_ID}/detach-ip").mock(
        return_value=httpx.Response(409, json={"detail": "no ip attached"})
    )
    result = runner.invoke(app, ["appgw", "detach-ip", GW_ID])
    assert result.exit_code == 1
    assert "Conflit" in result.stdout


def test_health_with_target_groups(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/health").mock(
        return_value=httpx.Response(
            200,
            json={
                "target_groups": [
                    {
                        "name": "api-pool",
                        "members": [
                            {
                                "id": MEMBER_ID,
                                "address": "10.0.0.5",
                                "port": 8080,
                                "status": "UP",
                                "last_check_at": "2026-05-15T10:00:00Z",
                            },
                            {
                                "id": "11" * 16,
                                "address": "10.0.0.6",
                                "port": 8080,
                                "status": "DOWN",
                                "last_check_at": "2026-05-15T10:00:00Z",
                            },
                        ],
                    }
                ]
            },
        )
    )
    result = runner.invoke(app, ["appgw", "health", GW_ID])
    assert result.exit_code == 0, result.stdout
    assert "api-pool" in result.stdout
    assert "UP" in result.stdout
    assert "DOWN" in result.stdout


def test_health_404(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/health").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(app, ["appgw", "health", GW_ID])
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


# ---------------------------------------------------------------------------
# Sub-app : listener
# ---------------------------------------------------------------------------


def test_listener_add_auto_subdomain(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "id": LISTENER_ID,
                "hostname": "web-edge-abc.app.cloud.cetic-group.com",
                "acme_status": "pending",
                "custom_domain": False,
            },
        )

    mock_api.post(f"/v1/app-gateways/{GW_ID}/listeners").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "listener", "add", GW_ID,
            "--hostname", "web-edge-abc.app.cloud.cetic-group.com",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["hostname"] == "web-edge-abc.app.cloud.cetic-group.com"
    assert captured["body"]["custom_domain"] is False


def test_listener_add_custom_domain(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "id": LISTENER_ID,
                "hostname": "api.example.com",
                "acme_status": "pending",
                "custom_domain": True,
            },
        )

    mock_api.post(f"/v1/app-gateways/{GW_ID}/listeners").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "listener", "add", GW_ID,
            "--hostname", "api.example.com",
            "--custom-domain",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["custom_domain"] is True


def test_listener_add_422_error(runner, mock_api):
    mock_api.post(f"/v1/app-gateways/{GW_ID}/listeners").mock(
        return_value=httpx.Response(
            422, json={"detail": "hostname already exists on another gateway"}
        )
    )
    result = runner.invoke(
        app,
        [
            "appgw", "listener", "add", GW_ID,
            "--hostname", "api.example.com",
        ],
    )
    assert result.exit_code == 1
    assert "invalides" in result.stdout or "Paramètres" in result.stdout


def test_listener_list(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/listeners").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": LISTENER_ID,
                    "hostname": "api.example.com",
                    "acme_status": "issued",
                    "custom_domain": True,
                    "acme_last_renewal_at": "2026-05-01T10:00:00Z",
                },
                {
                    "id": "22" * 16,
                    "hostname": "admin.example.com",
                    "acme_status": "pending",
                    "custom_domain": True,
                },
            ],
        )
    )
    result = runner.invoke(app, ["appgw", "listener", "list", GW_ID])
    assert result.exit_code == 0, result.stdout
    assert "api.example.com" in result.stdout
    assert "admin.example.com" in result.stdout


def test_listener_list_empty(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/listeners").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = runner.invoke(app, ["appgw", "listener", "list", GW_ID])
    assert result.exit_code == 0
    assert "Listeners (0)" in result.stdout


def test_listener_delete_with_yes(runner, mock_api):
    mock_api.delete(f"/v1/app-gateways/{GW_ID}/listeners/{LISTENER_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(
        app,
        [
            "appgw", "listener", "delete", GW_ID,
            "--listener-id", LISTENER_ID,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "supprimé" in result.stdout


def test_listener_delete_404(runner, mock_api):
    mock_api.delete(f"/v1/app-gateways/{GW_ID}/listeners/{LISTENER_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(
        app,
        [
            "appgw", "listener", "delete", GW_ID,
            "--listener-id", LISTENER_ID,
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


def test_listener_renew_cert(runner, mock_api):
    mock_api.post(
        f"/v1/app-gateways/{GW_ID}/listeners/{LISTENER_ID}/renew-cert"
    ).mock(return_value=httpx.Response(202, json={}))
    result = runner.invoke(
        app,
        [
            "appgw", "listener", "renew-cert", GW_ID,
            "--listener-id", LISTENER_ID,
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Renouvellement" in result.stdout


def test_listener_renew_cert_404(runner, mock_api):
    mock_api.post(
        f"/v1/app-gateways/{GW_ID}/listeners/{LISTENER_ID}/renew-cert"
    ).mock(return_value=httpx.Response(404, json={"detail": "not found"}))
    result = runner.invoke(
        app,
        [
            "appgw", "listener", "renew-cert", GW_ID,
            "--listener-id", LISTENER_ID,
        ],
    )
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


# ---------------------------------------------------------------------------
# Sub-app : tg (target groups)
# ---------------------------------------------------------------------------


def test_tg_create_default_algorithm(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201, json={"id": TG_ID, "name": "api-pool", "algorithm": "roundrobin"}
        )

    mock_api.post(f"/v1/app-gateways/{GW_ID}/target-groups").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["appgw", "tg", "create", GW_ID, "--name", "api-pool"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"name": "api-pool", "algorithm": "roundrobin"}


def test_tg_create_leastconn(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201, json={"id": TG_ID, "name": "web-pool", "algorithm": "leastconn"}
        )

    mock_api.post(f"/v1/app-gateways/{GW_ID}/target-groups").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "create", GW_ID,
            "--name", "web-pool", "--algorithm", "leastconn",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["algorithm"] == "leastconn"


def test_tg_create_422(runner, mock_api):
    mock_api.post(f"/v1/app-gateways/{GW_ID}/target-groups").mock(
        return_value=httpx.Response(422, json={"detail": "name already exists"})
    )
    result = runner.invoke(
        app,
        ["appgw", "tg", "create", GW_ID, "--name", "dup"],
    )
    assert result.exit_code == 1
    assert "invalides" in result.stdout or "Paramètres" in result.stdout


def test_tg_list(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/target-groups").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": TG_ID,
                    "name": "api-pool",
                    "algorithm": "roundrobin",
                    "hc_path": "/health",
                    "member_count": 3,
                }
            ],
        )
    )
    result = runner.invoke(app, ["appgw", "tg", "list", GW_ID])
    assert result.exit_code == 0, result.stdout
    assert "api-pool" in result.stdout


def test_tg_delete_with_yes(runner, mock_api):
    mock_api.delete(f"/v1/app-gateways/{GW_ID}/target-groups/{TG_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(
        app,
        ["appgw", "tg", "delete", GW_ID, "--tg-id", TG_ID, "--yes"],
    )
    assert result.exit_code == 0, result.stdout
    assert "supprimé" in result.stdout


def test_tg_delete_409_used_by_route(runner, mock_api):
    mock_api.delete(f"/v1/app-gateways/{GW_ID}/target-groups/{TG_ID}").mock(
        return_value=httpx.Response(
            409, json={"detail": "target group is referenced by 2 routes"}
        )
    )
    result = runner.invoke(
        app,
        ["appgw", "tg", "delete", GW_ID, "--tg-id", TG_ID, "--yes"],
    )
    assert result.exit_code == 1
    assert "Conflit" in result.stdout


# ---------------------------------------------------------------------------
# Sub-app : tg member
# ---------------------------------------------------------------------------


def test_tg_member_add_container(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={"id": MEMBER_ID, "container_id": CONTAINER_ID, "port": 8080},
        )

    mock_api.post(
        f"/v1/app-gateways/{GW_ID}/target-groups/{TG_ID}/members"
    ).mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "member", "add", GW_ID,
            "--tg-id", TG_ID,
            "--container", CONTAINER_ID,
            "--port", "8080",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["container_id"] == CONTAINER_ID
    assert captured["body"]["port"] == 8080
    assert captured["body"]["weight"] == 100
    assert "vm_instance_id" not in captured["body"]
    assert "target_ip" not in captured["body"]


def test_tg_member_add_vm(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": MEMBER_ID})

    mock_api.post(
        f"/v1/app-gateways/{GW_ID}/target-groups/{TG_ID}/members"
    ).mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "member", "add", GW_ID,
            "--tg-id", TG_ID,
            "--vm", VM_ID,
            "--port", "3000",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["vm_instance_id"] == VM_ID
    assert "container_id" not in captured["body"]


def test_tg_member_add_target_ip(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": MEMBER_ID})

    mock_api.post(
        f"/v1/app-gateways/{GW_ID}/target-groups/{TG_ID}/members"
    ).mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "member", "add", GW_ID,
            "--tg-id", TG_ID,
            "--target-ip", "10.0.0.5",
            "--port", "8080",
            "--weight", "200",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["target_ip"] == "10.0.0.5"
    assert captured["body"]["weight"] == 200


def test_tg_member_add_no_target_fails(runner, mock_api):
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "member", "add", GW_ID,
            "--tg-id", TG_ID,
            "--port", "8080",
        ],
    )
    assert result.exit_code == 1
    assert "exactement un" in result.stdout


def test_tg_member_add_two_targets_fails(runner, mock_api):
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "member", "add", GW_ID,
            "--tg-id", TG_ID,
            "--container", CONTAINER_ID,
            "--vm", VM_ID,
            "--port", "8080",
        ],
    )
    assert result.exit_code == 1
    assert "exactement un" in result.stdout


def test_tg_member_add_invalid_port(runner, mock_api):
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "member", "add", GW_ID,
            "--tg-id", TG_ID,
            "--target-ip", "10.0.0.5",
            "--port", "70000",
        ],
    )
    assert result.exit_code == 1
    assert "port" in result.stdout.lower()


def test_tg_member_add_invalid_weight(runner, mock_api):
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "member", "add", GW_ID,
            "--tg-id", TG_ID,
            "--target-ip", "10.0.0.5",
            "--port", "8080",
            "--weight", "5000",
        ],
    )
    assert result.exit_code == 1
    assert "poids" in result.stdout.lower()


def test_tg_member_remove_with_yes(runner, mock_api):
    mock_api.delete(
        f"/v1/app-gateways/{GW_ID}/target-groups/{TG_ID}/members/{MEMBER_ID}"
    ).mock(return_value=httpx.Response(204))
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "member", "remove", GW_ID,
            "--tg-id", TG_ID,
            "--member-id", MEMBER_ID,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "retiré" in result.stdout


def test_tg_member_remove_404(runner, mock_api):
    mock_api.delete(
        f"/v1/app-gateways/{GW_ID}/target-groups/{TG_ID}/members/{MEMBER_ID}"
    ).mock(return_value=httpx.Response(404, json={"detail": "not found"}))
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "member", "remove", GW_ID,
            "--tg-id", TG_ID,
            "--member-id", MEMBER_ID,
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


# ---------------------------------------------------------------------------
# Sub-app : route
# ---------------------------------------------------------------------------


def test_route_create_minimal(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "id": ROUTE_ID,
                "listener_id": LISTENER_ID,
                "target_group_id": TG_ID,
                "priority": 100,
                "waf_preset": "off",
            },
        )

    mock_api.post(f"/v1/app-gateways/{GW_ID}/routes").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "route", "create", GW_ID,
            "--listener-id", LISTENER_ID,
            "--target-group-id", TG_ID,
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["listener_id"] == LISTENER_ID
    assert captured["body"]["target_group_id"] == TG_ID
    assert captured["body"]["priority"] == 100
    assert captured["body"]["waf_preset"] == "off"
    assert "path_match" not in captured["body"]
    assert "rate_limit_per_sec" not in captured["body"]


def test_route_create_full_policies(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={"id": ROUTE_ID, "priority": 50, "waf_preset": "strict"},
        )

    mock_api.post(f"/v1/app-gateways/{GW_ID}/routes").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "route", "create", GW_ID,
            "--listener-id", LISTENER_ID,
            "--target-group-id", TG_ID,
            "--path", "/api",
            "--priority", "50",
            "--rate-limit", "100",
            "--allow-cidr", "10.0.0.0/8",
            "--allow-cidr", "192.168.1.0/24",
            "--deny-cidr", "172.16.0.0/12",
            "--waf-preset", "strict",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["path_match"] == "/api"
    assert captured["body"]["priority"] == 50
    assert captured["body"]["rate_limit_per_sec"] == 100
    assert captured["body"]["allow_cidrs"] == ["10.0.0.0/8", "192.168.1.0/24"]
    assert captured["body"]["deny_cidrs"] == ["172.16.0.0/12"]
    assert captured["body"]["waf_preset"] == "strict"


def test_route_create_invalid_waf_preset(runner, mock_api):
    result = runner.invoke(
        app,
        [
            "appgw", "route", "create", GW_ID,
            "--listener-id", LISTENER_ID,
            "--target-group-id", TG_ID,
            "--waf-preset", "ultra",
        ],
    )
    assert result.exit_code == 1
    assert "Preset WAF invalide" in result.stdout


def test_route_create_422(runner, mock_api):
    mock_api.post(f"/v1/app-gateways/{GW_ID}/routes").mock(
        return_value=httpx.Response(
            422, json={"detail": "listener_id does not belong to this gateway"}
        )
    )
    result = runner.invoke(
        app,
        [
            "appgw", "route", "create", GW_ID,
            "--listener-id", LISTENER_ID,
            "--target-group-id", TG_ID,
        ],
    )
    assert result.exit_code == 1
    assert "invalides" in result.stdout or "Paramètres" in result.stdout


def test_route_list_sorted_by_priority(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/routes").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "33" * 16,
                    "priority": 200,
                    "listener_id": LISTENER_ID,
                    "path_match": "/v2",
                    "target_group_id": TG_ID,
                    "waf_preset": "off",
                },
                {
                    "id": ROUTE_ID,
                    "priority": 50,
                    "listener_id": LISTENER_ID,
                    "path_match": "/api",
                    "target_group_id": TG_ID,
                    "rate_limit_per_sec": 100,
                    "waf_preset": "strict",
                },
            ],
        )
    )
    result = runner.invoke(app, ["appgw", "route", "list", GW_ID])
    assert result.exit_code == 0, result.stdout
    # Le tri par priority asc affiche /api (50) avant /v2 (200).
    api_pos = result.stdout.find("/api")
    v2_pos = result.stdout.find("/v2")
    assert api_pos != -1 and v2_pos != -1
    assert api_pos < v2_pos


def test_route_list_empty(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/routes").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = runner.invoke(app, ["appgw", "route", "list", GW_ID])
    assert result.exit_code == 0
    assert "Routes (0)" in result.stdout


def test_route_delete_with_yes(runner, mock_api):
    mock_api.delete(f"/v1/app-gateways/{GW_ID}/routes/{ROUTE_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(
        app,
        [
            "appgw", "route", "delete", GW_ID,
            "--route-id", ROUTE_ID,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "supprimée" in result.stdout


def test_route_delete_404(runner, mock_api):
    mock_api.delete(f"/v1/app-gateways/{GW_ID}/routes/{ROUTE_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(
        app,
        [
            "appgw", "route", "delete", GW_ID,
            "--route-id", ROUTE_ID,
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


# ---------------------------------------------------------------------------
# Sub-app : tg update (v0.12.0)
# ---------------------------------------------------------------------------


def test_tg_update_renames(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": TG_ID, "name": "api-pool-v2", "algorithm": "leastconn"},
        )

    mock_api.patch(f"/v1/app-gateways/{GW_ID}/target-groups/{TG_ID}").mock(
        side_effect=_capture
    )
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "update", GW_ID,
            "--tg-id", TG_ID,
            "--name", "api-pool-v2",
            "--algorithm", "leastconn",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"name": "api-pool-v2", "algorithm": "leastconn"}


def test_tg_update_health_check_fields(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": TG_ID})

    mock_api.patch(f"/v1/app-gateways/{GW_ID}/target-groups/{TG_ID}").mock(
        side_effect=_capture
    )
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "update", GW_ID,
            "--tg-id", TG_ID,
            "--hc-protocol", "https",
            "--hc-method", "get",
            "--hc-path", "/health",
            "--hc-expect-status", "204",
            "--hc-interval-sec", "10",
            "--hc-timeout-sec", "5",
            "--hc-healthy-threshold", "3",
            "--hc-unhealthy-threshold", "5",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {
        "hc_protocol": "https",
        "hc_method": "GET",
        "hc_path": "/health",
        "hc_expect_status": 204,
        "hc_interval_sec": 10,
        "hc_timeout_sec": 5,
        "hc_healthy_threshold": 3,
        "hc_unhealthy_threshold": 5,
    }


def test_tg_update_no_fields_fails(runner, mock_api):
    result = runner.invoke(
        app,
        ["appgw", "tg", "update", GW_ID, "--tg-id", TG_ID],
    )
    assert result.exit_code == 1
    assert "aucun champ" in result.stdout.lower()


def test_tg_update_invalid_algorithm_fails(runner, mock_api):
    result = runner.invoke(
        app,
        [
            "appgw", "tg", "update", GW_ID,
            "--tg-id", TG_ID,
            "--algorithm", "random",
        ],
    )
    assert result.exit_code == 1
    assert "Algorithme invalide" in result.stdout


# ---------------------------------------------------------------------------
# Sub-app : listener get (v0.12.0)
# ---------------------------------------------------------------------------


def test_listener_get_found(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/listeners").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": LISTENER_ID,
                    "hostname": "api.example.com",
                    "acme_status": "issued",
                    "custom_domain": True,
                },
                {
                    "id": "22" * 16,
                    "hostname": "admin.example.com",
                    "acme_status": "pending",
                },
            ],
        )
    )
    result = runner.invoke(
        app,
        ["appgw", "listener", "get", GW_ID, "--listener-id", LISTENER_ID],
    )
    assert result.exit_code == 0, result.stdout
    assert "api.example.com" in result.stdout


def test_listener_get_not_found(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/listeners").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = runner.invoke(
        app,
        ["appgw", "listener", "get", GW_ID, "--listener-id", LISTENER_ID],
    )
    assert result.exit_code == 1
    assert "aucun listener" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Sub-app : route create avec basic auth (v0.12.0)
# ---------------------------------------------------------------------------


def test_route_create_with_basic_auth(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "id": ROUTE_ID,
                "basic_auth_secret_ref": "secret/appgw/route-xyz",
            },
        )

    mock_api.post(f"/v1/app-gateways/{GW_ID}/routes").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "route", "create", GW_ID,
            "--listener-id", LISTENER_ID,
            "--target-group-id", TG_ID,
            "--basic-auth-user", "alice:s3cret",
            "--basic-auth-user", "bob:hunter2",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["basic_auth_users"] == [
        {"user": "alice", "password": "s3cret"},
        {"user": "bob", "password": "hunter2"},
    ]


def test_route_create_basic_auth_password_with_colon(runner, mock_api):
    """Le séparateur est le PREMIER `:` — pwd peut contenir des `:`."""
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": ROUTE_ID})

    mock_api.post(f"/v1/app-gateways/{GW_ID}/routes").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "appgw", "route", "create", GW_ID,
            "--listener-id", LISTENER_ID,
            "--target-group-id", TG_ID,
            "--basic-auth-user", "alice:p4ss:w:rd",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["basic_auth_users"] == [
        {"user": "alice", "password": "p4ss:w:rd"},
    ]


def test_route_create_basic_auth_invalid_format(runner, mock_api):
    result = runner.invoke(
        app,
        [
            "appgw", "route", "create", GW_ID,
            "--listener-id", LISTENER_ID,
            "--target-group-id", TG_ID,
            "--basic-auth-user", "noseparator",
        ],
    )
    assert result.exit_code == 1
    assert "user:password" in result.stdout


def test_route_create_basic_auth_duplicate_user(runner, mock_api):
    result = runner.invoke(
        app,
        [
            "appgw", "route", "create", GW_ID,
            "--listener-id", LISTENER_ID,
            "--target-group-id", TG_ID,
            "--basic-auth-user", "alice:x",
            "--basic-auth-user", "alice:y",
        ],
    )
    assert result.exit_code == 1
    assert "dupliqué" in result.stdout.lower() or "duplique" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Sub-app : route get + update (v0.12.0)
# ---------------------------------------------------------------------------


def test_route_get_masks_basic_auth_when_configured(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/routes").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": ROUTE_ID,
                    "priority": 100,
                    "path_match": "/admin",
                    "basic_auth_secret_ref": "secret/appgw/admin",
                    "waf_preset": "strict",
                }
            ],
        )
    )
    result = runner.invoke(
        app,
        ["appgw", "route", "get", GW_ID, "--route-id", ROUTE_ID],
    )
    assert result.exit_code == 0, result.stdout
    assert "configuré" in result.stdout
    # Le secret_ref brut ne doit pas être affiché.
    assert "secret/appgw/admin" not in result.stdout


def test_route_get_basic_auth_disabled(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/routes").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": ROUTE_ID,
                    "priority": 100,
                    "path_match": "/api",
                    "basic_auth_secret_ref": None,
                    "waf_preset": "off",
                }
            ],
        )
    )
    result = runner.invoke(
        app,
        ["appgw", "route", "get", GW_ID, "--route-id", ROUTE_ID],
    )
    assert result.exit_code == 0, result.stdout
    assert "désactivé" in result.stdout


def test_route_get_not_found(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/routes").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = runner.invoke(
        app,
        ["appgw", "route", "get", GW_ID, "--route-id", ROUTE_ID],
    )
    assert result.exit_code == 1
    assert "aucune route" in result.stdout.lower()


def test_route_update_priority_only(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": ROUTE_ID, "priority": 200})

    mock_api.patch(f"/v1/app-gateways/{GW_ID}/routes/{ROUTE_ID}").mock(
        side_effect=_capture
    )
    result = runner.invoke(
        app,
        [
            "appgw", "route", "update", GW_ID,
            "--route-id", ROUTE_ID,
            "--priority", "200",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"priority": 200}


def test_route_update_basic_auth_enable(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": ROUTE_ID})

    mock_api.patch(f"/v1/app-gateways/{GW_ID}/routes/{ROUTE_ID}").mock(
        side_effect=_capture
    )
    result = runner.invoke(
        app,
        [
            "appgw", "route", "update", GW_ID,
            "--route-id", ROUTE_ID,
            "--basic-auth-user", "alice:newpwd",
            "--basic-auth-user", "bob:newpwd",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["basic_auth_users"] == [
        {"user": "alice", "password": "newpwd"},
        {"user": "bob", "password": "newpwd"},
    ]
    # En mode enable on n'envoie PAS basic_auth_secret_ref.
    assert "basic_auth_secret_ref" not in captured["body"]


def test_route_update_basic_auth_disable(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": ROUTE_ID})

    mock_api.patch(f"/v1/app-gateways/{GW_ID}/routes/{ROUTE_ID}").mock(
        side_effect=_capture
    )
    result = runner.invoke(
        app,
        [
            "appgw", "route", "update", GW_ID,
            "--route-id", ROUTE_ID,
            "--no-basic-auth",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"basic_auth_secret_ref": None}


def test_route_update_basic_auth_conflicting_flags(runner, mock_api):
    result = runner.invoke(
        app,
        [
            "appgw", "route", "update", GW_ID,
            "--route-id", ROUTE_ID,
            "--basic-auth-user", "alice:x",
            "--no-basic-auth",
        ],
    )
    assert result.exit_code == 1
    assert "incompatibles" in result.stdout


def test_route_update_no_fields_fails(runner, mock_api):
    result = runner.invoke(
        app,
        ["appgw", "route", "update", GW_ID, "--route-id", ROUTE_ID],
    )
    assert result.exit_code == 1
    assert "aucun champ" in result.stdout.lower()


# ---------------------------------------------------------------------------
# acme-providers (v0.12.0)
# ---------------------------------------------------------------------------


def test_acme_providers_list_of_dicts(runner, mock_api):
    mock_api.get("/v1/app-gateways/acme/dns-providers").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "name": "ionos",
                    "label": "IONOS",
                    "required_credentials": ["IONOS_API_KEY"],
                },
                {
                    "name": "ovh",
                    "label": "OVHcloud",
                    "required_credentials": [
                        "OVH_APPLICATION_KEY",
                        "OVH_APPLICATION_SECRET",
                        "OVH_CONSUMER_KEY",
                    ],
                },
            ],
        )
    )
    result = runner.invoke(app, ["appgw", "acme-providers"])
    assert result.exit_code == 0, result.stdout
    assert "ionos" in result.stdout
    assert "OVHcloud" in result.stdout
    assert "Providers DNS-01 disponibles (2)" in result.stdout


def test_acme_providers_envelope_form(runner, mock_api):
    mock_api.get("/v1/app-gateways/acme/dns-providers").mock(
        return_value=httpx.Response(
            200,
            json={
                "providers": [
                    {"name": "cloudflare", "label": "Cloudflare"},
                ]
            },
        )
    )
    result = runner.invoke(app, ["appgw", "acme-providers"])
    assert result.exit_code == 0, result.stdout
    assert "cloudflare" in result.stdout


def test_acme_providers_500(runner, mock_api):
    mock_api.get("/v1/app-gateways/acme/dns-providers").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )
    result = runner.invoke(app, ["appgw", "acme-providers"])
    assert result.exit_code == 1
    assert "Erreur serveur" in result.stdout


# ---------------------------------------------------------------------------
# Garde-fous : branding + structure
# ---------------------------------------------------------------------------


def test_no_legacy_brand_terminology():
    """Garde-fou : aucune référence à l'ancien branding (cloud-lake / cl-*)."""
    import pathlib

    f = pathlib.Path(__file__).parent.parent / "cetic" / "commands" / "appgw.py"
    text = f.read_text(encoding="utf-8").lower()
    for token in ("cloud-lake", "cloudlake", "cloud_lake"):
        assert token not in text, f"{f} contient interdit : {token}"
    # `cl-*` historique : on ne doit pas voir cl-appgw / cl-lb dans le code.
    assert "cl-appgw" not in text
    assert "cl-lb" not in text


def test_appgw_app_command_count():
    """v0.12.0 : 8 top-level + 5 listener + 4 tg + 2 tg member + 5 route = 24 commandes.

    v0.11.0 → v0.12.0 ajoute :
    - top-level : `acme-providers`
    - listener  : `get`
    - tg        : `update`
    - route     : `get`, `update`

    Note : Typer stocke `c.name=None` quand `@app.command()` est utilisé sans
    paramètre `name=...`. Dans ce cas le nom CLI dérive de la fonction.
    """
    from cetic.commands import appgw

    def _names(commands: list[Any]) -> set[str]:
        return {c.name or c.callback.__name__.replace("_", "-") for c in commands}

    top_level = _names(appgw.app.registered_commands)
    # list, get, create, delete, attach-ip, detach-ip, health, acme-providers
    assert len(top_level) == 8, top_level
    for expected in (
        "list", "get", "create", "delete",
        "attach-ip", "detach-ip", "health", "acme-providers",
    ):
        assert expected in top_level, f"{expected} manquant dans {top_level}"

    listener_cmds = _names(appgw.listener_app.registered_commands)
    assert len(listener_cmds) == 5, listener_cmds  # add, get, list, delete, renew-cert
    for expected in ("add", "get", "list", "delete", "renew-cert"):
        assert expected in listener_cmds, f"listener {expected} manquant"

    tg_cmds = _names(appgw.tg_app.registered_commands)
    assert len(tg_cmds) == 4, tg_cmds  # create, list, update, delete
    for expected in ("create", "list", "update", "delete"):
        assert expected in tg_cmds, f"tg {expected} manquant"

    tg_member_cmds = _names(appgw.tg_member_app.registered_commands)
    assert len(tg_member_cmds) == 2, tg_member_cmds  # add, remove

    route_cmds = _names(appgw.route_app.registered_commands)
    assert len(route_cmds) == 5, route_cmds  # create, get, list, update, delete
    for expected in ("create", "get", "list", "update", "delete"):
        assert expected in route_cmds, f"route {expected} manquant"

    total = (
        len(top_level)
        + len(listener_cmds)
        + len(tg_cmds)
        + len(tg_member_cmds)
        + len(route_cmds)
    )
    assert total == 24


def test_format_api_error_messages():
    from cetic import client as client_mod
    from cetic.commands.appgw import _format_api_error

    assert "Non authentifié" in _format_api_error(client_mod.APIError(401, "x"))
    assert "Accès refusé" in _format_api_error(client_mod.APIError(403, "x"))
    assert "introuvable" in _format_api_error(client_mod.APIError(404, "x"))
    assert "Quota atteint" in _format_api_error(
        client_mod.APIError(409, "max_app_gateways=2 reached")
    )
    assert "Conflit" in _format_api_error(client_mod.APIError(409, "duplicate name"))
    assert "Paramètres invalides" in _format_api_error(client_mod.APIError(422, "x"))
    assert "Erreur serveur" in _format_api_error(client_mod.APIError(503, "x"))


def test_status_color_helper():
    from cetic.commands.appgw import _status_color

    assert _status_color("UP") == "green"
    assert _status_color("active") == "green"
    assert _status_color("issued") == "green"
    assert _status_color("DOWN") == "red"
    assert _status_color("error") == "red"
    assert _status_color("creating") == "yellow"
    assert _status_color("pending") == "yellow"
    assert _status_color("") == "white"
