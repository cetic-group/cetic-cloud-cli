"""Tests `cetic k8s` — DNS personnalisé, état du DNS privé et kubeconfig
`--mode sso-private` (cetic-cloud-platform#2194, API de #2181 et #2167)."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from cetic.main import app

CID = "11111111-2222-3333-4444-555555555555"
BASE = f"/v1/k8s/clusters/{CID}"

CUSTOM = [{"domain": "corp.example", "servers": ["10.0.1.10", "10.0.1.11"]}]
PRIVATE_DNS = {
    "status": "pending",
    "resolver_ip": "10.0.0.2",
    "zones": [{"name": "app.internal", "status": "applied"},
              {"name": "db.internal", "status": "pending"}],
    "excluded": [{"zone": "cluster.local", "reason": "cluster_domain"}],
    "custom_domains": [{"domain": "corp.example", "servers": ["10.0.1.10", "10.0.1.11"],
                        "status": "applied", "reason": None}],
    "pending_reason": "resolver_not_ready",
    "applied_at": "2026-11-26T10:00:00Z",
    "checked_at": "2026-11-27T08:00:00Z",
    "error": None,
    "error_code": None,
}


def _cluster(**over: Any) -> dict[str, Any]:
    c = {"id": CID, "name": "ccks", "region": "RNN", "k8s_version": "1.34.8",
         "status": "active", "plan": "standard", "tier": "prod", "os_image": "flatcar",
         "oidc": None, "custom_dns": [], "private_dns": None, "node_pools": []}
    c.update(over)
    return c


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(text: str) -> str:
    return " ".join(_ANSI.sub("", text).split())


def _capture_put(mock_api, status: int = 200, payload: Any = None) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _side(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(status, json=payload if payload is not None else _cluster(
            custom_dns=captured["body"]["custom_dns"]))

    mock_api.put(f"{BASE}/custom-dns").mock(side_effect=_side)
    return captured


def _no_put(mock_api) -> bool:
    return not any(call.request.method == "PUT" for call in mock_api.calls)


# ---------------------------------------------------------------------------
# k8s get — DNS personnalisé et DNS privé
# ---------------------------------------------------------------------------


def test_get_table_shows_custom_and_private_dns(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(
        200, json=_cluster(custom_dns=CUSTOM, private_dns=PRIVATE_DNS)))
    mock_api.get("/v1/k8s/clusters/plans").mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(app, ["k8s", "get", CID])
    assert result.exit_code == 0, result.output
    out = _flat(result.stdout)
    assert "corp.example → 10.0.1.10, 10.0.1.11" in out
    assert ("en attente (résolveur DNS du VPC pas encore prêt) — 2 zone(s) du VPC, "
            "1 domaine(s) personnalisé(s), 1 exclue(s)") in out


def test_get_table_private_dns_error(runner, mock_api):
    pd = dict(PRIVATE_DNS, status="error", pending_reason=None,
              error="Le cluster n'a pas pu être joint.", error_code="cluster_unreachable")
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster(private_dns=pd)))
    mock_api.get("/v1/k8s/clusters/plans").mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(app, ["k8s", "get", CID])
    assert result.exit_code == 0, result.output
    assert "erreur : Le cluster n'a pas pu être joint." in _flat(result.stdout)


def test_get_table_without_private_dns(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    mock_api.get("/v1/k8s/clusters/plans").mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(app, ["k8s", "get", CID])
    assert result.exit_code == 0, result.output
    assert "private_dns" in result.stdout


def test_get_json_keeps_raw_private_dns(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get(BASE).mock(return_value=httpx.Response(
        200, json=_cluster(custom_dns=CUSTOM, private_dns=PRIVATE_DNS)))
    mock_api.get("/v1/k8s/clusters/plans").mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(app, ["k8s", "get", CID])
    data = json.loads(result.stdout)
    assert data["private_dns"] == PRIVATE_DNS
    assert data["custom_dns"] == CUSTOM


# ---------------------------------------------------------------------------
# k8s dns get
# ---------------------------------------------------------------------------


def test_dns_get_table(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(
        200, json=_cluster(custom_dns=CUSTOM, private_dns=PRIVATE_DNS)))
    result = runner.invoke(app, ["k8s", "dns", "get", CID])
    assert result.exit_code == 0, result.output
    out = _flat(result.stdout)
    assert "corp.example → 10.0.1.10, 10.0.1.11 (appliqué)" in out
    assert "app.internal (appliqué) ; db.internal (en attente)" in out
    assert "cluster.local (domaine du cluster)" in out
    assert "10.0.0.2" in out
    assert "résolveur DNS du VPC pas encore prêt" in out


def test_dns_get_table_nothing(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    result = runner.invoke(app, ["k8s", "dns", "get", CID])
    assert result.exit_code == 0, result.output
    assert "aucun DNS privé ni personnalisé" in _flat(result.stdout)


@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_dns_get_machine_output(runner, mock_api, monkeypatch, fmt):
    monkeypatch.setenv("CCP_OUTPUT", fmt)
    mock_api.get(BASE).mock(return_value=httpx.Response(
        200, json=_cluster(custom_dns=CUSTOM, private_dns=PRIVATE_DNS)))
    result = runner.invoke(app, ["k8s", "dns", "get", CID])
    assert result.exit_code == 0, result.output
    if fmt == "json":
        data = json.loads(result.stdout)
    else:
        import yaml
        data = yaml.safe_load(result.stdout)
    assert data["custom_dns"] == CUSTOM
    assert data["private_dns"]["status"] == "pending"


# ---------------------------------------------------------------------------
# k8s dns set / clear
# ---------------------------------------------------------------------------


def test_dns_set_replaces_list_normalised(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    cap = _capture_put(mock_api)
    result = runner.invoke(app, ["k8s", "dns", "set", CID,
                                 "-d", " Corp.Example. = 10.0.1.10, 10.0.1.11",
                                 "--domain", "lab.local.test=10.0.2.53", "-y"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {"custom_dns": [
        {"domain": "corp.example", "servers": ["10.0.1.10", "10.0.1.11"]},
        {"domain": "lab.local.test", "servers": ["10.0.2.53"]},
    ]}
    assert "DNS personnalisé enregistré" in result.stdout
    assert "aucun" in _flat(result.stderr)


def test_dns_set_identical_is_noop(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster(custom_dns=CUSTOM)))
    result = runner.invoke(app, ["k8s", "dns", "set", CID,
                                 "-d", "corp.example=10.0.1.10,10.0.1.11", "-y"])
    assert result.exit_code == 0, result.output
    assert "rien à changer" in result.stderr
    assert _no_put(mock_api)


def test_dns_set_order_of_servers_is_a_change(runner, mock_api):
    """Le premier serveur est le principal : l'inverser est un changement."""
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster(custom_dns=CUSTOM)))
    cap = _capture_put(mock_api)
    result = runner.invoke(app, ["k8s", "dns", "set", CID,
                                 "-d", "corp.example=10.0.1.11,10.0.1.10", "-y"])
    assert result.exit_code == 0, result.output
    assert cap["body"]["custom_dns"][0]["servers"] == ["10.0.1.11", "10.0.1.10"]


