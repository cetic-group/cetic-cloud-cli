"""#53 (alignement cetic-cloud-platform#1579) — LB et AppGW.

Page d'indisponibilité, certificat fourni par le client (création + rotation),
cibles scale set dynamiques, champs réseau. Chaque test capture le corps
réellement envoyé : le contrat est celui des schémas Pydantic de l'API.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from cetic.commands._gateway import (
    ERROR_PAGE_MAX_BYTES,
    cert_expiry_label,
    read_cert_pair,
    read_error_page,
    target_label,
)
from cetic.commands.lb import _parse_backend_spec
from cetic.main import app

LB_ID = "11111111-2222-3333-4444-555555555555"
LISTENER_ID = "22222222-3333-4444-5555-666666666666"
GW_ID = "33333333-4444-5555-6666-777777777777"
TG_ID = "44444444-5555-6666-7777-888888888888"
VMSS_ID = "55555555-6666-7777-8888-999999999999"
CTSS_ID = "66666666-7777-8888-9999-aaaaaaaaaaaa"

CERT = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
KEY = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n"


@pytest.fixture
def pem(tmp_path: Path) -> tuple[Path, Path]:
    cert, key = tmp_path / "fullchain.pem", tmp_path / "privkey.pem"
    cert.write_text(CERT)
    key.write_text(KEY)
    return cert, key


def _capture(store: dict[str, Any], status: int = 200, body: Any = None):
    def _handler(request: httpx.Request) -> httpx.Response:
        store["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=body if body is not None else {})
    return _handler


def _lb(**over: Any) -> dict[str, Any]:
    base = {
        "id": LB_ID, "name": "web", "region": "RNN", "vnet_id": "v", "plan": "small",
        "status": "active", "vip_address": "10.0.0.5", "public_ip_address": None,
        "public_ip_id": None, "error_message": None, "tags": [],
        "vpc_id": "vpc-1", "vpc_name": "prod", "vnet_name": "web", "vnet_cidr": "10.0.0.0/24",
        "custom_error_page_html": "<h1>Maintenance</h1>",
        "listeners": [{
            "id": LISTENER_ID, "protocol": "https", "listen_port": 443,
            "algorithm": "roundrobin", "domain": "www.example.com",
            "acme_challenge": "custom", "acme_status": "issued",
            "tls_cert_not_after": "2099-01-01T00:00:00+00:00",
            "backends": [{"id": "b1", "vm_scale_set_id": VMSS_ID, "port": 8080, "weight": 1}],
        }],
        "created_at": "x", "updated_at": "x",
    }
    base.update(over)
    return base


# ── Cibles scale set : UNE cible, jamais développée ─────────────────────────


def test_backend_spec_scale_sets() -> None:
    assert _parse_backend_spec(f"vmss:{VMSS_ID}:8080") == {
        "port": 8080, "weight": 1, "vm_scale_set_id": VMSS_ID,
    }
    assert _parse_backend_spec(f"ctss:{CTSS_ID}:80:5")["container_scale_set_id"] == CTSS_ID


def test_lb_backend_add_scale_set_sends_one_target(runner, mock_api) -> None:
    """Le scale set part tel quel : aucune liste d'instances lue, un seul POST."""
    store: dict[str, Any] = {}
    route = mock_api.post(
        f"/v1/load-balancers/{LB_ID}/listeners/{LISTENER_ID}/backends"
    ).mock(side_effect=_capture(store, 201))
    members = mock_api.get(url__regex=r".*scale-sets.*")
    result = runner.invoke(app, [
        "lb", "backend", "add", LB_ID, LISTENER_ID, "--vm-scale-set", VMSS_ID, "--port", "8080",
    ])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert not members.called
    assert store["body"] == {"port": 8080, "weight": 1, "vm_scale_set_id": VMSS_ID}


def test_lb_backend_add_rejects_two_targets(runner, mock_api) -> None:
    route = mock_api.post(url__regex=r".*/backends$")
    result = runner.invoke(app, [
        "lb", "backend", "add", LB_ID, LISTENER_ID,
        "--vm-scale-set", VMSS_ID, "--container", "c1", "--port", "80",
    ])
    assert result.exit_code == 1
    assert not route.called


def test_appgw_member_add_scale_set_sends_one_target(runner, mock_api) -> None:
    store: dict[str, Any] = {}
    route = mock_api.post(
        f"/v1/app-gateways/{GW_ID}/target-groups/{TG_ID}/members"
    ).mock(side_effect=_capture(store, 201, {"id": "m1"}))
    result = runner.invoke(app, [
        "appgw", "tg", "member", "add", GW_ID, "--tg-id", TG_ID,
        "--container-scale-set", CTSS_ID, "--port", "8080",
    ])
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert store["body"] == {"port": 8080, "weight": 100, "container_scale_set_id": CTSS_ID}


