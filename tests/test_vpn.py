"""Tests pour `cetic vpn` (gateway/peer/config/rotate/policy).

Couvre les deux modèles de clé :
    - souverain (défaut) : la CLI génère la paire localement, n'envoie que la
      clé publique, et substitue la clé privée dans le placeholder du .conf
    - géré (--managed) : la plateforme renvoie un .conf complet, écrit tel quel
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx

from cetic.main import app


GW_ID = "11111111-2222-3333-4444-555555555555"
PEER_ID = "99999999-8888-7777-6666-555555555555"
VPC_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

PLACEHOLDER = "__INJECT_LOCAL_PRIVATE_KEY__"


def _gw(
    gid: str = GW_ID,
    name: str = "vpn-prod",
    region: str = "PAR",
    status: str = "active",
    endpoint_host: str = "vpn.par.cloud.cetic-group.com",
) -> dict[str, Any]:
    return {
        "id": gid,
        "name": name,
        "region": region,
        "plan": "small",
        "vpc_id": VPC_ID,
        "vpc_ids": [VPC_ID],
        "status": status,
        "endpoint_host": endpoint_host,
        "endpoint_port": 51820,
        "peer_pool_cidr": "10.80.0.0/24",
        "created_at": "2026-06-10T10:00:00Z",
    }


def _config_with_placeholder() -> str:
    return (
        "[Interface]\n"
        f"PrivateKey = {PLACEHOLDER}\n"
        "Address = 10.80.0.5/32\n"
        "\n"
        "[Peer]\n"
        "PublicKey = GWPUBKEYBASE64==\n"
        "Endpoint = vpn.par.cloud.cetic-group.com:51820\n"
        "AllowedIPs = 10.0.0.0/16\n"
        "PersistentKeepalive = 25\n"
    )


def _config_managed() -> str:
    return (
        "[Interface]\n"
        "PrivateKey = MANAGEDPRIVATEKEYBASE64==\n"
        "Address = 10.80.0.6/32\n"
        "\n"
        "[Peer]\n"
        "PublicKey = GWPUBKEYBASE64==\n"
        "Endpoint = vpn.par.cloud.cetic-group.com:51820\n"
        "AllowedIPs = 10.0.0.0/16\n"
        "PersistentKeepalive = 25\n"
    )


def _peer(
    model: str = "A",
    config: str | None = None,
    peer_type: str = "client",
) -> dict[str, Any]:
    return {
        "id": PEER_ID,
        "gateway_id": GW_ID,
        "name": "alice-laptop",
        "ip": "10.80.0.5",
        "public_key": "PEERPUBKEYBASE64==",
        "model": model,
        "peer_type": peer_type,
        "one_time": False,
        "store_private_key": model == "B",
        "revealed": False,
        "last_handshake_at": None,
        "created_at": "2026-06-10T10:00:00Z",
        "config": config,
    }


# ---------------------------------------------------------------------------
# Enregistrement de la sous-app
# ---------------------------------------------------------------------------


def test_vpn_subapp_registered():
    names = [g.name for g in app.registered_groups]
    assert "vpn" in names


# ---------------------------------------------------------------------------
# gateway list / get / delete
# ---------------------------------------------------------------------------


def test_gateway_list_table(runner, mock_api):
    mock_api.get("/v1/vpn/gateways").mock(
        return_value=httpx.Response(200, json=[_gw(), _gw(name="vpn-dev")])
    )
    result = runner.invoke(app, ["vpn", "gateway", "list"])
    assert result.exit_code == 0, result.stdout
    assert "vpn-prod" in result.stdout
    assert "PAR" in result.stdout
    assert "vpn.par.cloud.cetic-group.com" in result.stdout


def test_gateway_get(runner, mock_api):
    mock_api.get(f"/v1/vpn/gateways/{GW_ID}").mock(
        return_value=httpx.Response(200, json=_gw())
    )
    result = runner.invoke(app, ["vpn", "gateway", "get", GW_ID])
    assert result.exit_code == 0, result.stdout
    assert "vpn-prod" in result.stdout


def test_gateway_get_404(runner, mock_api):
    mock_api.get(f"/v1/vpn/gateways/{GW_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(app, ["vpn", "gateway", "get", GW_ID])
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


def test_gateway_create_sends_correct_body(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_gw(region="RNN"))

    mock_api.post("/v1/vpn/gateways").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "vpn", "gateway", "create",
            "--name", "vpn-prod", "--region", "RNN",
            "--vpc", VPC_ID,
            "--dns", "10.0.0.2",
            "--pool-cidr", "10.80.0.0/24",
            "--tags", "prod",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {
        "name": "vpn-prod",
        "region": "RNN",
        "vpc_ids": [VPC_ID],
        "plan": "small",
        "dns": "10.0.0.2",
        "peer_pool_cidr": "10.80.0.0/24",
        "tags": ["prod"],
    }
    assert "Passerelle VPN créée" in result.stdout


def test_gateway_create_multi_vpc(runner, mock_api):
    captured: dict[str, Any] = {}
    vpc2 = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_gw())

    mock_api.post("/v1/vpn/gateways").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["vpn", "gateway", "create", "--name", "g", "--region", "PAR",
         "--vpc", VPC_ID, "--vpc", vpc2],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["vpc_ids"] == [VPC_ID, vpc2]


def test_gateway_delete_success(runner, mock_api):
    mock_api.delete(f"/v1/vpn/gateways/{GW_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["vpn", "gateway", "delete", GW_ID, "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "supprimée" in result.stdout


def test_gateway_delete_aborted_without_yes(runner, mock_api):
    result = runner.invoke(app, ["vpn", "gateway", "delete", GW_ID], input="n\n")
    assert result.exit_code != 0
    assert not any(call.request.method == "DELETE" for call in mock_api.calls)


# ---------------------------------------------------------------------------
# peer add — Model A (souverain, défaut)
# ---------------------------------------------------------------------------


def test_peer_add_sovereign_generates_local_key(runner, mock_api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_peer(model="A", config=_config_with_placeholder()))

    mock_api.post(f"/v1/vpn/gateways/{GW_ID}/peers").mock(side_effect=_capture)
    result = runner.invoke(app, ["vpn", "peer", "add", GW_ID, "alice-laptop"])
    assert result.exit_code == 0, result.stdout

    # Le body ne contient QUE la clé publique (la privée ne quitte pas le poste).
    assert "public_key" in captured["body"]
    assert "store_private_key" not in captured["body"]
    pub = captured["body"]["public_key"]
    # Clé publique Curve25519 : 32 octets en base64.
    assert len(base64.b64decode(pub)) == 32

    # Le .conf est écrit, placeholder substitué par une vraie clé locale.
    conf = Path(tmp_path / "alice-laptop.conf")
    assert conf.is_file()
    content = conf.read_text(encoding="utf-8")
    assert PLACEHOLDER not in content
    assert "PrivateKey = " in content
    # Mode 0600.
    assert (conf.stat().st_mode & 0o777) == 0o600

    # Message d'utilisation côté client : importer dans l'app WireGuard.
    assert "WireGuard" in result.stdout
    assert "wireguard.com/install" in result.stdout
    # Body sans peer_type/site_cidrs pour un peer client.
    assert "peer_type" not in captured["body"]
    assert "site_cidrs" not in captured["body"]


def test_peer_add_sovereign_rejects_managed_subflags(runner, mock_api):
    result = runner.invoke(app, ["vpn", "peer", "add", GW_ID, "alice", "--no-store"])
    assert result.exit_code == 1
    assert "--managed" in result.stdout
    assert not any(call.request.method == "POST" for call in mock_api.calls)


# ---------------------------------------------------------------------------
# peer add — site-à-site (--site)
# ---------------------------------------------------------------------------


def test_peer_add_site_sends_peer_type_and_cidrs(runner, mock_api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json=_peer(
                model="A", config=_config_with_placeholder(), peer_type="site"
            ),
        )

    mock_api.post(f"/v1/vpn/gateways/{GW_ID}/peers").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "vpn", "peer", "add", GW_ID, "datacenter-paris",
            "--site", "192.168.10.0/24,192.168.20.0/24",
        ],
    )
    assert result.exit_code == 0, result.stdout

    # peer_type=site + site_cidrs aplaties depuis la liste séparée par virgule.
    assert captured["body"]["peer_type"] == "site"
    assert captured["body"]["site_cidrs"] == ["192.168.10.0/24", "192.168.20.0/24"]
    # Model A par défaut (pas de --managed) → seule la clé publique envoyée.
    assert "public_key" in captured["body"]
    assert "store_private_key" not in captured["body"]

    conf = Path(tmp_path / "datacenter-paris.conf")
    assert conf.is_file()

    # Message d'utilisation côté site : routeur/pare-feu distant + IP forwarding.
    assert "Site-à-site" in result.stdout
    assert "routeur" in result.stdout
    assert "routage IP" in result.stdout


def test_peer_add_site_repeated_flag(runner, mock_api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json=_peer(model="A", config=_config_with_placeholder(), peer_type="site"),
        )

    mock_api.post(f"/v1/vpn/gateways/{GW_ID}/peers").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "vpn", "peer", "add", GW_ID, "site-b",
            "--site", "10.10.0.0/16", "--site", "10.20.0.0/16",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["site_cidrs"] == ["10.10.0.0/16", "10.20.0.0/16"]


def test_peer_add_site_managed(runner, mock_api, tmp_path, monkeypatch):
    """--site + --managed : la plateforme génère la clé, peer_type reste site."""
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201, json=_peer(model="B", config=_config_managed(), peer_type="site")
        )

    mock_api.post(f"/v1/vpn/gateways/{GW_ID}/peers").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["vpn", "peer", "add", GW_ID, "site-c", "--site", "172.16.0.0/24", "--managed"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["peer_type"] == "site"
    assert captured["body"]["site_cidrs"] == ["172.16.0.0/24"]
    # Mode géré : pas de clé publique envoyée.
    assert "public_key" not in captured["body"]
    assert captured["body"]["store_private_key"] is True


# ---------------------------------------------------------------------------
# peer add — Model B (--managed)
# ---------------------------------------------------------------------------


def test_peer_add_managed_writes_config_as_is(runner, mock_api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_peer(model="B", config=_config_managed()))

    mock_api.post(f"/v1/vpn/gateways/{GW_ID}/peers").mock(side_effect=_capture)
    result = runner.invoke(app, ["vpn", "peer", "add", GW_ID, "bob-laptop", "--managed"])
    assert result.exit_code == 0, result.stdout

    # Pas de clé publique envoyée en mode géré.
    assert "public_key" not in captured["body"]
    assert captured["body"]["store_private_key"] is True
    assert captured["body"]["one_time"] is False

    conf = Path(tmp_path / "bob-laptop.conf")
    assert conf.is_file()
    content = conf.read_text(encoding="utf-8")
    assert "MANAGEDPRIVATEKEYBASE64==" in content
    assert (conf.stat().st_mode & 0o777) == 0o600


def test_peer_add_managed_no_store_one_time(runner, mock_api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_peer(model="B", config=_config_managed()))

    mock_api.post(f"/v1/vpn/gateways/{GW_ID}/peers").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["vpn", "peer", "add", GW_ID, "bob", "--managed", "--no-store", "--one-time"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["store_private_key"] is False
    assert captured["body"]["one_time"] is True


# ---------------------------------------------------------------------------
# peer list / rm
# ---------------------------------------------------------------------------


def test_peer_list(runner, mock_api):
    mock_api.get(f"/v1/vpn/gateways/{GW_ID}/peers").mock(
        return_value=httpx.Response(200, json=[_peer(model="A")])
    )
    result = runner.invoke(app, ["vpn", "peer", "list", GW_ID])
    assert result.exit_code == 0, result.stdout
    assert "alice-laptop" in result.stdout
    assert "10.80.0.5" in result.stdout


def test_peer_rm_success(runner, mock_api):
    mock_api.delete(f"/v1/vpn/gateways/{GW_ID}/peers/{PEER_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["vpn", "peer", "rm", GW_ID, PEER_ID, "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "retiré" in result.stdout


# ---------------------------------------------------------------------------
# config (re-download)
# ---------------------------------------------------------------------------


def test_config_download_managed(runner, mock_api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_api.get(f"/v1/vpn/gateways/{GW_ID}/peers/{PEER_ID}/config").mock(
        return_value=httpx.Response(200, json={"config": _config_managed()})
    )
    result = runner.invoke(app, ["vpn", "config", GW_ID, PEER_ID, "--name", "bob"])
    assert result.exit_code == 0, result.stdout
    conf = Path(tmp_path / "bob.conf")
    assert conf.is_file()
    assert (conf.stat().st_mode & 0o777) == 0o600
    # Sans peer_type → message client par défaut.
    assert "WireGuard" in result.stdout


def test_config_download_site_hint(runner, mock_api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mock_api.get(f"/v1/vpn/gateways/{GW_ID}/peers/{PEER_ID}/config").mock(
        return_value=httpx.Response(
            200, json={"config": _config_managed(), "peer_type": "site"}
        )
    )
    result = runner.invoke(app, ["vpn", "config", GW_ID, PEER_ID, "--name", "site-a"])
    assert result.exit_code == 0, result.stdout
    assert "Site-à-site" in result.stdout
    assert "routeur" in result.stdout


def test_config_download_409_sovereign(runner, mock_api):
    mock_api.get(f"/v1/vpn/gateways/{GW_ID}/peers/{PEER_ID}/config").mock(
        return_value=httpx.Response(409, json={"detail": "Clé inconnue du serveur — rotation requise."})
    )
    result = runner.invoke(app, ["vpn", "config", GW_ID, PEER_ID])
    assert result.exit_code == 1
    assert "Conflit" in result.stdout


def test_config_download_410_one_time(runner, mock_api):
    mock_api.get(f"/v1/vpn/gateways/{GW_ID}/peers/{PEER_ID}/config").mock(
        return_value=httpx.Response(410, json={"detail": "Config déjà récupérée."})
    )
    result = runner.invoke(app, ["vpn", "config", GW_ID, PEER_ID])
    assert result.exit_code == 1
    assert "Indisponible" in result.stdout


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------


def test_rotate_sovereign_regen_local(runner, mock_api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"config": _config_with_placeholder(), "public_key": "NEWPUB=="}
        )

    mock_api.post(f"/v1/vpn/gateways/{GW_ID}/peers/{PEER_ID}/rotate").mock(side_effect=_capture)
    result = runner.invoke(app, ["vpn", "rotate", GW_ID, PEER_ID, "--name", "alice"])
    assert result.exit_code == 0, result.stdout

    # Nouvelle clé publique envoyée.
    assert "public_key" in captured["body"]
    assert len(base64.b64decode(captured["body"]["public_key"])) == 32

    conf = Path(tmp_path / "alice.conf")
    content = conf.read_text(encoding="utf-8")
    assert PLACEHOLDER not in content
    assert (conf.stat().st_mode & 0o777) == 0o600


def test_rotate_managed_server_regen(runner, mock_api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"config": _config_managed(), "public_key": "NEWPUB=="})

    mock_api.post(f"/v1/vpn/gateways/{GW_ID}/peers/{PEER_ID}/rotate").mock(side_effect=_capture)
    result = runner.invoke(app, ["vpn", "rotate", GW_ID, PEER_ID, "--managed", "--name", "bob"])
    assert result.exit_code == 0, result.stdout
    # Pas de clé publique envoyée : la plateforme régénère.
    assert "public_key" not in captured["body"]
    conf = Path(tmp_path / "bob.conf")
    assert "MANAGEDPRIVATEKEYBASE64==" in conf.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# policy get / set
# ---------------------------------------------------------------------------


def test_policy_get(runner, mock_api):
    mock_api.get(f"/v1/vpn/gateways/{GW_ID}/policy").mock(
        return_value=httpx.Response(
            200, json={"groups": {"admins": ["alice"]}, "rules": [{"allow": "10.0.0.0/16"}]}
        )
    )
    result = runner.invoke(app, ["vpn", "policy", "get", GW_ID])
    assert result.exit_code == 0, result.stdout
    assert "admins" in result.stdout
    assert "10.0.0.0/16" in result.stdout


def test_policy_set_from_file(runner, mock_api, tmp_path):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"groups": {}, "rules": [{"allow": "10.0.0.0/16"}]})

    mock_api.put(f"/v1/vpn/gateways/{GW_ID}/policy").mock(side_effect=_capture)

    pfile = tmp_path / "policy.json"
    pfile.write_text(
        json.dumps({"groups": {"admins": ["alice"]}, "rules": [{"allow": "10.0.0.0/16"}]}),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["vpn", "policy", "set", GW_ID, "--file", str(pfile)])
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["groups"] == {"admins": ["alice"]}
    assert captured["body"]["rules"] == [{"allow": "10.0.0.0/16"}]
    assert "mise à jour" in result.stdout


def test_policy_set_from_stdin(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"groups": {}, "rules": []})

    mock_api.put(f"/v1/vpn/gateways/{GW_ID}/policy").mock(side_effect=_capture)
    result = runner.invoke(
        app, ["vpn", "policy", "set", GW_ID], input='{"groups": {}, "rules": []}'
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"groups": {}, "rules": []}


def test_policy_set_invalid_json(runner, mock_api):
    result = runner.invoke(app, ["vpn", "policy", "set", GW_ID], input="{not json")
    assert result.exit_code == 1
    assert "JSON invalide" in result.stdout
    assert not any(call.request.method == "PUT" for call in mock_api.calls)


# ---------------------------------------------------------------------------
# Anti-leak : aucun jargon infra dans l'aide
# ---------------------------------------------------------------------------


def test_help_no_infra_jargon(runner):
    result = runner.invoke(app, ["vpn", "--help"])
    assert result.exit_code == 0
    for term in ("WireGuard", "LXC", "FRR", "nftables", "Proxmox"):
        assert term not in result.stdout