def test_dns_set_asks_confirmation(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    result = runner.invoke(app, ["k8s", "dns", "set", CID, "-d", "corp.example=10.0.1.10"],
                           input="n\n")
    assert result.exit_code != 0
    assert _no_put(mock_api)


def test_dns_set_json_output_is_cluster_dns(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    _capture_put(mock_api)
    result = runner.invoke(app, ["k8s", "dns", "set", CID, "-d", "corp.example=10.0.1.10",
                                 "-y"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["custom_dns"] == [{"domain": "corp.example", "servers": ["10.0.1.10"]}]


@pytest.mark.parametrize(("valeurs", "needle"), [
    (["corp.example"], "DOMAINE=IP"),
    (["=10.0.1.10"], "DOMAINE=IP"),
    (["corp..example=10.0.1.10"], "nom de domaine invalide"),
    (["10.0.0.1=10.0.1.10"], "nom de domaine invalide"),
    (["svc.cluster.local=10.0.1.10"], "domaine réservé"),
    (["corp.example=10.0.1.10,10.0.1.11,10.0.1.12"], "1 ou 2 adresses"),
    (["corp.example="], "1 ou 2 adresses"),
    (["corp.example=10.0.1.10,10.0.1.10"], "deux fois"),
    (["corp.example=10.0.1.10", "CORP.example.=10.0.1.11"], "saisi deux fois"),
    (["corp.example=10.0.1.300"], "IPv4 unicast"),
    (["corp.example=127.0.0.1"], "IPv4 unicast"),
    (["corp.example=fd00::53"], "IPv4 unicast"),
    ([f"d{i}.example=10.0.1.10" for i in range(6)], "5 domaines au plus"),
])
def test_dns_set_local_validation(runner, mock_api, valeurs, needle):
    args = ["k8s", "dns", "set", CID]
    for v in valeurs:
        args += ["-d", v]
    result = runner.invoke(app, [*args, "-y"], env={"COLUMNS": "200", "NO_COLOR": "1"})
    assert result.exit_code == 2, result.output
    assert needle in _flat(result.stderr)
    assert not mock_api.calls


def test_dns_set_api_refusal_lists_each_line(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    detail = {"code": "custom_dns_invalid",
              "message": "DNS personnalisé refusé : corrigez les lignes signalées.",
              "errors": [{"index": 0, "field": "servers", "domain": "corp.example",
                          "reason": "invalid_server",
                          "message": "10.9.9.9 : L'adresse doit appartenir à un "
                                     "sous-réseau du VPC du cluster."}]}
    mock_api.put(f"{BASE}/custom-dns").mock(return_value=httpx.Response(
        422, json={"detail": detail}))
    result = runner.invoke(app, ["k8s", "dns", "set", CID, "-d", "corp.example=10.9.9.9",
                                 "-y"])
    assert result.exit_code == 1
    err = _flat(result.stderr)
    assert "DNS personnalisé refusé" in err
    assert ("ligne 1 (corp.example) : 10.9.9.9 : L'adresse doit appartenir à un "
            "sous-réseau du VPC du cluster.") in err


def test_dns_clear(runner, mock_api):
    cap = _capture_put(mock_api)
    result = runner.invoke(app, ["k8s", "dns", "clear", CID, "--yes"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {"custom_dns": []}
    assert "DNS personnalisé retiré" in result.stdout


def test_dns_clear_asks_confirmation(runner, mock_api):
    result = runner.invoke(app, ["k8s", "dns", "clear", CID], input="n\n")
    assert result.exit_code != 0
    assert _no_put(mock_api)


def test_dns_clear_deleting_cluster(runner, mock_api):
    mock_api.put(f"{BASE}/custom-dns").mock(return_value=httpx.Response(
        409, json={"detail": "Le cluster est en cours de suppression."}))
    result = runner.invoke(app, ["k8s", "dns", "clear", CID, "-y"])
    assert result.exit_code == 1
    assert "en cours de suppression" in result.stderr


# ---------------------------------------------------------------------------
# kubeconfig --mode sso-private
# ---------------------------------------------------------------------------


def test_kubeconfig_mode_sso_private(runner, mock_api):
    captured: dict[str, Any] = {}

    def _side(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"kubeconfig": "apiVersion: v1\nkind: Config\n",
                                         "expires_at": None})

    mock_api.get(f"{BASE}/kubeconfig").mock(side_effect=_side)
    result = runner.invoke(app, ["k8s", "kubeconfig", CID, "--mode", "SSO-Private"])
    assert result.exit_code == 0, result.output
    assert captured["params"] == {"mode": "sso-private"}
    # stdout ne reçoit que le YAML (`> ~/.kube/config`).
    assert result.stdout == "apiVersion: v1\nkind: Config\n\n"
    err = _flat(result.stderr)
    assert "kubelogin" in err and "VPN ou le VNet" in err


def test_kubeconfig_mode_sso_public_has_no_private_hint(runner, mock_api):
    mock_api.get(f"{BASE}/kubeconfig").mock(return_value=httpx.Response(
        200, json={"kubeconfig": "kc"}))
    result = runner.invoke(app, ["k8s", "kubeconfig", CID, "--mode", "sso"])
    assert result.exit_code == 0, result.output
    assert "kubelogin" in result.stderr
    assert "VPN" not in result.stderr


def test_kubeconfig_sso_private_conflict(runner, mock_api):
    mock_api.get(f"{BASE}/kubeconfig").mock(return_value=httpx.Response(
        409, json={"detail": "SSO non configuré sur ce cluster (PUT /v1/k8s/clusters/{id}/oidc)."}))
    result = runner.invoke(app, ["k8s", "kubeconfig", CID, "--mode", "sso-private"])
    assert result.exit_code == 1
    assert "SSO non configuré" in result.stderr
    assert result.stdout == ""


def test_kubeconfig_help_lists_sso_private(runner):
    result = runner.invoke(app, ["k8s", "kubeconfig", "--help"],
                           env={"COLUMNS": "200", "NO_COLOR": "1"})
    assert result.exit_code == 0
    out = _flat(result.stdout)
    assert "sso-private" in out and "S256" in out


def test_kubeconfig_invalid_mode_lists_sso_private(runner, mock_api):
    result = runner.invoke(app, ["k8s", "kubeconfig", CID, "--mode", "oidc"])
    assert result.exit_code == 1
    assert "sso-private" in _flat(result.output)
    assert not mock_api.calls
