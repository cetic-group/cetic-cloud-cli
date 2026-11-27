"""IP, volumes et clés d'API alignés sur l'API.

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
    """Symétrique de `ip attach --lb`."""
    mock_api.get("/v1/public-ips").mock(return_value=httpx.Response(200, json=[
        {"id": IP, "load_balancer_id": CIBLE}]))
    lb = mock_api.post(f"/v1/load-balancers/{CIBLE}/detach-ip").mock(
        return_value=httpx.Response(200, json={"id": CIBLE}))
    ip = mock_api.post(f"/v1/public-ips/{IP}/detach").mock(return_value=httpx.Response(200, json={}))
    result = runner.invoke(app, ["ip", "detach", IP])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert lb.called and not ip.called


@pytest.mark.parametrize("champ,route", [
    ("app_gateway_id", "/v1/app-gateways/{}/detach-ip"),
    ("bastion_id", "/v1/bastions/{}/detach-ip"),
    ("vpn_gateway_id", "/v1/vpn/gateways/{}/detach-ip"),
    ("k8s_cluster_id", "/v1/k8s/clusters/{}/detach-ip"),
])
def test_ip_detach_passe_par_la_ressource_qui_tient_l_ip(runner, mock_api, champ, route):
    """Une IP tenue par une gateway, un bastion ou une passerelle VPN (clé
    étrangère) : la route générique la passait en `allocated` sans nettoyer la
    ressource."""
    mock_api.get("/v1/public-ips").mock(return_value=httpx.Response(200, json=[
        {"id": IP, champ: CIBLE}]))
    res = mock_api.post(route.format(CIBLE)).mock(return_value=httpx.Response(200, json={}))
    gen = mock_api.post(f"/v1/public-ips/{IP}/detach").mock(return_value=httpx.Response(200, json={}))
    result = runner.invoke(app, ["ip", "detach", IP])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert res.called and not gen.called



def test_ip_detach_d_une_ip_de_base_refuse_et_oriente(runner, mock_api):
    """Revue : la route générique laissait `db_instances.public_ip_id` pointer
    sur une IP libérée ; la CLI renvoie vers `cetic db <moteur> detach-ip`."""
    mock_api.get("/v1/public-ips").mock(return_value=httpx.Response(200, json=[
        {"id": IP, "db_instance_id": CIBLE, "db_instance_name": "app-db"}]))
    gen = mock_api.post(f"/v1/public-ips/{IP}/detach").mock(return_value=httpx.Response(200, json={}))
    result = runner.invoke(app, ["ip", "detach", IP])
    assert result.exit_code == 1
    assert "detach-ip" in result.stdout and "app-db" in result.stdout
    assert not gen.called


def test_ip_detach_uuid_en_majuscules_passe_par_le_lb(runner, mock_api):
    """Revue : un UUID en majuscules retombait en silence sur la route
    générique (détachement incomplet d'une IP de LB)."""
    ip_hex = "abcdef12-3456-7890-abcd-ef1234567890"
    mock_api.get("/v1/public-ips").mock(return_value=httpx.Response(200, json=[
        {"id": ip_hex, "load_balancer_id": CIBLE}]))
    lb = mock_api.post(f"/v1/load-balancers/{CIBLE}/detach-ip").mock(
        return_value=httpx.Response(200, json={}))
    mock_api.post(f"/v1/public-ips/{ip_hex.upper()}/detach").mock(
        return_value=httpx.Response(200, json={}))
    result = runner.invoke(app, ["ip", "detach", ip_hex.upper()])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert lb.called


def test_bucket_key_revoke_sans_bucket_404_indique_bucket(runner, mock_api):
    mock_api.delete("/v1/object-storage/keys/k1").mock(
        return_value=httpx.Response(404, json={"detail": "Clé introuvable"}))
    result = runner.invoke(app, ["bucket", "key", "revoke", "k1", "--yes"])
    assert result.exit_code == 1
    assert "--bucket" in result.stdout
