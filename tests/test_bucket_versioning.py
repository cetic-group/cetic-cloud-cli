"""Tests pour `cetic bucket versioning` (cetic-cloud-platform#1923).

On lit le corps exact qui part à l'API : la durée ne doit partir que par
`enable --noncurrent-days` et `retention`, jamais par `suspend`. Deux cas
effacent des versions anciennes et doivent être confirmés : raccourcir la durée,
et prendre en charge un versionnage posé hors de la plateforme.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cetic.main import app

BID = "11111111-2222-3333-4444-555555555555"
URL = f"/v1/buckets/{BID}/versioning"


def _cfg(status: str | None = "Enabled", days: int | None = 30, sync: str = "applied",
         error: str | None = None) -> dict[str, Any]:
    return {"status": status, "noncurrent_version_expiration_days": days,
            "sync_status": sync, "error_message": error}


def _texte(r) -> str:
    """Sortie sans les retours à la ligne que Rich insère à 80 colonnes."""
    return " ".join(r.output.split())


def _etat(mock_api, cfg: dict[str, Any]) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=cfg))


def _capture(mock_api, reponse: dict[str, Any]) -> list[dict[str, Any]]:
    """Enregistre chaque corps envoyé en PUT et répond `reponse`."""
    corps: list[dict[str, Any]] = []

    def _h(req: httpx.Request) -> httpx.Response:
        corps.append(json.loads(req.content))
        return httpx.Response(200, json=reponse)

    mock_api.put(URL).mock(side_effect=_h)
    return corps


# ── show ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("status", "sync", "attendu"), [
    ("Enabled", "applied", "Activé"),
    ("Suspended", "applied", "Suspendu"),
    (None, "none", "Non activé"),
    ("Enabled", "pending", "En attente"),
])
def test_show(runner, mock_api, status, sync, attendu) -> None:
    _etat(mock_api, _cfg(status, 90, sync))
    r = runner.invoke(app, ["bucket", "versioning", "show", BID])
    assert r.exit_code == 0, r.output
    assert attendu in r.output


def test_show_non_gere_annonce_la_duree_posee(runner, mock_api) -> None:
    _etat(mock_api, _cfg("Enabled", None, "unmanaged", "stockage injoignable"))
    r = runner.invoke(app, ["bucket", "versioning", "show", BID])
    assert r.exit_code == 0, r.output
    assert "hors de la plateforme" in _texte(r)
    assert "30 jours par défaut" in _texte(r) and "efface" in _texte(r)
    assert "stockage injoignable" in _texte(r)


# ── sortie machine (json / yaml) ────────────────────────────────────────────

def test_show_json_brut_meme_en_attente_avec_erreur(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    cfg = _cfg("Enabled", 45, "pending", "stockage lent")
    _etat(mock_api, cfg)
    r = runner.invoke(app, ["bucket", "versioning", "show", BID])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout) == cfg


def test_enable_json_lisible_par_un_script(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    _etat(mock_api, _cfg(None, None, "none"))
    reponse = _cfg("Enabled", 30, "pending")
    _capture(mock_api, reponse)
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout) == reponse


@pytest.mark.parametrize(("commande", "reponse"), [
    (["suspend"], _cfg("Suspended", 30)),
    (["retention", "-d", "60"], _cfg("Enabled", 60)),
])
def test_ecritures_json_lisibles(runner, mock_api, monkeypatch, commande, reponse) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    _etat(mock_api, _cfg("Enabled", 30))
    _capture(mock_api, reponse)
    r = runner.invoke(app, ["bucket", "versioning", commande[0], BID, *commande[1:]])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout) == reponse


def test_yaml_brut(runner, mock_api, monkeypatch) -> None:
    import yaml

    monkeypatch.setenv("CCP_OUTPUT", "yaml")
    cfg = _cfg("Suspended", 45)
    _etat(mock_api, cfg)
    r = runner.invoke(app, ["bucket", "versioning", "show", BID])
    assert r.exit_code == 0, r.output
    assert yaml.safe_load(r.stdout) == cfg


# ── enable ──────────────────────────────────────────────────────────────────

def test_enable_sans_duree_n_envoie_que_le_statut(runner, mock_api) -> None:
    _etat(mock_api, _cfg(None, None, "none"))
    corps = _capture(mock_api, _cfg())
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID])
    assert r.exit_code == 0, r.output
    assert corps == [{"status": "Enabled"}]
    assert "activé" in r.output


def test_enable_avec_duree(runner, mock_api) -> None:
    _etat(mock_api, _cfg(None, None, "none"))
    corps = _capture(mock_api, _cfg("Enabled", 90))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID, "--noncurrent-days", "90"])
    assert r.exit_code == 0, r.output
    assert corps == [{"status": "Enabled", "noncurrent_version_expiration_days": 90}]


@pytest.mark.parametrize("commande", ["enable", "retention"])
@pytest.mark.parametrize("jours", ["0", "3651"])
def test_duree_hors_bornes_refusee_sans_appel(runner, mock_api, commande, jours) -> None:
    # Lecture simulée et --yes : seules les bornes 1..3650 peuvent arrêter la commande.
    _etat(mock_api, _cfg("Enabled", 30))
    corps = _capture(mock_api, _cfg())
    r = runner.invoke(app, ["bucket", "versioning", commande, BID, "-d", jours, "--yes"])
    assert r.exit_code == 2
    assert corps == []


@pytest.mark.parametrize("jours", ["1", "3650"])
def test_duree_aux_bornes_acceptee(runner, mock_api, jours) -> None:
    _etat(mock_api, _cfg("Enabled", 30))
    corps = _capture(mock_api, _cfg("Enabled", int(jours)))
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID, "-d", jours, "--yes"])
    assert r.exit_code == 0, r.output
    assert corps[0]["noncurrent_version_expiration_days"] == int(jours)


def test_enable_duree_raccourcie_demande_confirmation(runner, mock_api) -> None:
    _etat(mock_api, _cfg("Enabled", 365))
    corps = _capture(mock_api, _cfg("Enabled", 7))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID, "-d", "7"], input="n\n")
    assert r.exit_code != 0
    assert corps == []
    assert "365" in _texte(r) and "7 jours" in _texte(r)


def test_enable_duree_raccourcie_yes(runner, mock_api) -> None:
    _etat(mock_api, _cfg("Enabled", 365))
    corps = _capture(mock_api, _cfg("Enabled", 7))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID, "-d", "7", "--yes"])
    assert r.exit_code == 0, r.output
    assert corps == [{"status": "Enabled", "noncurrent_version_expiration_days": 7}]


def test_enable_duree_allongee_sans_confirmation(runner, mock_api) -> None:
    _etat(mock_api, _cfg("Enabled", 30))
    corps = _capture(mock_api, _cfg("Enabled", 60))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID, "-d", "60"])
    assert r.exit_code == 0, r.output
    assert len(corps) == 1


def test_reprendre_un_bucket_gere_sans_confirmation(runner, mock_api) -> None:
    _etat(mock_api, _cfg("Suspended", 90))
    corps = _capture(mock_api, _cfg("Enabled", 90))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID])
    assert r.exit_code == 0, r.output
    assert corps == [{"status": "Enabled"}]


# ── prise en charge d'un versionnage posé hors plateforme ───────────────────
# Souvent sans expiration : les versions anciennes sont gardées sans limite, et
# la prise en charge pose une durée qui efface les plus anciennes.

@pytest.mark.parametrize(("commande", "pose"), [
    (["enable"], "30 jours"),
    (["enable", "-d", "90"], "90 jours"),
    (["suspend"], "30 jours"),
    (["retention", "-d", "400"], "400 jours"),
])
def test_prise_en_charge_hors_plateforme_confirmee(runner, mock_api, commande, pose) -> None:
    _etat(mock_api, _cfg("Enabled", None, "unmanaged"))
    corps = _capture(mock_api, _cfg())
    r = runner.invoke(app, ["bucket", "versioning", commande[0], BID, *commande[1:]], input="n\n")
    assert r.exit_code != 0
    assert corps == [], "rien ne doit partir sans confirmation"
    assert "hors de la plateforme" in _texte(r) and pose in _texte(r)


@pytest.mark.parametrize("commande", [["enable"], ["suspend"], ["retention", "-d", "400"]])
def test_prise_en_charge_hors_plateforme_yes(runner, mock_api, commande) -> None:
    _etat(mock_api, _cfg("Enabled", None, "unmanaged"))
    corps = _capture(mock_api, _cfg())
    r = runner.invoke(app, ["bucket", "versioning", commande[0], BID, *commande[1:], "--yes"])
    assert r.exit_code == 0, r.output
    assert len(corps) == 1


def test_prise_en_charge_hors_plateforme_acceptee(runner, mock_api) -> None:
    _etat(mock_api, _cfg("Enabled", None, "unmanaged"))
    corps = _capture(mock_api, _cfg())
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID], input="y\n")
    assert r.exit_code == 0, r.output
    assert corps == [{"status": "Enabled"}]


# ── suspend ─────────────────────────────────────────────────────────────────

def test_suspend_n_envoie_jamais_de_duree(runner, mock_api) -> None:
    _etat(mock_api, _cfg("Enabled", 90))
    corps = _capture(mock_api, _cfg("Suspended", 90))
    r = runner.invoke(app, ["bucket", "versioning", "suspend", BID])
    assert r.exit_code == 0, r.output
    assert corps == [{"status": "Suspended"}]
    assert "suspendu" in r.output


def test_suspend_bucket_jamais_versionne(runner, mock_api) -> None:
    _etat(mock_api, _cfg(None, None, "none"))
    mock_api.put(URL).mock(return_value=httpx.Response(
        422, json={"detail": "Ce bucket n'a jamais été versionné : il n'y a rien à suspendre."}))
    r = runner.invoke(app, ["bucket", "versioning", "suspend", BID])
    assert r.exit_code == 1
    assert "jamais été versionné" in _texte(r)


# ── retention ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["Enabled", "Suspended"])
def test_retention_garde_l_etat(runner, mock_api, status) -> None:
    _etat(mock_api, _cfg(status, 30))
    corps = _capture(mock_api, _cfg(status, 60))
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID, "--days", "60"])
    assert r.exit_code == 0, r.output
    assert corps == [{"status": status, "noncurrent_version_expiration_days": 60}]
    assert "60 jours" in r.output


def test_retention_bucket_jamais_versionne(runner, mock_api) -> None:
    _etat(mock_api, _cfg(None, None, "none"))
    corps = _capture(mock_api, _cfg())
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID, "--days", "60"])
    assert r.exit_code == 1
    assert corps == []
    assert "enable" in r.output


def test_retention_raccourcie_refusee(runner, mock_api) -> None:
    _etat(mock_api, _cfg("Suspended", 90))
    corps = _capture(mock_api, _cfg("Suspended", 10))
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID, "-d", "10"], input="n\n")
    assert r.exit_code != 0
    assert corps == []


def test_retention_sans_duree_refusee(runner, mock_api) -> None:
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID])
    assert r.exit_code != 0


# ── erreurs de l'API ────────────────────────────────────────────────────────

def test_conflit_409_message_de_l_api(runner, mock_api) -> None:
    _etat(mock_api, _cfg("Enabled", 30))
    mock_api.put(URL).mock(return_value=httpx.Response(
        409, json={"detail": "Une règle de cycle de vie garde les versions 365 jours : indiquez au moins 365 jours."}))
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID, "-d", "60"])
    assert r.exit_code == 1
    assert "au moins 365 jours" in _texte(r)


def test_502_enregistre_la_plateforme_reessaie(runner, mock_api) -> None:
    mock_api.get(URL).mock(side_effect=[
        httpx.Response(200, json=_cfg(None, None, "none")),
        httpx.Response(200, json=_cfg("Enabled", 30, "error", "stockage injoignable")),
    ])
    mock_api.put(URL).mock(return_value=httpx.Response(
        502, json={"detail": "Versionnage enregistré mais non appliqué (stockage injoignable)."}))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID])
    assert r.exit_code == 1
    assert "réessaie d'elle-même" in _texte(r)
    assert "pas confirmé" not in _texte(r)


@pytest.mark.parametrize("relu", [
    _cfg(None, None, "none"),        # état réel illisible : rien d'enregistré
    _cfg("Enabled", 30, "applied"),  # passerelle en échec après l'enregistrement
    _cfg("Enabled", 30, "pending"),  # écriture concurrente
])
def test_502_non_confirme_sans_affirmer_le_resultat(runner, mock_api, relu) -> None:
    # Seul sync_status=error prouve « enregistré, réessayé ». Ailleurs on ne sait
    # pas : ni « rien n'a été enregistré » ni « réessaie », mais « vérifiez ».
    mock_api.get(URL).mock(side_effect=[
        httpx.Response(200, json=_cfg(None, None, "none")),
        httpx.Response(200, json=relu),
    ])
    mock_api.put(URL).mock(return_value=httpx.Response(
        502, json={"detail": "Erreur stockage objet : délai dépassé"}))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID])
    assert r.exit_code == 1
    assert "pas confirmé" in _texte(r) and "versioning show" in _texte(r)
    assert "réessaie d'elle-même" not in _texte(r)
    assert "Rien n'a été enregistré" not in _texte(r)


def test_pas_de_commande_disable(runner) -> None:
    r = runner.invoke(app, ["bucket", "versioning", "disable", BID])
    assert r.exit_code != 0
    aide = runner.invoke(app, ["bucket", "versioning", "--help"]).output.lower()
    assert "disable" not in aide and "désactiv" not in aide.replace("jamais désactiver", "")
