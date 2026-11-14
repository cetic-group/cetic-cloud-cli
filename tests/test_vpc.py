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


# ---------------------------------------------------------------------------
# IP de sortie dédiée du VPC — cetic-group/cetic-cloud-platform#1734
# ---------------------------------------------------------------------------

EGRESS_IP = "203.0.113.7"


def _flat(result: Any) -> str:
    """Sortie sans les retours à la ligne de Rich (largeur 80 sous CliRunner)."""
    return " ".join(result.output.split())


def _vpc_egress(*, dedicated: bool, ip: str | None = None,
                status: str | None = None, **kw: Any) -> dict[str, Any]:
    v = _vpc(**kw)
    v.update({
        "dedicated_egress_ip": dedicated, "egress_ip": ip,
        "egress_ip_status": status,
    })
    return v


def _capture_post(captured: dict[str, Any], response: dict[str, Any]):
    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=response)
    return _handler


def test_create_dedicated_egress_ip_sends_true(runner, mock_api) -> None:
    captured: dict[str, Any] = {}
    mock_api.post("/v1/vpcs").mock(side_effect=_capture_post(
        captured, _vpc_egress(dedicated=True, ip=EGRESS_IP, status="allocated")))
    result = runner.invoke(app, [
        "vpc", "create", "--name", "prod", "--region", "RNN", "--dedicated-egress-ip",
    ])
    assert result.exit_code == 0, result.output
    assert captured == {"name": "prod", "region": "RNN", "dedicated_egress_ip": True}
    assert EGRESS_IP in _flat(result)


def test_create_without_option_does_not_send_field(runner, mock_api) -> None:
    captured: dict[str, Any] = {}
    mock_api.post("/v1/vpcs").mock(side_effect=_capture_post(
        captured, _vpc_egress(dedicated=False)))
    result = runner.invoke(app, ["vpc", "create", "--name", "prod", "--region", "RNN"])
    assert result.exit_code == 0, result.output
    assert "dedicated_egress_ip" not in captured


def test_create_dedicated_ignored_by_platform_fails(runner, mock_api) -> None:
    # Plateforme antérieure : le champ est ignoré, la réponse ne le porte pas.
    mock_api.post("/v1/vpcs").mock(return_value=httpx.Response(201, json=_vpc()))
    result = runner.invoke(app, [
        "vpc", "create", "--name", "prod", "--region", "RNN", "--dedicated-egress-ip",
    ])
    assert result.exit_code == 1, result.output
    assert "pas pris en compte" in _flat(result)
    assert VPC_ID in _flat(result)


def test_create_dedicated_409_pool_empty_is_readable(runner, mock_api) -> None:
    mock_api.post("/v1/vpcs").mock(return_value=httpx.Response(409, json={
        "detail": "Aucune IP publique n'est disponible pour la sortie internet du VPC."}))
    result = runner.invoke(app, [
        "vpc", "create", "--name", "prod", "--region", "RNN", "--dedicated-egress-ip",
    ])
    assert result.exit_code == 1
    assert "IP de sortie dédiée indisponible" in _flat(result)
    assert "Aucune IP publique n'est disponible" in _flat(result)


def test_create_dedicated_422_not_offered_is_readable(runner, mock_api) -> None:
    mock_api.post("/v1/vpcs").mock(return_value=httpx.Response(422, json={
        "detail": "Cette installation ne propose pas d'IP de sortie dédiée."}))
    result = runner.invoke(app, [
        "vpc", "create", "--name", "prod", "--region", "RNN", "--dedicated-egress-ip",
    ])
    assert result.exit_code == 1
    assert "IP de sortie dédiée refusée" in _flat(result)
    assert "ne propose pas" in _flat(result)


def test_update_enable_dedicated_egress_ip(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_vpc_egress(
            dedicated=True, ip=EGRESS_IP, status="attaching"))

    mock_api.patch(f"/v1/vpcs/{VPC_ID}").mock(side_effect=_handler)
    result = runner.invoke(app, ["vpc", "update", VPC_ID, "--dedicated-egress-ip"])
    assert result.exit_code == 0, result.output
    assert captured == {"dedicated_egress_ip": True}
    assert EGRESS_IP in _flat(result)
    assert "mise en service" in _flat(result)


