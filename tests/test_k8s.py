"""Tests pour `cetic k8s` — create --tier, list (colonne Tier), kubeconfig --mode.

Cascade côté CLI v0.17.0 d'une feature CCKS HA livrée par CCP api v2.6.9 :
- POST /v1/k8s/clusters accepte body `tier: dev|prod`
- GET /v1/k8s/clusters expose `tier`, `proxy_secondary_*`, `proxy_vip_vnet`
- GET /v1/k8s/clusters/{id}/kubeconfig?mode=private|public
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app


CLUSTER_ID = "11111111-2222-3333-4444-555555555555"
VPC_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
VNET_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _cluster(
    cid: str = CLUSTER_ID,
    name: str = "ccks-prod",
    tier: str = "dev",
    status: str = "Available",
) -> dict[str, Any]:
    return {
        "id": cid,
        "name": name,
        "region": "fr-par-1",
        "k8s_version": "v1.31.0",
        "status": status,
        "tier": tier,
        "proxy_secondary_vmid": 1234 if tier == "prod" else None,
        "proxy_secondary_node": "pve-02" if tier == "prod" else None,
        "proxy_vip_vnet": "10.42.0.10" if tier == "prod" else None,
    }


# ---------------------------------------------------------------------------
# create — flag --tier
# ---------------------------------------------------------------------------


def _create_args(*extra: str) -> list[str]:
    return [
        "k8s", "create",
        "--name", "ccks-prod",
        "--region", "fr-par-1",
        "--vpc", VPC_ID,
        "--vnet", VNET_ID,
        "--template", "clks-capi-debian-13",
        *extra,
    ]


def test_create_default_tier_is_dev(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_cluster(tier="dev", status="Provisioning"))

    mock_api.post("/v1/k8s/clusters").mock(side_effect=_capture)
    result = runner.invoke(app, _create_args())
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["tier"] == "dev"


def test_create_tier_prod(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_cluster(tier="prod", status="Provisioning"))

    mock_api.post("/v1/k8s/clusters").mock(side_effect=_capture)
    result = runner.invoke(app, _create_args("--tier", "prod"))
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["tier"] == "prod"
    assert "Cluster créé" in result.stdout


def test_create_tier_case_insensitive(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_cluster(tier="prod"))

    mock_api.post("/v1/k8s/clusters").mock(side_effect=_capture)
    result = runner.invoke(app, _create_args("--tier", "PROD"))
    assert result.exit_code == 0, result.stdout
    # Normalisé en lowercase avant d'être envoyé.
    assert captured["body"]["tier"] == "prod"


def test_create_tier_invalid_rejected(runner, mock_api):
    result = runner.invoke(app, _create_args("--tier", "staging"))
    assert result.exit_code == 1
    assert "invalide" in result.stdout.lower()
    # Aucun POST n'a été émis.
    assert not any(call.request.method == "POST" for call in mock_api.calls)


# ---------------------------------------------------------------------------
# list — colonne Tier
# ---------------------------------------------------------------------------


def test_list_table_shows_tier_column(runner, mock_api):
    mock_api.get("/v1/k8s/clusters").mock(
        return_value=httpx.Response(
            200,
            json=[
                _cluster(CLUSTER_ID, "ccks-dev", tier="dev"),
                _cluster(
                    "22222222-3333-4444-5555-666666666666",
                    "ccks-prod",
                    tier="prod",
                ),
            ],
        )
    )
    result = runner.invoke(app, ["k8s", "list"])
    assert result.exit_code == 0, result.stdout
    # En-tête de colonne présent.
    assert "Tier" in result.stdout
    # Les 2 valeurs apparaissent.
    assert "dev" in result.stdout
    assert "prod" in result.stdout
    assert "ccks-dev" in result.stdout
    assert "ccks-prod" in result.stdout


def test_list_json_exposes_tier(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    payload = [
        _cluster(CLUSTER_ID, "ccks-dev", tier="dev"),
        _cluster(
            "22222222-3333-4444-5555-666666666666",
            "ccks-prod",
            tier="prod",
        ),
    ]
    mock_api.get("/v1/k8s/clusters").mock(return_value=httpx.Response(200, json=payload))

    result = runner.invoke(app, ["k8s", "list"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 2
    # Le rendu JSON conserve le tier brut (pas le markup Rich).
    tiers = {row["tier"] for row in data}
    assert "dev" in str(tiers)
    assert "prod" in str(tiers)


def test_list_legacy_payload_without_tier(runner, mock_api):
    """Backend ancien qui ne renvoie pas encore `tier` → fallback `—`."""
    legacy = {
        "id": CLUSTER_ID,
        "name": "old-ccks",
        "region": "fr-par-1",
        "k8s_version": "v1.30.0",
        "status": "Available",
    }
    mock_api.get("/v1/k8s/clusters").mock(return_value=httpx.Response(200, json=[legacy]))
    result = runner.invoke(app, ["k8s", "list"])
    assert result.exit_code == 0, result.stdout
    assert "Tier" in result.stdout
    assert "old-ccks" in result.stdout


# ---------------------------------------------------------------------------
# get — exposition tier + proxy_secondary_*
# ---------------------------------------------------------------------------


def test_get_exposes_tier_and_proxy_secondary_fields(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get(f"/v1/k8s/clusters/{CLUSTER_ID}").mock(
        return_value=httpx.Response(200, json=_cluster(tier="prod")),
    )
    result = runner.invoke(app, ["k8s", "get", CLUSTER_ID])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert data["tier"] == "prod"
    assert data["proxy_secondary_vmid"] == 1234
    assert data["proxy_secondary_node"] == "pve-02"
    assert data["proxy_vip_vnet"] == "10.42.0.10"


# ---------------------------------------------------------------------------
# kubeconfig — flag --mode (private | public)
# ---------------------------------------------------------------------------


def test_kubeconfig_default_mode_is_private(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"kubeconfig": "apiVersion: v1\nkind: Config\n"})

    mock_api.get(f"/v1/k8s/clusters/{CLUSTER_ID}/kubeconfig").mock(side_effect=_capture)
    result = runner.invoke(app, ["k8s", "kubeconfig", CLUSTER_ID])
    assert result.exit_code == 0, result.stdout
    assert captured["params"].get("mode") == "private"
    assert "apiVersion: v1" in result.stdout


def test_kubeconfig_mode_public(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"kubeconfig": "apiVersion: v1\nkind: Config\n# public\n"})

    mock_api.get(f"/v1/k8s/clusters/{CLUSTER_ID}/kubeconfig").mock(side_effect=_capture)
    result = runner.invoke(app, ["k8s", "kubeconfig", CLUSTER_ID, "--mode", "public"])
    assert result.exit_code == 0, result.stdout
    assert captured["params"].get("mode") == "public"
    assert "# public" in result.stdout


def test_kubeconfig_mode_case_insensitive(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"kubeconfig": "kc"})

    mock_api.get(f"/v1/k8s/clusters/{CLUSTER_ID}/kubeconfig").mock(side_effect=_capture)
    result = runner.invoke(app, ["k8s", "kubeconfig", CLUSTER_ID, "--mode", "PUBLIC"])
    assert result.exit_code == 0, result.stdout
    assert captured["params"].get("mode") == "public"


def test_kubeconfig_mode_invalid_rejected(runner, mock_api):
    result = runner.invoke(app, ["k8s", "kubeconfig", CLUSTER_ID, "--mode", "internal"])
    assert result.exit_code == 1
    assert "invalide" in result.stdout.lower()
    # Aucun GET n'a été émis vers le sous-chemin kubeconfig.
    assert not any(
        "/kubeconfig" in str(call.request.url) for call in mock_api.calls
    )


# ---------------------------------------------------------------------------
# _parse_label_arg — unit tests (no HTTP)
# ---------------------------------------------------------------------------


def test_parse_label_valid_simple():
    from cetic.commands.k8s import _parse_label_arg

    key, value = _parse_label_arg("env=prod")
    assert key == "env"
    assert value == "prod"


def test_parse_label_value_with_equals():
    """Valeur qui contient un '=' → seul le premier '=' est le délimiteur."""
    from cetic.commands.k8s import _parse_label_arg

    key, value = _parse_label_arg("annotation=foo=bar")
    assert key == "annotation"
    assert value == "foo=bar"


def test_parse_label_empty_value_allowed():
    from cetic.commands.k8s import _parse_label_arg

    key, value = _parse_label_arg("tier=")
    assert key == "tier"
    assert value == ""


def test_parse_label_no_equals_raises():
    import pytest
    import typer
    from cetic.commands.k8s import _parse_label_arg

    with pytest.raises(typer.BadParameter, match="key=value"):
        _parse_label_arg("no-equals")


def test_parse_label_empty_key_raises():
    import pytest
    import typer
    from cetic.commands.k8s import _parse_label_arg

    with pytest.raises(typer.BadParameter, match="empty key"):
        _parse_label_arg("=value")


# ---------------------------------------------------------------------------
# _parse_taint_arg — unit tests (no HTTP)
# ---------------------------------------------------------------------------


def test_parse_taint_key_value_effect():
    from cetic.commands.k8s import _parse_taint_arg

    result = _parse_taint_arg("dedicated=gpu:NoSchedule")
    assert result == {"key": "dedicated", "value": "gpu", "effect": "NoSchedule"}


def test_parse_taint_key_only_effect():
    from cetic.commands.k8s import _parse_taint_arg

    result = _parse_taint_arg("spot:PreferNoSchedule")
    assert result == {"key": "spot", "value": None, "effect": "PreferNoSchedule"}


def test_parse_taint_noexecute():
    from cetic.commands.k8s import _parse_taint_arg

    result = _parse_taint_arg("maintenance=true:NoExecute")
    assert result["effect"] == "NoExecute"
    assert result["key"] == "maintenance"
    assert result["value"] == "true"


def test_parse_taint_no_colon_raises():
    import pytest
    import typer
    from cetic.commands.k8s import _parse_taint_arg

    with pytest.raises(typer.BadParameter, match="key=value:effect"):
        _parse_taint_arg("dedicated=gpu")


def test_parse_taint_invalid_effect_raises():
    import pytest
    import typer
    from cetic.commands.k8s import _parse_taint_arg

    with pytest.raises(typer.BadParameter, match="NoSchedule|PreferNoSchedule|NoExecute"):
        _parse_taint_arg("key=val:BadEffect")


def test_parse_taint_empty_key_raises():
    import pytest
    import typer
    from cetic.commands.k8s import _parse_taint_arg

    with pytest.raises(typer.BadParameter, match="empty key"):
        _parse_taint_arg(":NoSchedule")


# ---------------------------------------------------------------------------
# pool create — --label + --taint integration
# ---------------------------------------------------------------------------

POOL_ID = "cccccccc-dddd-eeee-ffff-000000000000"


def _pool(pid: str = POOL_ID, name: str = "gpu-pool") -> dict[str, Any]:
    return {"id": pid, "name": name, "plan": "small", "replicas": 1, "status": "Provisioning"}


def test_pool_create_labels_sent(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_pool())

    mock_api.post(f"/v1/k8s/clusters/{CLUSTER_ID}/node-pools").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "k8s", "pool", "create", CLUSTER_ID,
        "--name", "gpu-pool",
        "--label", "env=prod",
        "--label", "zone=fr",
    ])
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["labels"] == {"env": "prod", "zone": "fr"}
    assert "taints" not in captured["body"]


def test_pool_create_taints_sent(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_pool())

    mock_api.post(f"/v1/k8s/clusters/{CLUSTER_ID}/node-pools").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "k8s", "pool", "create", CLUSTER_ID,
        "--name", "gpu-pool",
        "--taint", "dedicated=gpu:NoSchedule",
        "--taint", "spot:PreferNoSchedule",
    ])
    assert result.exit_code == 0, result.stdout
    taints = captured["body"]["taints"]
    assert len(taints) == 2
    assert taints[0] == {"key": "dedicated", "value": "gpu", "effect": "NoSchedule"}
    assert taints[1] == {"key": "spot", "value": None, "effect": "PreferNoSchedule"}
    assert "labels" not in captured["body"]


def test_pool_create_no_labels_taints_not_in_body(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_pool())

    mock_api.post(f"/v1/k8s/clusters/{CLUSTER_ID}/node-pools").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "k8s", "pool", "create", CLUSTER_ID,
        "--name", "gpu-pool",
    ])
    assert result.exit_code == 0, result.stdout
    assert "labels" not in captured["body"]
    assert "taints" not in captured["body"]


def test_pool_create_invalid_label_rejected(runner, mock_api):
    result = runner.invoke(app, [
        "k8s", "pool", "create", CLUSTER_ID,
        "--name", "gpu-pool",
        "--label", "bad-no-equals",
    ])
    assert result.exit_code != 0


def test_pool_create_invalid_taint_effect_rejected(runner, mock_api):
    result = runner.invoke(app, [
        "k8s", "pool", "create", CLUSTER_ID,
        "--name", "gpu-pool",
        "--taint", "key=val:BadEffect",
    ])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# pool update — --label + --taint + --labels-clear / --taints-clear
# ---------------------------------------------------------------------------


def test_pool_update_labels_sent(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_pool())

    mock_api.patch(f"/v1/k8s/clusters/{CLUSTER_ID}/node-pools/{POOL_ID}").mock(
        side_effect=_capture
    )
    result = runner.invoke(app, [
        "k8s", "pool", "update", CLUSTER_ID, POOL_ID,
        "--label", "env=staging",
    ])
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["labels"] == {"env": "staging"}


def test_pool_update_labels_clear_sends_empty_dict(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_pool())

    mock_api.patch(f"/v1/k8s/clusters/{CLUSTER_ID}/node-pools/{POOL_ID}").mock(
        side_effect=_capture
    )
    result = runner.invoke(app, [
        "k8s", "pool", "update", CLUSTER_ID, POOL_ID,
        "--labels-clear",
    ])
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["labels"] == {}


def test_pool_update_taints_clear_sends_empty_list(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_pool())

    mock_api.patch(f"/v1/k8s/clusters/{CLUSTER_ID}/node-pools/{POOL_ID}").mock(
        side_effect=_capture
    )
    result = runner.invoke(app, [
        "k8s", "pool", "update", CLUSTER_ID, POOL_ID,
        "--taints-clear",
    ])
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["taints"] == []


def test_pool_update_label_and_labels_clear_conflict(runner, mock_api):
    result = runner.invoke(app, [
        "k8s", "pool", "update", CLUSTER_ID, POOL_ID,
        "--label", "env=prod",
        "--labels-clear",
    ])
    assert result.exit_code == 1
    assert "incompatibles" in result.stdout