def test_appgw_member_list_labels_dynamic_target(runner, mock_api) -> None:
    mock_api.get(f"/v1/app-gateways/{GW_ID}/target-groups").mock(return_value=httpx.Response(
        200, json=[{"id": TG_ID, "name": "web", "members": [
            {"id": "m1", "vm_scale_set_id": VMSS_ID, "port": 80, "weight": 100, "enabled": True},
        ]}]))
    result = runner.invoke(app, ["appgw", "tg", "member", "list", GW_ID, "--tg-id", TG_ID])
    assert result.exit_code == 0, result.output
    assert "dynamique" in result.output


def test_target_label() -> None:
    assert "dynamique" in target_label({"vm_scale_set_id": VMSS_ID})
    assert target_label({"target_ip": "10.0.0.9", "target_label": "node-1"}) == "10.0.0.9 (node-1)"


# ── Page d'indisponibilité ───────────────────────────────────────────────────


def test_lb_update_error_page(runner, mock_api, tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<h1>Maintenance</h1>", encoding="utf-8")
    store: dict[str, Any] = {}
    mock_api.patch(f"/v1/load-balancers/{LB_ID}").mock(side_effect=_capture(store))
    result = runner.invoke(app, ["lb", "update", LB_ID, "--error-page-file", str(page)])
    assert result.exit_code == 0, result.output
    assert store["body"] == {"custom_error_page_html": "<h1>Maintenance</h1>"}


def test_appgw_reset_error_page_sends_explicit_null(runner, mock_api) -> None:
    """Le champ PRÉSENT à null rétablit la page par défaut ; absent = inchangé."""
    store: dict[str, Any] = {}
    mock_api.patch(f"/v1/app-gateways/{GW_ID}").mock(side_effect=_capture(store))
    result = runner.invoke(app, ["appgw", "update", GW_ID, "--reset-error-page"])
    assert result.exit_code == 0, result.output
    assert store["body"] == {"custom_error_page_html": None}


def test_error_page_too_big_is_refused_locally(runner, mock_api, tmp_path: Path) -> None:
    page = tmp_path / "big.html"
    page.write_bytes(b"x" * (ERROR_PAGE_MAX_BYTES + 1))
    route = mock_api.patch(f"/v1/load-balancers/{LB_ID}")
    result = runner.invoke(app, ["lb", "update", LB_ID, "--error-page-file", str(page)])
    assert result.exit_code == 1
    assert "7 Kio" in result.output
    assert not route.called


def test_read_error_page_limit_is_in_utf8_bytes(tmp_path: Path) -> None:
    page = tmp_path / "p.html"
    page.write_text("é" * (ERROR_PAGE_MAX_BYTES // 2), encoding="utf-8")  # pile 7 Kio
    assert read_error_page(page)
    page.write_text("é" * (ERROR_PAGE_MAX_BYTES // 2 + 1), encoding="utf-8")
    with pytest.raises(ValueError):
        read_error_page(page)


def test_get_table_hides_html_but_json_keeps_it(runner, mock_api, monkeypatch) -> None:
    mock_api.get(f"/v1/load-balancers/{LB_ID}").mock(return_value=httpx.Response(200, json=_lb()))
    result = runner.invoke(app, ["lb", "get", LB_ID])
    assert result.exit_code == 0, result.output
    assert "personnalisée" in result.output
    assert "<h1>" not in result.output
    assert "prod / web (10.0.0.0/24)" in result.output
    assert "dynamique" in result.output

    monkeypatch.setenv("CCP_OUTPUT", "json")
    result = runner.invoke(app, ["lb", "get", LB_ID])
    assert json.loads(result.output)["custom_error_page_html"] == "<h1>Maintenance</h1>"


# ── Certificat client ────────────────────────────────────────────────────────


def test_lb_create_with_custom_certificate(runner, mock_api, pem) -> None:
    cert, key = pem
    store: dict[str, Any] = {}
    mock_api.post("/v1/load-balancers").mock(
        side_effect=_capture(store, 201, {"id": LB_ID, "status": "provisioning"}))
    result = runner.invoke(app, [
        "lb", "create", "-n", "web", "-r", "RNN", "--vnet", "v",
        "--listener-protocol", "https", "--listener-port", "443",
        "--domain", "www.example.com", "--acme-challenge", "custom",
        "--cert-file", str(cert), "--key-file", str(key),
        "--backend", f"vmss:{VMSS_ID}:8080",
    ])
    assert result.exit_code == 0, result.output
    listener = store["body"]["listeners"][0]
    assert listener["acme_challenge"] == "custom"
    assert listener["tls_certificate_pem"] == CERT
    assert listener["tls_private_key_pem"] == KEY
    assert listener["backends"] == [{"port": 8080, "weight": 1, "vm_scale_set_id": VMSS_ID}]


def test_lb_listener_add_custom_requires_both_files(runner, mock_api, pem) -> None:
    cert, _ = pem
    route = mock_api.post(f"/v1/load-balancers/{LB_ID}/listeners")
    result = runner.invoke(app, [
        "lb", "listener", "add", LB_ID, "--protocol", "https", "--port", "443",
        "--domain", "www.example.com", "--acme-challenge", "custom", "--cert-file", str(cert),
    ])
    assert result.exit_code == 1
    assert "ensemble" in result.output
    assert not route.called


def test_lb_rotate_cert_posts_body_on_retry_route(runner, mock_api, pem) -> None:
    cert, key = pem
    store: dict[str, Any] = {}
    mock_api.post(f"/v1/load-balancers/{LB_ID}/listeners/{LISTENER_ID}/acme/retry").mock(
        side_effect=_capture(store, 200, _lb()))
    result = runner.invoke(app, [
        "lb", "listener", "rotate-cert", LB_ID, LISTENER_ID,
        "--cert-file", str(cert), "--key-file", str(key),
    ])
    assert result.exit_code == 0, result.output
    assert store["body"] == {"tls_certificate_pem": CERT, "tls_private_key_pem": KEY}
    assert "2099-01-01" in result.output


def test_appgw_listener_custom_and_rotation(runner, mock_api, pem) -> None:
    cert, key = pem
    created: dict[str, Any] = {}
    mock_api.post(f"/v1/app-gateways/{GW_ID}/listeners").mock(side_effect=_capture(
        created, 201, {"id": LISTENER_ID, "hostname": "api.example.com",
                       "tls_cert_not_after": "2099-01-01T00:00:00+00:00"}))
    result = runner.invoke(app, [
        "appgw", "listener", "add", GW_ID, "--hostname", "api.example.com",
        "--acme-challenge", "custom", "--cert-file", str(cert), "--key-file", str(key),
    ])
    assert result.exit_code == 0, result.output
    assert created["body"]["acme_challenge"] == "custom"
    assert created["body"]["tls_private_key_pem"] == KEY

    rotated: dict[str, Any] = {}
    mock_api.post(f"/v1/app-gateways/{GW_ID}/listeners/{LISTENER_ID}/renew-cert").mock(
        side_effect=_capture(rotated, 200, {"id": LISTENER_ID}))
    result = runner.invoke(app, [
        "appgw", "listener", "rotate-cert", GW_ID, "--listener-id", LISTENER_ID,
        "--cert-file", str(cert), "--key-file", str(key),
    ])
    assert result.exit_code == 0, result.output
    assert rotated["body"] == {"tls_certificate_pem": CERT, "tls_private_key_pem": KEY}


def test_encrypted_key_is_refused(tmp_path: Path) -> None:
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    cert.write_text(CERT)
    key.write_text("-----BEGIN ENCRYPTED PRIVATE KEY-----\nx\n-----END ENCRYPTED PRIVATE KEY-----\n")
    with pytest.raises(ValueError, match="phrase secrète"):
        read_cert_pair(cert, key)


def test_renew_409_on_custom_listener_explains_rotation(runner, mock_api) -> None:
    mock_api.post(f"/v1/app-gateways/{GW_ID}/listeners/{LISTENER_ID}/renew-cert").mock(
        return_value=httpx.Response(409, json={
            "detail": "Ce listener utilise votre certificat : envoyez le nouveau certificat."}))
    result = runner.invoke(app, [
        "appgw", "listener", "renew-cert", GW_ID, "--listener-id", LISTENER_ID,
    ])
    assert result.exit_code == 1
    assert "votre certificat" in result.output
    assert "rotate-cert" in result.output


def test_422_validation_list_is_readable(runner, mock_api, pem) -> None:
    cert, key = pem
    mock_api.post(f"/v1/load-balancers/{LB_ID}/listeners/{LISTENER_ID}/acme/retry").mock(
        return_value=httpx.Response(422, json={"detail": [
            {"loc": ["body", "tls_private_key_pem"], "msg": "Value error, clé invalide",
             "type": "value_error"}]}))
    result = runner.invoke(app, [
        "lb", "listener", "rotate-cert", LB_ID, LISTENER_ID,
        "--cert-file", str(cert), "--key-file", str(key),
    ])
    assert result.exit_code == 1
    assert "tls_private_key_pem : clé invalide" in result.output
    assert "'loc'" not in result.output


def test_cert_expiry_label() -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)
    assert cert_expiry_label((now + timedelta(days=90)).isoformat(), now=now) == "2027-01-08"
    assert "⚠ 10 j" in cert_expiry_label((now + timedelta(days=10)).isoformat(), now=now)
    assert "expiré" in cert_expiry_label((now - timedelta(days=1)).isoformat(), now=now)
    assert cert_expiry_label(None) == "—"


# ── Santé : décompte des instances d'une cible scale set ─────────────────────


def test_lb_health_shows_instances(runner, mock_api) -> None:
    mock_api.get(f"/v1/load-balancers/{LB_ID}/health").mock(return_value=httpx.Response(200, json={
        "b1": {"status": "UP", "check_status": "L7OK", "instances_up": "2",
               "instances_total": "3"},
    }))
    result = runner.invoke(app, ["lb", "health", LB_ID])
    assert result.exit_code == 0, result.output
    assert "2/3" in result.output
