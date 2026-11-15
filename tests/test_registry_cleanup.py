"""Tests `cetic registry cleanup` — nettoyage planifié (cetic-cloud-platform#1984).

Les messages d'erreur simulés sont ceux du code de l'API
(`apps/api/app/api/v1/registries.py`, `schemas/registry_retention.py`) : la
CLI doit les afficher tels quels.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app

REG_ID = "11111111-2222-3333-4444-555555555555"
SCHED_ID = "99999999-8888-7777-6666-555555555555"
DEL_ID = "dddddddd-eeee-ffff-0000-111111111111"
BASE = f"/v1/registries/{REG_ID}"
SCHED_PATH = f"{BASE}/cleanup/schedules"


def _sched(**over: Any) -> dict[str, Any]:
    s = {
        "id": SCHED_ID, "slot": 1, "name": "preversions",
        "repo_patterns": ["app/*"], "tag_patterns": ["*-rc-*", "*-dev"],
        "critere": "keep_last_n", "valeur": 5, "cron": "0 3 * * *", "enabled": True,
        "last_dispatched_at": "2026-11-15T10:00:00Z",
        "created_at": "2026-11-15T10:00:00Z", "updated_at": "2026-11-15T10:00:00Z",
    }
    s.update(over)
    return s


def _deletion(**over: Any) -> dict[str, Any]:
    d = {
        "id": DEL_ID, "run_id": "r1", "repo": "app/api", "tag": "1.0-rc-1",
        "manifest_digest": "sha256:" + "a" * 64, "taille": 12_000_000,
        "regle_id": None, "raison": "planification « preversions » : au-delà des 5 plus récents",
        "purge_after": "2026-11-22T10:00:00Z", "purged_at": None, "cancelled_at": None,
        "cancelled_by": None, "schedule_id": SCHED_ID, "schedule_name": "preversions",
        "origine": "nettoyage", "exclusion_active": False,
        "exclusion_levee_at": None, "exclusion_levee_par": None,
    }
    d.update(over)
    return d


def _capture(store: dict[str, Any], status: int, payload: Any):
    def _f(request: httpx.Request) -> httpx.Response:
        store["body"] = json.loads(request.content) if request.content else None
        store["params"] = dict(request.url.params)
        return httpx.Response(status, json=payload)
    return _f


# ---------------------------------------------------------------------------
# Planifications
# ---------------------------------------------------------------------------


def test_list_table(runner, mock_api):
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[
        _sched(), _sched(id="x", slot=2, name="vieux", critere="older_than_days",
                         valeur=30, cron=None),
    ]))
    result = runner.invoke(app, ["registry", "cleanup", "list", REG_ID])
    assert result.exit_code == 0, result.stdout
    assert "garder les 5 plus récents" in result.stdout
    assert "plus ancien que 30 j" in result.stdout
    assert "à la demande" in result.stdout


def test_list_json_brut(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    result = runner.invoke(app, ["registry", "cleanup", "list", REG_ID])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == [_sched()]


def test_get_par_nom_lit_la_liste(runner, mock_api):
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    result = runner.invoke(app, ["registry", "cleanup", "get", REG_ID, "preversions"])
    assert result.exit_code == 0, result.stdout
    assert SCHED_ID in result.stdout
    assert "app/*" in result.stdout


def test_get_json_sans_mise_en_forme(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched(cron=None)]))
    result = runner.invoke(app, ["registry", "cleanup", "get", REG_ID, SCHED_ID])
    assert result.exit_code == 0, result.stdout
    out = json.loads(result.stdout)
    assert out == _sched(cron=None)
    assert out["critere"] == "keep_last_n"
    assert out["last_dispatched_at"] == "2026-11-15T10:00:00Z"


def test_get_inconnue(runner, mock_api):
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(app, ["registry", "cleanup", "get", REG_ID, "absente"])
    assert result.exit_code == 1
    assert "absente" in result.stdout


def test_create_corps(runner, mock_api):
    store: dict[str, Any] = {}
    mock_api.post(SCHED_PATH).mock(side_effect=_capture(store, 201, _sched()))
    result = runner.invoke(app, [
        "registry", "cleanup", "create", REG_ID, "--name", "preversions",
        "--repo", "app/*", "--tag", "*-rc-*", "--tag", "*-dev",
        "--critere", "keep_last_n", "--valeur", "5", "--cron", "0 3 * * *",
    ])
    assert result.exit_code == 0, result.stdout
    assert store["body"] == {
        "name": "preversions", "repo_patterns": ["app/*"],
        "tag_patterns": ["*-rc-*", "*-dev"], "critere": "keep_last_n",
        "valeur": 5, "cron": "0 3 * * *", "enabled": True,
    }
    assert "créneau 1" in result.stdout


def test_create_sans_cron_ni_valeur_et_desactivee(runner, mock_api):
    """Sans --cron : la clé est absente (l'API la met à nul = à la demande)."""
    store: dict[str, Any] = {}
    mock_api.post(SCHED_PATH).mock(
        side_effect=_capture(store, 201, _sched(critere="all", valeur=None, cron=None,
                                                enabled=False)))
    result = runner.invoke(app, [
        "registry", "cleanup", "create", REG_ID, "-n", "rc",
        "--repo", "app/*", "--tag", "*-rc-*", "--critere", "all", "--disabled",
    ])
    assert result.exit_code == 0, result.stdout
    assert "cron" not in store["body"]
    assert "valeur" not in store["body"]
    assert store["body"]["enabled"] is False
    assert "à la demande seulement" in result.stdout


def test_create_json_seul_sur_stdout(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.post(SCHED_PATH).mock(return_value=httpx.Response(201, json=_sched()))
    result = runner.invoke(app, [
        "registry", "cleanup", "create", REG_ID, "-n", "preversions",
        "--repo", "app/*", "--tag", "*-rc-*", "--critere", "keep_last_n", "--valeur", "5",
    ])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == _sched()


def test_create_5e_planification_422_tel_quel(runner, mock_api):
    msg = ("Au plus 4 planifications de nettoyage par registry : "
           "supprimez-en une avant d'en créer une autre.")
    mock_api.post(SCHED_PATH).mock(return_value=httpx.Response(422, json={"detail": msg}))
    result = runner.invoke(app, [
        "registry", "cleanup", "create", REG_ID, "-n", "cinq",
        "--repo", "app/*", "--tag", "*-rc-*", "--critere", "all",
    ])
    assert result.exit_code == 1
    flat = " ".join(result.stdout.split())
    assert "Erreur 422" in flat
    assert msg in flat


def test_create_garde_fou_motifs_universels_422_pydantic(runner, mock_api):
    """422 de validation Pydantic (liste) : le message du validateur passe tel quel."""
    msg = ("« all » sur des motifs qui correspondent à tous les dépôts et à tous "
           "les tags (« * », « ?* », « */* »…) viderait toute la registry : "
           "restreignez un des motifs.")
    mock_api.post(SCHED_PATH).mock(return_value=httpx.Response(422, json={"detail": [
        {"type": "value_error", "loc": ["body"], "msg": f"Value error, {msg}"},
    ]}))
    result = runner.invoke(app, [
        "registry", "cleanup", "create", REG_ID, "-n", "tout",
        "--repo", "*", "--tag", "*", "--critere", "all",
    ])
    assert result.exit_code == 1
    assert msg in " ".join(result.stdout.split())


def test_update_corps_partiel_par_nom(runner, mock_api):
    store: dict[str, Any] = {}
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    mock_api.patch(f"{SCHED_PATH}/{SCHED_ID}").mock(
        side_effect=_capture(store, 200, _sched(enabled=False)))
    result = runner.invoke(app, [
        "registry", "cleanup", "update", REG_ID, "preversions", "--disabled",
        "--tag", "*-beta",
    ])
    assert result.exit_code == 0, result.stdout
    assert store["body"] == {"tag_patterns": ["*-beta"], "enabled": False}


def test_update_no_cron_envoie_null(runner, mock_api):
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    store: dict[str, Any] = {}
    mock_api.patch(f"{SCHED_PATH}/{SCHED_ID}").mock(
        side_effect=_capture(store, 200, _sched(cron=None)))
    result = runner.invoke(app, ["registry", "cleanup", "update", REG_ID, SCHED_ID, "--no-cron"])
    assert result.exit_code == 0, result.stdout
    assert store["body"] == {"cron": None}


def test_update_cron_et_no_cron_incompatibles(runner, mock_api):
    result = runner.invoke(app, [
        "registry", "cleanup", "update", REG_ID, SCHED_ID, "--no-cron", "--cron", "0 3 * * *",
    ])
    assert result.exit_code == 1
    assert "incompatibles" in result.stdout


def test_update_sans_rien(runner, mock_api):
    result = runner.invoke(app, ["registry", "cleanup", "update", REG_ID, SCHED_ID])
    assert result.exit_code == 1
    assert "Aucune modification" in result.stdout


def test_update_critere_sans_valeur_422_de_l_api_non_masque(runner, mock_api):
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    """La CLI n'invente pas de valeur et n'arrête pas la requête : l'API
    refuse (`update_cleanup_schedule`), son message est affiché tel quel."""
    store: dict[str, Any] = {}
    msg = "« valeur » est exigée quand « critere » change."
    mock_api.patch(f"{SCHED_PATH}/{SCHED_ID}").mock(
        side_effect=_capture(store, 422, {"detail": msg}))
    result = runner.invoke(app, [
        "registry", "cleanup", "update", REG_ID, SCHED_ID, "--critere", "older_than_days",
    ])
    assert store["body"] == {"critere": "older_than_days"}
    assert result.exit_code == 1
    assert msg in " ".join(result.stdout.split())


def test_delete(runner, mock_api):
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    route = mock_api.delete(f"{SCHED_PATH}/{SCHED_ID}").mock(return_value=httpx.Response(204))
    result = runner.invoke(app, ["registry", "cleanup", "delete", REG_ID, SCHED_ID, "--yes"])
    assert result.exit_code == 0, result.stdout
    assert route.called


def test_delete_refus_de_confirmation(runner, mock_api):
    route = mock_api.delete(f"{SCHED_PATH}/{SCHED_ID}").mock(return_value=httpx.Response(204))
    result = runner.invoke(app, ["registry", "cleanup", "delete", REG_ID, SCHED_ID], input="n\n")
    assert result.exit_code != 0
    assert not route.called


# ---------------------------------------------------------------------------
# Simulation / exécution
# ---------------------------------------------------------------------------

DISPATCH = {"registry_id": REG_ID, "schedule_id": SCHED_ID, "mode": "simulation",
            "status": "queued", "task_id": "t-1"}


def test_simulate(runner, mock_api):
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    mock_api.post(f"{SCHED_PATH}/{SCHED_ID}/simulate").mock(
        return_value=httpx.Response(202, json=DISPATCH))
    result = runner.invoke(app, ["registry", "cleanup", "simulate", REG_ID, SCHED_ID])
    assert result.exit_code == 0, result.stdout
    assert "t-1" in result.stdout
    assert "cleanup runs" in result.stdout


def test_simulate_json(runner, mock_api, monkeypatch):
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.post(f"{SCHED_PATH}/{SCHED_ID}/simulate").mock(
        return_value=httpx.Response(202, json=DISPATCH))
    result = runner.invoke(app, ["registry", "cleanup", "simulate", REG_ID, SCHED_ID])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout) == DISPATCH


def test_run_409_simulation_requise_tel_quel(runner, mock_api):
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    msg = ("Aucune simulation réussie de cette planification sur ses règles actuelles : "
           "simulez-la avant de l'exécuter.")
    mock_api.post(f"{SCHED_PATH}/{SCHED_ID}/run").mock(
        return_value=httpx.Response(409, json={"detail": msg}))
    result = runner.invoke(app, ["registry", "cleanup", "run", REG_ID, SCHED_ID, "--yes"])
    assert result.exit_code == 1
    flat = " ".join(result.stdout.split())
    assert "Erreur 409" in flat
    assert msg in flat
    assert "Quota" not in flat


def test_run_ok(runner, mock_api):
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    mock_api.post(f"{SCHED_PATH}/{SCHED_ID}/run").mock(
        return_value=httpx.Response(202, json={**DISPATCH, "mode": "application"}))
    result = runner.invoke(app, ["registry", "cleanup", "run", REG_ID, SCHED_ID, "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "Exécution demandée" in result.stdout


def test_run_demande_confirmation(runner, mock_api):
    route = mock_api.post(f"{SCHED_PATH}/{SCHED_ID}/run").mock(
        return_value=httpx.Response(202, json=DISPATCH))
    result = runner.invoke(app, ["registry", "cleanup", "run", REG_ID, SCHED_ID], input="n\n")
    assert result.exit_code != 0
    assert not route.called


# ---------------------------------------------------------------------------
# Passages et corbeille
# ---------------------------------------------------------------------------

RUN = {
    "id": "run-1", "mode": "simulation", "statut": "succeeded", "declenche_par": "manuel",
    "rules_hash": "h", "started_at": "2026-11-15T10:00:00Z", "finished_at": None,
    "tags_evalues": 40, "tags_selectionnes": 7, "octets_liberables": 3_500_000_000,
    "message_erreur": None, "schedule_id": SCHED_ID, "schedule_name": "preversions",
    "origine": "nettoyage",
}


def test_runs_envoie_origine_nettoyage_par_defaut(runner, mock_api):
    store: dict[str, Any] = {}
    mock_api.get(f"{BASE}/retention/runs").mock(side_effect=_capture(store, 200, [RUN]))
    result = runner.invoke(app, ["registry", "cleanup", "runs", REG_ID])
    assert result.exit_code == 0, result.stdout
    assert store["params"] == {"origine": "nettoyage", "limit": "50", "offset": "0"}
    assert "3.5 GB" in result.stdout
    assert "preversions" in result.stdout


def test_runs_d_une_planification(runner, mock_api):
    store: dict[str, Any] = {}
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    mock_api.get(f"{BASE}/retention/runs").mock(side_effect=_capture(store, 200, [RUN]))
    result = runner.invoke(app, [
        "registry", "cleanup", "runs", REG_ID, "--schedule", "preversions",
        "--mode", "simulation", "--statut", "succeeded", "--limit", "10", "--offset", "20",
    ])
    assert result.exit_code == 0, result.stdout
    assert store["params"] == {"schedule_id": SCHED_ID, "mode": "simulation",
                               "statut": "succeeded", "limit": "10", "offset": "20"}


def test_runs_origine_tout_et_json(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    store: dict[str, Any] = {}
    mock_api.get(f"{BASE}/retention/runs").mock(side_effect=_capture(store, 200, [RUN]))
    result = runner.invoke(app, ["registry", "cleanup", "runs", REG_ID, "--origine", "tout"])
    assert result.exit_code == 0, result.stdout
    assert store["params"]["origine"] == "tout"
    assert json.loads(result.stdout) == [RUN]


def test_runs_origine_invalide(runner, mock_api):
    result = runner.invoke(app, ["registry", "cleanup", "runs", REG_ID, "--origine", "x"])
    assert result.exit_code == 1
    assert "--origine" in result.stdout


def test_trash_params_et_etats(runner, mock_api):
    store: dict[str, Any] = {}
    mock_api.get(f"{BASE}/retention/deletions").mock(side_effect=_capture(store, 200, [
        _deletion(),
        _deletion(id="d2", cancelled_at="2026-11-16T10:00:00Z", exclusion_active=True),
        _deletion(id="d3", purged_at="2026-11-22T11:00:00Z"),
    ]))
    result = runner.invoke(app, ["registry", "cleanup", "trash", REG_ID, "--en-attente",
                                 "--origine", "retention"])
    assert result.exit_code == 0, result.stdout
    assert store["params"] == {"origine": "retention", "en_attente": "true",
                               "limit": "100", "offset": "0"}
    flat = " ".join(result.stdout.split())
    assert "en attente" in flat
    assert "exclusion active" in flat
    assert "supprimée le" in flat


def test_trash_defaut_sans_en_attente(runner, mock_api):
    store: dict[str, Any] = {}
    mock_api.get(f"{BASE}/retention/deletions").mock(side_effect=_capture(store, 200, []))
    result = runner.invoke(app, ["registry", "cleanup", "trash", REG_ID])
    assert result.exit_code == 0, result.stdout
    assert store["params"] == {"origine": "nettoyage", "limit": "100", "offset": "0"}


def test_restore(runner, mock_api):
    mock_api.post(f"{BASE}/retention/deletions/{DEL_ID}/cancel").mock(
        return_value=httpx.Response(200, json={"id": DEL_ID,
                                               "cancelled_at": "2026-11-16T10:00:00Z",
                                               "cancelled_by": "console:a@b.c"}))
    result = runner.invoke(app, ["registry", "cleanup", "restore", REG_ID, DEL_ID])
    assert result.exit_code == 0, result.stdout
    assert "restaurée" in result.stdout


def test_restore_409_deja_purgee_tel_quel(runner, mock_api):
    msg = "Ce tag a déjà été purgé — l'annulation n'a plus d'effet."
    mock_api.post(f"{BASE}/retention/deletions/{DEL_ID}/cancel").mock(
        return_value=httpx.Response(409, json={"detail": msg}))
    result = runner.invoke(app, ["registry", "cleanup", "restore", REG_ID, DEL_ID])
    assert result.exit_code == 1
    assert msg in " ".join(result.stdout.split())


def test_lift_exclusion(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    payload = _deletion(cancelled_at="2026-11-16T10:00:00Z",
                        exclusion_levee_at="2026-11-17T10:00:00Z",
                        exclusion_levee_par="console:a@b.c")
    route = mock_api.delete(f"{BASE}/retention/deletions/{DEL_ID}/exclusion").mock(
        return_value=httpx.Response(200, json=payload))
    result = runner.invoke(app, ["registry", "cleanup", "lift-exclusion", REG_ID, DEL_ID,
                                 "--yes"])
    assert result.exit_code == 0, result.stdout
    assert route.called
    assert json.loads(result.stdout) == payload


def test_lift_exclusion_409_non_restauree_tel_quel(runner, mock_api):
    msg = "Cette image n'a pas été restaurée : aucune exclusion à lever."
    mock_api.delete(f"{BASE}/retention/deletions/{DEL_ID}/exclusion").mock(
        return_value=httpx.Response(409, json={"detail": msg}))
    result = runner.invoke(app, ["registry", "cleanup", "lift-exclusion", REG_ID, DEL_ID, "-y"])
    assert result.exit_code == 1
    assert msg in " ".join(result.stdout.split())


def test_nom_au_format_uuid_resolu_par_son_nom(runner, mock_api):
    """Relecture #83 : l'API accepte un NOM au format UUID ; la CLI cherche
    l'id d'abord, puis le nom, au lieu de le prendre pour un id inexistant."""
    nom = "0123abcd-0123-0123-0123-0123456789ab"
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched(name=nom)]))
    route = mock_api.post(f"{SCHED_PATH}/{SCHED_ID}/simulate").mock(
        return_value=httpx.Response(202, json=DISPATCH))
    result = runner.invoke(app, ["registry", "cleanup", "simulate", REG_ID, nom])
    assert result.exit_code == 0, result.stdout
    assert route.called


def test_delete_previent_que_la_corbeille_sera_purgee(runner, mock_api):
    mock_api.get(SCHED_PATH).mock(return_value=httpx.Response(200, json=[_sched()]))
    mock_api.delete(f"{SCHED_PATH}/{SCHED_ID}").mock(return_value=httpx.Response(204))
    result = runner.invoke(app, ["registry", "cleanup", "delete", REG_ID, SCHED_ID, "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "délai de grâce" in result.stdout
