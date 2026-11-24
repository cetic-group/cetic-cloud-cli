"""Tests `cetic k8s` — plans CCKS, upgrades séparés, maintenance, SSO, disque
des pools (cetic-cloud-platform#2143)."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import typer

from cetic import client as client_mod
from cetic.commands import k8s as k8s_mod
from cetic.main import app

CID = "11111111-2222-3333-4444-555555555555"
PID = "99999999-8888-7777-6666-555555555555"
VPC_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
VNET_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
BASE = f"/v1/k8s/clusters/{CID}"

PLANS = [
    {"key": "starter", "tier": "dev", "price_hourly": "0", "price_monthly": "0.00",
     "currency": "EUR", "max_nodes": 10, "control_plane_replicas": 1, "etcd_members": 3,
     "etcd_volume_gb": 2, "high_availability": False, "node_ip_block_size": 16,
     "recommended_vnet_prefix": 26},
    {"key": "standard", "tier": "prod", "price_hourly": "0.027397",
     "price_monthly": "20.00", "currency": "EUR", "max_nodes": 50,
     "control_plane_replicas": 2, "etcd_members": 3, "etcd_volume_gb": 5,
     "high_availability": True, "node_ip_block_size": 176, "recommended_vnet_prefix": 24},
    {"key": "pro", "tier": "prod", "price_hourly": "0.082192", "price_monthly": "60.00",
     "currency": "EUR", "max_nodes": 150, "control_plane_replicas": 3, "etcd_members": 3,
     "etcd_volume_gb": 10, "high_availability": True, "node_ip_block_size": 176,
     "recommended_vnet_prefix": 24},
]

PATCH = {"version": "1.31.4", "os": "flatcar", "templates_ok": True,
         "plan_de_controle": True, "pools": [PID, "p2"], "duree_estimee_minutes": 25}
OPERATION = {
    "id": "op-1", "type": "patch", "origine": "manuelle", "statut": "en_cours",
    "version_depart": "1.31.3", "version_cible": "1.31.4",
    "etapes": [
        {"type": "plan_de_controle", "version": "1.31.4", "statut": "terminee"},
        {"type": "pool", "pool_id": PID, "pool_name": "default", "version": "1.31.4",
         "statut": "en_cours"},
    ],
    "created_at": "2026-11-24T10:00:00Z",
}


def _cluster(**over: Any) -> dict[str, Any]:
    c = {
        "id": CID, "name": "ccks", "region": "RNN", "k8s_version": "1.31.3",
        "status": "active", "plan": "standard", "tier": "prod", "etcd_volume_gb": 5,
        "node_ip_block_size": 176, "os_image": "flatcar", "patch_disponible": None,
        "upgrade_en_cours": None, "oidc": None,
        "node_pools": [{"id": PID, "name": "default", "plan": "small", "replicas": 3,
                        "disk_gb": 20, "k8s_version": None, "status": "active"}],
    }
    c.update(over)
    return c


def _capture(mock_route, status: int = 200, payload: Any = None) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _side(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["params"] = dict(request.url.params)
        captured["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=payload if payload is not None else _cluster())

    mock_route.mock(side_effect=_side)
    return captured


def _flat(text: str) -> str:
    """Sortie Rich sans les retours à la ligne de largeur de terminal."""
    return " ".join(text.split())


def _no_call(mock_api, method: str) -> bool:
    return not any(call.request.method == method for call in mock_api.calls)


# ---------------------------------------------------------------------------
# client — code machine des erreurs structurées
# ---------------------------------------------------------------------------


def test_api_error_keeps_code_and_body(mock_api):
    detail = {"code": "ccks_plan_node_limit", "message": "Trop de nœuds",
              "plan": "starter", "max_nodes": 10, "requested_nodes": 12}
    mock_api.get("/v1/x").mock(return_value=httpx.Response(422, json={"detail": detail}))
    with pytest.raises(client_mod.APIError) as exc:
        client_mod.get("/v1/x")
    assert exc.value.detail == "Trop de nœuds"
    assert exc.value.code == "ccks_plan_node_limit"
    assert exc.value.body == detail


def test_api_error_plain_detail_has_no_code(mock_api):
    mock_api.get("/v1/x").mock(return_value=httpx.Response(409, json={"detail": "occupé"}))
    with pytest.raises(client_mod.APIError) as exc:
        client_mod.get("/v1/x")
    assert exc.value.code is None and exc.value.body is None


# ---------------------------------------------------------------------------
# create --plan
# ---------------------------------------------------------------------------


def _create_args(*extra: str) -> list[str]:
    return ["k8s", "create", "--name", "ccks", "--region", "RNN", "--vpc", VPC_ID,
            "--vnet", VNET_ID, "--template", "kube-v1-31-0", *extra]


def test_create_plan_pro_sent(runner, mock_api):
    cap = _capture(mock_api.post("/v1/k8s/clusters"), 201, _cluster(plan="pro"))
    result = runner.invoke(app, _create_args("--plan", "PRO"))
    assert result.exit_code == 0, result.output
    assert cap["body"]["plan"] == "pro"
    assert "tier" not in cap["body"]
    assert "plan : pro" in result.stdout


def test_create_plan_invalid_rejected(runner, mock_api):
    result = runner.invoke(app, _create_args("--plan", "gold"))
    assert result.exit_code == 1
    assert "--plan invalide" in result.stdout
    assert _no_call(mock_api, "POST")


def test_create_plan_and_tier_disagree_rejected(runner, mock_api):
    result = runner.invoke(app, _create_args("--plan", "starter", "--tier", "prod"))
    assert result.exit_code == 1
    assert "désaccord" in result.stdout
    assert _no_call(mock_api, "POST")


def test_create_plan_pro_with_tier_prod_accepted(runner, mock_api):
    cap = _capture(mock_api.post("/v1/k8s/clusters"), 201, _cluster(plan="pro"))
    result = runner.invoke(app, _create_args("--plan", "pro", "--tier", "prod"))
    assert result.exit_code == 0, result.output
    assert cap["body"]["plan"] == "pro"
    assert "déprécié" in result.stderr


def test_create_node_limit_error_explained(runner, mock_api):
    detail = {"code": "ccks_plan_node_limit", "message": "Le plan Starter admet 10 nœuds.",
              "plan": "starter", "max_nodes": 10, "requested_nodes": 12}
    mock_api.post("/v1/k8s/clusters").mock(
        return_value=httpx.Response(422, json={"detail": detail}))
    result = runner.invoke(app, _create_args("--pool-replicas", "12"))
    assert result.exit_code == 1
    assert "Le plan Starter admet 10 nœuds." in result.stdout
    assert "10 nœuds au plus, 12 demandés" in result.stdout
    assert "cetic k8s set-plan" in result.stdout


def test_create_help_documents_plan_and_disk_default(runner):
    result = runner.invoke(app, ["k8s", "create", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "--plan" in result.stdout
    assert "starter" in result.stdout
    assert "DÉPRÉCIÉ" in result.stdout
    assert "20 Go" in result.stdout


# ---------------------------------------------------------------------------
# cluster-plans
# ---------------------------------------------------------------------------


def test_cluster_plans_table(runner, mock_api):
    mock_api.get("/v1/k8s/clusters/plans").mock(return_value=httpx.Response(200, json=PLANS))
    result = runner.invoke(app, ["k8s", "cluster-plans"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    for needle in ("starter", "standard", "pro", "Gratuit", "20.00 EUR/mois",
                   "60.00 EUR/mois", "150", "3 × 10 Gi", "/24"):
        assert needle in out, needle


def test_cluster_plans_json_raw(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/k8s/clusters/plans").mock(return_value=httpx.Response(200, json=PLANS))
    result = runner.invoke(app, ["k8s", "cluster-plans"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == PLANS


# ---------------------------------------------------------------------------
# get — plan, nœuds max, etcd, patch disponible, upgrade en cours, disque
# ---------------------------------------------------------------------------


def test_get_table_shows_plan_limits_and_updates(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(
        200, json=_cluster(patch_disponible=PATCH, upgrade_en_cours=OPERATION)))
    mock_api.get("/v1/k8s/clusters/plans").mock(return_value=httpx.Response(200, json=PLANS))
    result = runner.invoke(app, ["k8s", "get", CID])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "standard" in out
    assert "max_nodes" in out and "50" in out
    assert "5 Gi" in out
    assert "1.31.4 (plan de contrôle + 2 pool(s), ~25 min)" in out
    assert "patch 1.31.3 → 1.31.4, en cours (manuelle, 1/2 étapes)" in out
    assert "20 Go" in out


def test_get_json_keeps_raw_objects(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get(BASE).mock(return_value=httpx.Response(
        200, json=_cluster(patch_disponible=PATCH, upgrade_en_cours=OPERATION)))
    mock_api.get("/v1/k8s/clusters/plans").mock(return_value=httpx.Response(200, json=PLANS))
    result = runner.invoke(app, ["k8s", "get", CID])
    data = json.loads(result.stdout)
    assert data["plan"] == "standard"
    assert data["max_nodes"] == 50
    assert data["etcd_volume_gb"] == 5
    assert data["patch_disponible"] == PATCH
    assert data["upgrade_en_cours"] == OPERATION


def test_get_survives_unreadable_plan_catalog(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    mock_api.get("/v1/k8s/clusters/plans").mock(return_value=httpx.Response(500, json={}))
    result = runner.invoke(app, ["k8s", "get", CID])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["plan"] == "standard"
    assert "max_nodes" not in data


def test_list_update_column(runner, mock_api):
    mock_api.get("/v1/k8s/clusters").mock(return_value=httpx.Response(200, json=[
        _cluster(patch_disponible=PATCH),
        _cluster(id="c2", name="autre", upgrade_en_cours=OPERATION),
    ]))
    result = runner.invoke(app, ["k8s", "list"])
    assert result.exit_code == 0, result.output
    assert "patch 1.31.4 disponible" in result.stdout
    assert "en cours → 1.31.4" in result.stdout


def test_pool_list_shows_disk(runner, mock_api):
    mock_api.get(f"{BASE}/node-pools").mock(return_value=httpx.Response(200, json=[
        {"id": PID, "name": "default", "plan": "small", "replicas": 3, "disk_gb": 20,
         "status": "active"},
        {"id": "p2", "name": "old", "plan": "small", "replicas": 1, "disk_gb": None,
         "status": "active"},
    ]))
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    result = runner.invoke(app, ["k8s", "pool", "list", CID])
    assert result.exit_code == 0, result.output
    assert "Disque" in result.stdout
    assert "20 Go" in result.stdout
    assert "(plan)" in result.stdout


def test_pool_create_help_mentions_default_disk(runner):
    result = runner.invoke(app, ["k8s", "pool", "create", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "20 Go" in result.stdout


# ---------------------------------------------------------------------------
# set-plan (PATCH)
# ---------------------------------------------------------------------------


def test_set_plan_patches_plan(runner, mock_api):
    cap = _capture(mock_api.patch(BASE), 200, _cluster(plan="pro"))
    result = runner.invoke(app, ["k8s", "set-plan", CID, "--plan", "pro", "--yes"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {"plan": "pro"}
    assert "pro" in result.stdout


def test_set_plan_asks_confirmation(runner, mock_api):
    result = runner.invoke(app, ["k8s", "set-plan", CID, "--plan", "pro"], input="n\n")
    assert result.exit_code != 0
    assert _no_call(mock_api, "PATCH")


def test_set_plan_invalid(runner, mock_api):
    result = runner.invoke(app, ["k8s", "set-plan", CID, "--plan", "gold", "--yes"])
    assert result.exit_code == 1
    assert _no_call(mock_api, "PATCH")


@pytest.mark.parametrize(("status", "detail", "needle"), [
    (409, {"code": "ccks_plan_change_requires_recreation", "message": "Recréer.",
           "from": "starter", "to": "standard"}, "--plan standard"),
    (422, {"code": "ccks_node_ip_block_not_extendable", "message": "Bloc plein.",
           "node_ip_block_size": 100, "required": 176}, "Bloc d'adresses des nœuds : 100"),
    (422, {"code": "ccks_plan_node_limit", "message": "Trop.", "plan": "standard",
           "max_nodes": 50, "requested_nodes": 60}, "50 nœuds au plus, 60 demandés"),
    (402, {"code": "spend_cap_exceeded", "message": "Plafond.",
           "action_url": "/billing/caps"}, "Plafond de dépenses atteint"),
])
def test_set_plan_errors_explained(runner, mock_api, status, detail, needle):
    mock_api.patch(BASE).mock(return_value=httpx.Response(status, json={"detail": detail}))
    result = runner.invoke(app, ["k8s", "set-plan", CID, "--plan", "standard", "--yes"])
    assert result.exit_code == 1
    assert detail["message"] in result.stdout
    assert needle in result.stdout


# ---------------------------------------------------------------------------
# upgrade-version (plan de contrôle seul), alias `upgrade`, pool upgrade
# ---------------------------------------------------------------------------


def test_upgrade_version_posts_control_plane_only(runner, mock_api):
    cap = _capture(mock_api.post(f"{BASE}/upgrade-version"), 200,
                   _cluster(k8s_version="1.32.0", status="updating"))
    result = runner.invoke(app, ["k8s", "upgrade-version", CID, "--version", "v1.32.0", "-y"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {"k8s_version": "v1.32.0"}
    assert "Plan de contrôle → 1.32.0" in result.stdout
    assert "cetic k8s pool upgrade" in result.stdout


def test_upgrade_version_help_says_control_plane_only(runner):
    result = runner.invoke(app, ["k8s", "upgrade-version", "--help"], env={"COLUMNS": "200"})
    assert "PLAN DE CONTRÔLE seul" in result.stdout


def test_upgrade_version_invalid_version(runner, mock_api):
    result = runner.invoke(app, ["k8s", "upgrade-version", CID, "--version", "1.32", "-y"])
    assert result.exit_code != 0
    assert _no_call(mock_api, "POST")


def test_upgrade_alias_deprecated(runner, mock_api):
    cap = _capture(mock_api.post(f"{BASE}/upgrade-version"))
    result = runner.invoke(app, ["k8s", "upgrade", CID, "--version", "v1.32.0", "-y"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {"k8s_version": "v1.32.0"}
    assert "déprécié" in result.stderr


def test_pool_upgrade_posts(runner, mock_api):
    cap = _capture(mock_api.post(f"{BASE}/node-pools/{PID}/upgrade"), 200,
                   {"id": PID, "name": "default", "k8s_version": "1.32.0",
                    "status": "updating"})
    result = runner.invoke(app, ["k8s", "pool", "upgrade", CID, PID, "--version", "1.32.0",
                                 "--yes"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {"k8s_version": "1.32.0"}
    assert "Pool default → 1.32.0 (status : updating)" in result.stdout


def test_pool_upgrade_conflict(runner, mock_api):
    mock_api.post(f"{BASE}/node-pools/{PID}/upgrade").mock(return_value=httpx.Response(
        409, json={"detail": "Une mise à niveau est déjà en cours."}))
    result = runner.invoke(app, ["k8s", "pool", "upgrade", CID, PID, "-v", "1.32.0", "-y"])
    assert result.exit_code == 1
    assert "déjà en cours" in result.stdout


# ---------------------------------------------------------------------------
# upgrade-patch
# ---------------------------------------------------------------------------


def test_upgrade_patch_sends_displayed_version(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(
        200, json=_cluster(patch_disponible=PATCH)))
    cap = _capture(mock_api.post(f"{BASE}/upgrade-patch"), 200,
                   _cluster(status="updating", upgrade_en_cours=OPERATION))
    result = runner.invoke(app, ["k8s", "upgrade-patch", CID, "--yes"])
    assert result.exit_code == 0, result.output
    assert cap["params"] == {"version": "1.31.4"}
    assert cap["body"] is None
    assert "Patch 1.31.4 lancé" in result.stdout
    assert f"cetic k8s upgrades {CID}" in _flat(result.stdout)


def test_upgrade_patch_confirmation_shows_scope(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(
        200, json=_cluster(patch_disponible=PATCH)))
    result = runner.invoke(app, ["k8s", "upgrade-patch", CID], input="n\n")
    assert result.exit_code != 0
    assert "plan de contrôle + 2 pool(s)" in result.output
    assert _no_call(mock_api, "POST")


def test_upgrade_patch_none_available(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    result = runner.invoke(app, ["k8s", "upgrade-patch", CID, "--yes"])
    assert result.exit_code == 0
    assert "Aucun patch disponible" in result.stdout
    assert _no_call(mock_api, "POST")


def test_upgrade_patch_changed_since_display(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(
        200, json=_cluster(patch_disponible=PATCH)))
    mock_api.post(f"{BASE}/upgrade-patch").mock(return_value=httpx.Response(
        409, json={"detail": "Le patch disponible a changé (1.31.5)."}))
    result = runner.invoke(app, ["k8s", "upgrade-patch", CID, "--yes"])
    assert result.exit_code == 1
    assert "a changé" in result.stdout


# ---------------------------------------------------------------------------
# upgrades (historique)
# ---------------------------------------------------------------------------


def test_upgrades_history_table(runner, mock_api):
    done = dict(OPERATION, id="op-0", statut="terminee", type="pool",
                origine="automatique", termine_at="2026-11-24T11:00:00Z")
    route = mock_api.get(f"{BASE}/upgrades")
    cap = _capture(route, 200, [OPERATION, done])
    result = runner.invoke(app, ["k8s", "upgrades", CID, "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert cap["params"] == {"limit": "5"}
    for needle in ("1.31.3 → 1.31.4", "en cours", "terminée", "automatique",
                   "2026-11-24 10:00", "1/2"):
        assert needle in result.stdout, needle


# ---------------------------------------------------------------------------
# maintenance
# ---------------------------------------------------------------------------

MAINT = {
    "cluster_id": CID, "auto_patch": True, "auto_mineure": False, "auto_majeure": False,
    "fenetre": {"jours": [2, 3, 4], "heure_debut": "02:00", "duree_heures": 4,
                "fuseau": "Europe/Paris"},
    "dates_gel": [{"debut": "2026-12-20", "fin": "2027-01-03"}],
    "personnalise": False,
    "fenetre_plateforme": {"jours": [2, 3, 4], "heure_debut": "02:00", "duree_heures": 4,
                           "fuseau": "Europe/Paris"},
    "delai_stabilisation_jours": 7, "delai_patch_force_jours": 30,
    "prochaine_execution": {"type": "patch", "version": "1.31.4", "plan_de_controle": True,
                            "pools": [PID], "fenetre": "cluster",
                            "debut_prevu": "2026-11-25T02:00:00+01:00"},
    "reprise_prevue": None, "upgrade_en_cours": None, "notifications": [],
}


def test_maintenance_get_table(runner, mock_api):
    mock_api.get(f"{BASE}/maintenance").mock(return_value=httpx.Response(200, json=MAINT))
    result = runner.invoke(app, ["k8s", "maintenance", "get", CID])
    assert result.exit_code == 0, result.output
    for needle in ("mar, mer, jeu à 02:00, 4 h (Europe/Paris)", "2026-12-20 → 2027-01-03",
                   "patch 1.31.4 (plan de contrôle + 1 pool(s)), prévue le 2026-11-25 02:00"):
        assert needle in result.stdout, needle


def test_maintenance_set_booleans_only(runner, mock_api):
    cap = _capture(mock_api.put(f"{BASE}/maintenance"), 200, MAINT)
    result = runner.invoke(app, ["k8s", "maintenance", "set", CID, "--no-auto-patch",
                                 "--auto-mineure"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {"auto_patch": False, "auto_mineure": True}
    # Aucune lecture : la fenêtre n'est pas touchée.
    assert _no_call(mock_api, "GET")


def test_maintenance_set_partial_window_merges_current(runner, mock_api):
    mock_api.get(f"{BASE}/maintenance").mock(return_value=httpx.Response(200, json=MAINT))
    cap = _capture(mock_api.put(f"{BASE}/maintenance"), 200, MAINT)
    result = runner.invoke(app, ["k8s", "maintenance", "set", CID, "--jours", "sam,dim",
                                 "--heure-debut", "23:30"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {"fenetre": {"jours": [6, 7], "heure_debut": "23:30",
                                       "duree_heures": 4, "fuseau": "Europe/Paris"}}


def test_maintenance_set_full_window_no_read(runner, mock_api):
    cap = _capture(mock_api.put(f"{BASE}/maintenance"), 200, MAINT)
    result = runner.invoke(app, ["k8s", "maintenance", "set", CID, "--jours", "1-5",
                                 "--heure-debut", "01:00", "--duree-heures", "6",
                                 "--fuseau", "UTC"])
    assert result.exit_code == 0, result.output
    assert cap["body"]["fenetre"] == {"jours": [1, 2, 3, 4, 5], "heure_debut": "01:00",
                                      "duree_heures": 6, "fuseau": "UTC"}
    assert _no_call(mock_api, "GET")


def test_maintenance_set_gels_and_clear(runner, mock_api):
    cap = _capture(mock_api.put(f"{BASE}/maintenance"), 200, MAINT)
    result = runner.invoke(app, ["k8s", "maintenance", "set", CID,
                                 "--gel", "2026-12-20:2027-01-03", "--gel", "2027-08-01:2027-08-15"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {"dates_gel": [{"debut": "2026-12-20", "fin": "2027-01-03"},
                                         {"debut": "2027-08-01", "fin": "2027-08-15"}]}
    result = runner.invoke(app, ["k8s", "maintenance", "set", CID, "--sans-gel"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {"dates_gel": []}


@pytest.mark.parametrize("args", [
    ["--gel", "2026-12-20"],
    ["--gel", "2027-01-03:2026-12-20"],
    ["--gel", "2026-12-01:2027-01-15"],
    ["--heure-debut", "25:00"],
    ["--duree-heures", "2"],
    ["--jours", "8"],
    ["--gel", "2026-12-20:2026-12-21", "--sans-gel"],
])
def test_maintenance_set_invalid_rejected_locally(runner, mock_api, args):
    result = runner.invoke(app, ["k8s", "maintenance", "set", CID, *args])
    assert result.exit_code != 0
    assert _no_call(mock_api, "PUT")


def test_maintenance_set_nothing(runner, mock_api):
    result = runner.invoke(app, ["k8s", "maintenance", "set", CID])
    assert result.exit_code == 0
    assert "Aucun paramètre" in result.stdout
    assert _no_call(mock_api, "PUT")


def test_maintenance_api_422_shown(runner, mock_api):
    mock_api.put(f"{BASE}/maintenance").mock(return_value=httpx.Response(
        422, json={"detail": "Fuseau inconnu : Mars/Olympus"}))
    result = runner.invoke(app, ["k8s", "maintenance", "set", CID, "--jours", "1",
                                 "--heure-debut", "01:00", "--duree-heures", "4",
                                 "--fuseau", "Mars/Olympus"])
    assert result.exit_code == 1
    assert "Fuseau inconnu" in result.stdout


def test_parse_jours_unit():
    assert k8s_mod._parse_jours("2,3,4") == [2, 3, 4]
    assert k8s_mod._parse_jours("mar, jeudi,2") == [2, 4]
    assert k8s_mod._parse_jours("6-7,1") == [1, 6, 7]
    with pytest.raises(typer.BadParameter):
        k8s_mod._parse_jours("4-2")
    with pytest.raises(typer.BadParameter):
        k8s_mod._parse_jours("xyz")


# ---------------------------------------------------------------------------
# OIDC / kubeconfig SSO
# ---------------------------------------------------------------------------


def test_oidc_set_body_defaults(runner, mock_api):
    cap = _capture(mock_api.put(f"{BASE}/oidc"))
    result = runner.invoke(app, ["k8s", "oidc", "set", CID, "--issuer-url",
                                 "https://sso.example.com/realms/x", "--client-id", "kube",
                                 "--yes"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {
        "issuer_url": "https://sso.example.com/realms/x", "client_id": "kube",
        "username_claim": "email", "username_prefix": "oidc:", "groups_claim": "groups",
        "groups_prefix": "oidc:", "extra_scopes": [], "ca_pem": None,
    }
    assert "--mode sso" in _flat(result.stdout)


def test_oidc_set_options(runner, mock_api, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n")
    cap = _capture(mock_api.put(f"{BASE}/oidc"))
    result = runner.invoke(app, ["k8s", "oidc", "set", CID, "--issuer-url", "https://idp",
                                 "--client-id", "k", "--no-groups", "--scope", "profile",
                                 "--scope", "offline_access", "--username-claim", "sub",
                                 "--ca-file", str(ca), "-y"])
    assert result.exit_code == 0, result.output
    assert cap["body"]["groups_claim"] is None
    assert cap["body"]["extra_scopes"] == ["profile", "offline_access"]
    assert cap["body"]["username_claim"] == "sub"
    assert cap["body"]["ca_pem"].startswith("-----BEGIN CERTIFICATE-----")


def test_oidc_set_requires_https(runner, mock_api):
    result = runner.invoke(app, ["k8s", "oidc", "set", CID, "--issuer-url", "http://idp",
                                 "--client-id", "k", "-y"])
    assert result.exit_code == 1
    assert _no_call(mock_api, "PUT")


def test_oidc_set_conflict_shown(runner, mock_api):
    mock_api.put(f"{BASE}/oidc").mock(return_value=httpx.Response(
        409, json={"detail": "Le cluster doit être actif pour changer son SSO"}))
    result = runner.invoke(app, ["k8s", "oidc", "set", CID, "--issuer-url", "https://idp",
                                 "--client-id", "k", "-y"])
    assert result.exit_code == 1
    assert "doit être actif" in result.stdout


def test_oidc_delete(runner, mock_api):
    route = mock_api.delete(f"{BASE}/oidc").mock(
        return_value=httpx.Response(200, json=_cluster()))
    result = runner.invoke(app, ["k8s", "oidc", "delete", CID, "--yes"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "SSO retiré" in result.stdout


def test_oidc_get(runner, mock_api):
    oidc = {"issuer_url": "https://idp", "client_id": "kube", "username_claim": "email",
            "username_prefix": "oidc:", "groups_claim": "groups", "groups_prefix": "oidc:",
            "extra_scopes": [], "ca_pem": None}
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster(oidc=oidc)))
    result = runner.invoke(app, ["k8s", "oidc", "get", CID])
    assert result.exit_code == 0, result.output
    assert "https://idp" in result.stdout


def test_oidc_get_not_configured(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    result = runner.invoke(app, ["k8s", "oidc", "get", CID])
    assert result.exit_code == 0
    assert "SSO non configuré" in result.stdout


def test_kubeconfig_mode_sso(runner, mock_api):
    yaml_kc = "apiVersion: v1\nkind: Config\nusers:\n- user:\n    exec: {}\n"
    cap = _capture(mock_api.get(f"{BASE}/kubeconfig"), 200,
                   {"kubeconfig": yaml_kc, "expires_at": None})
    result = runner.invoke(app, ["k8s", "kubeconfig", CID, "--mode", "sso"])
    assert result.exit_code == 0, result.output
    assert cap["params"] == {"mode": "sso"}
    # stdout = le YAML seul (redirigeable vers ~/.kube/config).
    assert result.stdout.strip() == yaml_kc.strip()
    assert "kubelogin" in result.stderr


def test_kubeconfig_sso_not_configured(runner, mock_api):
    mock_api.get(f"{BASE}/kubeconfig").mock(return_value=httpx.Response(
        409, json={"detail": "SSO non configuré sur ce cluster"}))
    result = runner.invoke(app, ["k8s", "kubeconfig", CID, "--mode", "sso"])
    assert result.exit_code == 1
    assert "SSO non configuré" in result.stdout
