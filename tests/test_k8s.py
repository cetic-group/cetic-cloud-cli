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
    os_image: str = "flatcar",
) -> dict[str, Any]:
    return {
        "id": cid,
        "name": name,
        "region": "fr-par-1",
        "k8s_version": "v1.31.0",
        "status": status,
        "tier": tier,
        "os_image": os_image,
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


# ---------------------------------------------------------------------------
# Multi-OS — flag --os, résolution template, colonnes OS (issue #460)
# ---------------------------------------------------------------------------


def _template(
    os_slug: str = "flatcar",
    os_key: str = "kube-v1-31-0-flatcar",
    k8s_version: str = "v1.31.0",
    region: str = "fr-par-1",
) -> dict[str, Any]:
    return {
        "os": os_slug,
        "os_key": os_key,
        "os_label": {"flatcar": "Flatcar", "ubuntu": "Ubuntu", "rocky9": "Rocky Linux 9"}[os_slug],
        "display_name": f"Kubernetes {k8s_version} ({os_slug})",
        "k8s_version": k8s_version,
        "region": region,
        "built_at": "2026-06-18T00:00:00Z",
    }


def test_create_default_os_is_flatcar(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_cluster(status="Provisioning"))

    mock_api.post("/v1/k8s/clusters").mock(side_effect=_capture)
    # --template fourni → pas de lookup templates.
    result = runner.invoke(app, _create_args())
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["os_image"] == "flatcar"
    assert captured["body"]["os_template_key"] == "clks-capi-debian-13"


def test_create_os_explicit_sent(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_cluster(os_image="ubuntu", status="Provisioning"))

    mock_api.post("/v1/k8s/clusters").mock(side_effect=_capture)
    result = runner.invoke(app, _create_args("--os", "ubuntu"))
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["os_image"] == "ubuntu"


def test_create_os_case_insensitive(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_cluster(os_image="rocky9"))

    mock_api.post("/v1/k8s/clusters").mock(side_effect=_capture)
    result = runner.invoke(app, _create_args("--os", "ROCKY9"))
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["os_image"] == "rocky9"


def test_create_os_invalid_rejected(runner, mock_api):
    result = runner.invoke(app, _create_args("--os", "windows"))
    assert result.exit_code == 1
    assert "invalide" in result.stdout.lower()
    assert not any(call.request.method == "POST" for call in mock_api.calls)


def test_create_resolves_template_from_os_and_version(runner, mock_api):
    """Sans --template : lookup /v1/k8s/templates puis envoi de l'os_key matché."""
    captured: dict[str, Any] = {}

    mock_api.get("/v1/k8s/templates").mock(
        return_value=httpx.Response(200, json=[
            _template("flatcar", "kube-v1-31-0-flatcar"),
            _template("ubuntu", "kube-v1-31-0-ubuntu"),
            _template("rocky9", "kube-v1-31-0-rocky9"),
        ])
    )

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_cluster(os_image="ubuntu"))

    mock_api.post("/v1/k8s/clusters").mock(side_effect=_capture)
    # Pas de --template.
    args = [
        "k8s", "create",
        "--name", "ccks-prod",
        "--region", "fr-par-1",
        "--vpc", VPC_ID,
        "--vnet", VNET_ID,
        "--os", "ubuntu",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["os_image"] == "ubuntu"
    assert captured["body"]["os_template_key"] == "kube-v1-31-0-ubuntu"


def test_create_no_matching_template_errors(runner, mock_api):
    mock_api.get("/v1/k8s/templates").mock(
        return_value=httpx.Response(200, json=[_template("flatcar", "kube-v1-31-0-flatcar")])
    )
    args = [
        "k8s", "create",
        "--name", "ccks-prod",
        "--region", "fr-par-1",
        "--vpc", VPC_ID,
        "--vnet", VNET_ID,
        "--os", "rocky9",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "Aucun template" in result.stdout
    assert not any(call.request.method == "POST" for call in mock_api.calls)


def test_resolve_os_template_key_unit():
    from cetic.commands.k8s import _resolve_os_template_key

    templates = [
        _template("flatcar", "k-flatcar"),
        _template("ubuntu", "k-ubuntu"),
    ]
    assert _resolve_os_template_key(
        templates, region="fr-par-1", k8s_version="v1.31.0", os_slug="ubuntu"
    ) == "k-ubuntu"
    # Mauvaise région → None.
    assert _resolve_os_template_key(
        templates, region="other", k8s_version="v1.31.0", os_slug="ubuntu"
    ) is None
    # OS absent → None.
    assert _resolve_os_template_key(
        templates, region="fr-par-1", k8s_version="v1.31.0", os_slug="rocky9"
    ) is None


def test_list_shows_os_column(runner, mock_api):
    mock_api.get("/v1/k8s/clusters").mock(
        return_value=httpx.Response(200, json=[
            _cluster(CLUSTER_ID, "ccks-ubuntu", os_image="ubuntu"),
            _cluster("22222222-3333-4444-5555-666666666666", "ccks-rocky", os_image="rocky9"),
        ])
    )
    result = runner.invoke(app, ["k8s", "list"])
    assert result.exit_code == 0, result.stdout
    assert "OS" in result.stdout
    assert "Ubuntu" in result.stdout
    assert "Rocky Linux 9" in result.stdout


def test_get_exposes_os_image_json(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get(f"/v1/k8s/clusters/{CLUSTER_ID}").mock(
        return_value=httpx.Response(200, json=_cluster(os_image="ubuntu")),
    )
    result = runner.invoke(app, ["k8s", "get", CLUSTER_ID])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    # slug brut conservé + libellé dérivé.
    assert data["os_image"] == "ubuntu"
    assert data["os"] == "Ubuntu"


def test_templates_shows_os_slug(runner, mock_api):
    mock_api.get("/v1/k8s/templates").mock(
        return_value=httpx.Response(200, json=[_template("rocky9", "kube-v1-31-0-rocky9")])
    )
    result = runner.invoke(app, ["k8s", "templates"])
    assert result.exit_code == 0, result.stdout
    assert "rocky9" in result.stdout
    assert "Rocky Linux 9" in result.stdout


# ---------------------------------------------------------------------------
# Per-pool k8s version — --pool-version (create), --version (pool create/update),
# colonne Version (pool list) (issue #470)
# ---------------------------------------------------------------------------


def test_create_pool_version_sent_in_initial_pool(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_cluster(status="Provisioning"))

    mock_api.post("/v1/k8s/clusters").mock(side_effect=_capture)
    result = runner.invoke(app, _create_args("--version", "v1.32.0", "--pool-version", "v1.31.0"))
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["k8s_version"] == "v1.32.0"
    assert captured["body"]["initial_pool"]["k8s_version"] == "v1.31.0"


def test_create_pool_version_omitted_inherits(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_cluster(status="Provisioning"))

    mock_api.post("/v1/k8s/clusters").mock(side_effect=_capture)
    result = runner.invoke(app, _create_args())
    assert result.exit_code == 0, result.stdout
    assert "k8s_version" not in captured["body"]["initial_pool"]


def test_create_pool_version_invalid_rejected(runner, mock_api):
    result = runner.invoke(app, _create_args("--pool-version", "1.x"))
    assert result.exit_code != 0
    assert not any(call.request.method == "POST" for call in mock_api.calls)


def test_pool_create_version_sent(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_pool())

    mock_api.post(f"/v1/k8s/clusters/{CLUSTER_ID}/node-pools").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "k8s", "pool", "create", CLUSTER_ID,
        "--name", "gpu-pool",
        "--version", "v1.31.0",
    ])
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["k8s_version"] == "v1.31.0"


def test_pool_create_version_omitted_not_in_body(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_pool())

    mock_api.post(f"/v1/k8s/clusters/{CLUSTER_ID}/node-pools").mock(side_effect=_capture)
    result = runner.invoke(app, ["k8s", "pool", "create", CLUSTER_ID, "--name", "gpu-pool"])
    assert result.exit_code == 0, result.stdout
    assert "k8s_version" not in captured["body"]


def test_pool_create_version_invalid_rejected(runner, mock_api):
    result = runner.invoke(app, [
        "k8s", "pool", "create", CLUSTER_ID,
        "--name", "gpu-pool",
        "--version", "nope",
    ])
    assert result.exit_code != 0
    assert not any(call.request.method == "POST" for call in mock_api.calls)


def test_pool_update_version_sent(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_pool())

    mock_api.patch(f"/v1/k8s/clusters/{CLUSTER_ID}/node-pools/{POOL_ID}").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "k8s", "pool", "update", CLUSTER_ID, POOL_ID,
        "--version", "1.31.2",
    ])
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["k8s_version"] == "1.31.2"