def test_update_disable_asks_confirmation_and_aborts(runner, mock_api) -> None:
    mock_api.get(f"/v1/vpcs/{VPC_ID}").mock(return_value=httpx.Response(200, json=_vpc_egress(
        dedicated=True, ip=EGRESS_IP, status="attached")))
    patch = mock_api.patch(f"/v1/vpcs/{VPC_ID}").mock(
        return_value=httpx.Response(200, json=_vpc_egress(dedicated=False)))
    result = runner.invoke(app, ["vpc", "update", VPC_ID, "--no-dedicated-egress-ip"],
                           input="n\n")
    assert result.exit_code != 0
    assert not patch.called
    assert EGRESS_IP in _flat(result)
    assert "perdue" in _flat(result)
    assert "ne pourra pas être récupérée" in _flat(result)


def test_update_disable_confirmed_sends_false(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_vpc_egress(dedicated=False))

    mock_api.get(f"/v1/vpcs/{VPC_ID}").mock(return_value=httpx.Response(200, json=_vpc_egress(
        dedicated=True, ip=EGRESS_IP, status="attached")))
    mock_api.patch(f"/v1/vpcs/{VPC_ID}").mock(side_effect=_handler)
    result = runner.invoke(app, ["vpc", "update", VPC_ID, "--no-dedicated-egress-ip"],
                           input="y\n")
    assert result.exit_code == 0, result.output
    assert captured == {"dedicated_egress_ip": False}
    assert "Partagée" in _flat(result)


def test_update_disable_yes_skips_confirmation(runner, mock_api) -> None:
    get = mock_api.get(f"/v1/vpcs/{VPC_ID}").mock(
        return_value=httpx.Response(200, json=_vpc_egress(dedicated=True, ip=EGRESS_IP)))
    mock_api.patch(f"/v1/vpcs/{VPC_ID}").mock(
        return_value=httpx.Response(200, json=_vpc_egress(dedicated=False)))
    result = runner.invoke(app, ["vpc", "update", VPC_ID, "--no-dedicated-egress-ip", "--yes"])
    assert result.exit_code == 0, result.output
    assert not get.called
    assert "perdue" not in _flat(result)


def test_update_disable_without_ip_held_needs_no_confirmation(runner, mock_api) -> None:
    mock_api.get(f"/v1/vpcs/{VPC_ID}").mock(
        return_value=httpx.Response(200, json=_vpc_egress(dedicated=False)))
    patch = mock_api.patch(f"/v1/vpcs/{VPC_ID}").mock(
        return_value=httpx.Response(200, json=_vpc_egress(dedicated=False)))
    result = runner.invoke(app, ["vpc", "update", VPC_ID, "--no-dedicated-egress-ip"])
    assert result.exit_code == 0, result.output
    assert patch.called


def test_update_name_and_tags_with_egress_option(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_vpc_egress(dedicated=True, ip=EGRESS_IP,
                                                    status="allocated", name="neuf"))

    mock_api.patch(f"/v1/vpcs/{VPC_ID}").mock(side_effect=_handler)
    result = runner.invoke(app, ["vpc", "update", VPC_ID, "--name", "neuf", "--tag", "a",
                                 "--dedicated-egress-ip"])
    assert result.exit_code == 0, result.output
    assert captured == {"name": "neuf", "tags": ["a"], "dedicated_egress_ip": True}


def test_update_without_egress_option_omits_field(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_vpc(name="neuf"))

    mock_api.patch(f"/v1/vpcs/{VPC_ID}").mock(side_effect=_handler)
    result = runner.invoke(app, ["vpc", "update", VPC_ID, "--name", "neuf"])
    assert result.exit_code == 0, result.output
    assert captured == {"name": "neuf"}


def test_update_409_inactive_vpc_is_readable(runner, mock_api) -> None:
    mock_api.patch(f"/v1/vpcs/{VPC_ID}").mock(return_value=httpx.Response(409, json={
        "detail": "Ce réseau privé n'est pas exploitable pour le moment."}))
    result = runner.invoke(app, ["vpc", "update", VPC_ID, "--dedicated-egress-ip"])
    assert result.exit_code == 1
    assert "IP de sortie dédiée indisponible" in _flat(result)
    assert "pas exploitable" in _flat(result)


