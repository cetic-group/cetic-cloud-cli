"""cetic-cloud-platform#2004 — IP, volumes et clés d'API alignés sur l'API.

- `ip get` / `api-key get` : pas de route GET unitaire, lecture dans la liste ;
- `ip attach` / `volume attach` : `resource_type` + `resource_id` (422 avec
  `container_id`/`vm_id`) ; l'IP d'un LB passe par `…/load-balancers/{id}/attach-ip` ;
- `volume list` : colonne « Attaché à » lue sur `attached_to_name`/`attached_to_id`.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cetic.main import app

IP = "11111111-1111-1111-1111-111111111111"
CIBLE = "22222222-2222-2222-2222-222222222222"
VOL = "33333333-3333-3333-3333-333333333333"


def test_ip_get_lit_la_liste(runner, mock_api):
    mock_api.get("/v1/public-ips").mock(return_value=httpx.Response(200, json=[
        {"id": "autre", "ip_address": "203.0.113.9"},
        {"id": IP, "ip_address": "203.0.113.10", "status": "allocated"}]))
    result = runner.invoke(app, ["ip", "get", IP], env={"CCP_OUTPUT": "json"})
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["ip_address"] == "203.0.113.10"


def test_ip_get_introuvable(runner, mock_api):
    mock_api.get("/v1/public-ips").mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(app, ["ip", "get", IP])
    assert result.exit_code == 1
    assert "introuvable" in result.stdout + result.stderr


@pytest.mark.parametrize(("option", "attendu"), [
    ("--container", {"resource_type": "container", "resource_id": CIBLE}),
    ("--vm", {"resource_type": "vm_instance", "resource_id": CIBLE}),
])
def test_ip_attach_contrat(runner, mock_api, option, attendu):
    route = mock_api.post(f"/v1/public-ips/{IP}/attach").mock(
        return_value=httpx.Response(200, json={"id": IP}))
    result = runner.invoke(app, ["ip", "attach", IP, option, CIBLE])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(route.calls.last.request.content) == attendu


def test_ip_attach_lb_par_la_route_du_lb(runner, mock_api):
    route = mock_api.post(f"/v1/load-balancers/{CIBLE}/attach-ip").mock(
        return_value=httpx.Response(200, json={"id": CIBLE}))
    result = runner.invoke(app, ["ip", "attach", IP, "--lb", CIBLE])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(route.calls.last.request.content) == {"public_ip_id": IP}


def test_ip_attach_une_seule_cible(runner, mock_api):
    result = runner.invoke(app, ["ip", "attach", IP, "--container", CIBLE, "--vm", CIBLE])
    assert result.exit_code == 1


@pytest.mark.parametrize(("option", "type_"), [("--container", "container"), ("--vm", "vm")])
def test_volume_attach_contrat(runner, mock_api, option, type_):
    route = mock_api.post(f"/v1/volumes/{VOL}/attach").mock(
        return_value=httpx.Response(200, json={"id": VOL}))
    result = runner.invoke(app, ["volume", "attach", VOL, option, CIBLE])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(route.calls.last.request.content) == {"resource_type": type_, "resource_id": CIBLE}


def test_volume_list_affiche_la_cible(runner, mock_api):
    mock_api.get("/v1/volumes").mock(return_value=httpx.Response(200, json=[{
        "id": VOL, "name": "data", "region": "RNN", "size_gb": 10, "status": "attached",
        "attached_to_id": CIBLE, "attached_to_type": "container", "attached_to_name": "web-1"}]))
    result = runner.invoke(app, ["volume", "list"], env={"CCP_OUTPUT": "json"})
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)[0]["attached_to"] == "web-1"


def test_api_key_get_lit_la_liste(runner, mock_api):
    mock_api.get("/v1/api-keys").mock(return_value=httpx.Response(200, json=[
        {"id": CIBLE, "name": "ci", "prefix": "ccp_live_abcd"}]))
    result = runner.invoke(app, ["api-key", "get", CIBLE], env={"CCP_OUTPUT": "json"})
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["name"] == "ci"



def test_ip_detach_d_une_ip_de_lb_passe_par_le_lb(runner, mock_api):
    """Symétrique de `ip attach --lb` (revue de la PR #86)."""
    mock_api.get("/v1/public-ips").mock(return_value=httpx.Response(200, json=[
        {"id": IP, "load_balancer_id": CIBLE}]))
    lb = mock_api.post(f"/v1/load-balancers/{CIBLE}/detach-ip").mock(
        return_value=httpx.Response(200, json={"id": CIBLE}))
    ip = mock_api.post(f"/v1/public-ips/{IP}/detach").mock(return_value=httpx.Response(200, json={}))
    result = runner.invoke(app, ["ip", "detach", IP])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert lb.called and not ip.called