def test_pool_update_version_invalid_rejected(runner, mock_api):
    result = runner.invoke(app, [
        "k8s", "pool", "update", CLUSTER_ID, POOL_ID,
        "--version", "v1",
    ])
    assert result.exit_code != 0
    assert not any(call.request.method == "PATCH" for call in mock_api.calls)


def test_pool_list_shows_version_column(runner, mock_api):
    pools = [
        {"id": POOL_ID, "name": "pinned", "plan": "small", "replicas": 1,
         "k8s_version": "v1.31.0", "status": "Available"},
        {"id": "00000000-1111-2222-3333-444444444444", "name": "inherits",
         "plan": "small", "replicas": 2, "k8s_version": None, "status": "Available"},
    ]
    mock_api.get(f"/v1/k8s/clusters/{CLUSTER_ID}/node-pools").mock(
        return_value=httpx.Response(200, json=pools)
    )
    mock_api.get(f"/v1/k8s/clusters/{CLUSTER_ID}").mock(
        return_value=httpx.Response(200, json=_cluster())  # CP = v1.31.0
    )
    result = runner.invoke(app, ["k8s", "pool", "list", CLUSTER_ID])
    assert result.exit_code == 0, result.stdout
    assert "Version" in result.stdout
    assert "v1.31.0" in result.stdout
    assert "héritée" in result.stdout