def test_update_422_not_evpn_is_readable(runner, mock_api) -> None:
    mock_api.patch(f"/v1/vpcs/{VPC_ID}").mock(return_value=httpx.Response(422, json={
        "detail": "L'IP de sortie dédiée est réservée aux VPC EVPN."}))
    result = runner.invoke(app, ["vpc", "update", VPC_ID, "--dedicated-egress-ip"])
    assert result.exit_code == 1
    assert "IP de sortie dédiée refusée" in _flat(result)
    assert "EVPN" in _flat(result)


def test_update_enable_ignored_by_platform_fails(runner, mock_api) -> None:
    mock_api.patch(f"/v1/vpcs/{VPC_ID}").mock(return_value=httpx.Response(200, json=_vpc()))
    result = runner.invoke(app, ["vpc", "update", VPC_ID, "--dedicated-egress-ip"])
    assert result.exit_code == 1
    assert "pas pris en compte" in _flat(result)


def test_list_shows_egress_column(runner, mock_api) -> None:
    mock_api.get("/v1/vpcs").mock(return_value=httpx.Response(200, json=[
        _vpc_egress(dedicated=True, ip=EGRESS_IP, status="attached"),
        _vpc_egress(dedicated=False, vpc_id="66666666-7777-8888-9999-000000000000",
                    name="staging"),
    ]))
    result = runner.invoke(app, ["vpc", "list"])
    assert result.exit_code == 0, result.output
    assert "Sortie internet" in _flat(result)
    assert f"Dédiée {EGRESS_IP} (en service)" in _flat(result)
    assert "Partagée" in _flat(result)


def test_list_json_keeps_raw_egress_fields(runner, monkeypatch, mock_api) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/vpcs").mock(return_value=httpx.Response(200, json=[
        _vpc_egress(dedicated=True, ip=EGRESS_IP, status="attached"),
    ]))
    result = runner.invoke(app, ["vpc", "list"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data[0]["dedicated_egress_ip"] is True
    assert data[0]["egress_ip"] == EGRESS_IP
    # Type de l'IP de sortie : retiré de l'API (cetic-cloud-platform#1975),
    # jamais montré au client (#1977).
    assert "egress_ip_type" not in data[0]
    assert data[0]["egress_ip_status"] == "attached"


def test_get_shows_egress_label_in_table(runner, mock_api) -> None:
    mock_api.get(f"/v1/vpcs/{VPC_ID}").mock(return_value=httpx.Response(200, json=_vpc_egress(
        dedicated=True, ip=EGRESS_IP, status="allocated")))
    result = runner.invoke(app, ["vpc", "get", VPC_ID])
    assert result.exit_code == 0, result.output
    assert "sortie_internet" in _flat(result)
    assert f"Dédiée {EGRESS_IP} (réservée, pas encore en service)" in _flat(result)


def test_get_json_is_raw_response(runner, monkeypatch, mock_api) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    payload = _vpc_egress(dedicated=True, ip=EGRESS_IP, status="attached")
    mock_api.get(f"/v1/vpcs/{VPC_ID}").mock(return_value=httpx.Response(200, json=payload))
    result = runner.invoke(app, ["vpc", "get", VPC_ID])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload


def test_fmt_vpc_egress_labels() -> None:
    from cetic.commands.vpc import fmt_vpc_egress

    assert fmt_vpc_egress({}) == "Partagée"
    assert fmt_vpc_egress({"dedicated_egress_ip": False}) == "Partagée"
    assert fmt_vpc_egress({"dedicated_egress_ip": True}) == (
        "Dédiée — IP en attente d'attribution")
    assert fmt_vpc_egress({"dedicated_egress_ip": True, "egress_ip": EGRESS_IP,
                           "egress_ip_status": "error"}) == f"Dédiée {EGRESS_IP} (en erreur)"
    # Désactivée, IP pas encore rendue : on le montre.
    assert fmt_vpc_egress({"dedicated_egress_ip": False, "egress_ip": EGRESS_IP,
                           "egress_ip_status": "detaching"}) == (
        f"Partagée (IP {EGRESS_IP} : retour au pool en cours)")
