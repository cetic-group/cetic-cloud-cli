"""Tests pour `cetic bucket versioning` (cetic-cloud-platform#1923).

On lit le corps exact qui part à l'API : la durée ne doit partir que par
`enable --noncurrent-days` et `retention`, jamais par `suspend`.
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
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg(status, 90, sync)))
    r = runner.invoke(app, ["bucket", "versioning", "show", BID])
    assert r.exit_code == 0, r.output
    assert attendu in r.output


def test_show_non_gere_et_erreur(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(
        200, json=_cfg("Enabled", None, "unmanaged", "stockage injoignable")))
    r = runner.invoke(app, ["bucket", "versioning", "show", BID])
    assert r.exit_code == 0, r.output
    assert "hors de la plateforme" in _texte(r)
    assert "stockage injoignable" in _texte(r)


def test_show_json(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg("Suspended", 45)))
    r = runner.invoke(app, ["bucket", "versioning", "show", BID])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["Versionnage"] == "Suspendu"
    assert data["Conservation des versions anciennes"] == "45 jours"


# ── enable ──────────────────────────────────────────────────────────────────

def test_enable_sans_duree_n_envoie_que_le_statut(runner, mock_api) -> None:
    corps = _capture(mock_api, _cfg())
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID])
    assert r.exit_code == 0, r.output
    assert corps == [{"status": "Enabled"}]
    assert "activé" in r.output


def test_enable_avec_duree(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg(None, None, "none")))
    corps = _capture(mock_api, _cfg("Enabled", 90))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID, "--noncurrent-days", "90"])
    assert r.exit_code == 0, r.output
    assert corps == [{"status": "Enabled", "noncurrent_version_expiration_days": 90}]


@pytest.mark.parametrize("commande", ["enable", "retention"])
@pytest.mark.parametrize("jours", ["0", "3651"])
def test_duree_hors_bornes_refusee_sans_appel(runner, mock_api, commande, jours) -> None:
    # Lecture simulée et --yes : seules les bornes 1..3650 peuvent arrêter la commande.
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg("Enabled", 30)))
    corps = _capture(mock_api, _cfg())
    r = runner.invoke(app, ["bucket", "versioning", commande, BID, "-d", jours, "--yes"])
    assert r.exit_code == 2
    assert corps == []


@pytest.mark.parametrize("jours", ["1", "3650"])
def test_duree_aux_bornes_acceptee(runner, mock_api, jours) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg("Enabled", 30)))
    corps = _capture(mock_api, _cfg("Enabled", int(jours)))
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID, "-d", jours, "--yes"])
    assert r.exit_code == 0, r.output
    assert corps[0]["noncurrent_version_expiration_days"] == int(jours)


def test_enable_duree_raccourcie_demande_confirmation(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg("Enabled", 365)))
    corps = _capture(mock_api, _cfg("Enabled", 7))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID, "-d", "7"], input="n\n")
    assert r.exit_code != 0
    assert corps == []
    assert "365" in _texte(r) and "7 jours" in _texte(r)


def test_enable_duree_raccourcie_yes(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg("Enabled", 365)))
    corps = _capture(mock_api, _cfg("Enabled", 7))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID, "-d", "7", "--yes"])
    assert r.exit_code == 0, r.output
    assert corps == [{"status": "Enabled", "noncurrent_version_expiration_days": 7}]


def test_enable_duree_allongee_sans_confirmation(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg("Enabled", 30)))
    corps = _capture(mock_api, _cfg("Enabled", 60))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID, "-d", "60"])
    assert r.exit_code == 0, r.output
    assert len(corps) == 1


# ── suspend ─────────────────────────────────────────────────────────────────

def test_suspend_n_envoie_jamais_de_duree(runner, mock_api) -> None:
    corps = _capture(mock_api, _cfg("Suspended", 90))
    r = runner.invoke(app, ["bucket", "versioning", "suspend", BID])
    assert r.exit_code == 0, r.output
    assert corps == [{"status": "Suspended"}]
    assert "suspendu" in r.output


def test_suspend_bucket_jamais_versionne(runner, mock_api) -> None:
    mock_api.put(URL).mock(return_value=httpx.Response(
        422, json={"detail": "Ce bucket n'a jamais été versionné : il n'y a rien à suspendre."}))
    r = runner.invoke(app, ["bucket", "versioning", "suspend", BID])
    assert r.exit_code == 1
    assert "jamais été versionné" in _texte(r)


# ── retention ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["Enabled", "Suspended"])
def test_retention_garde_l_etat(runner, mock_api, status) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg(status, 30)))
    corps = _capture(mock_api, _cfg(status, 60))
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID, "--days", "60"])
    assert r.exit_code == 0, r.output
    assert corps == [{"status": status, "noncurrent_version_expiration_days": 60}]
    assert "60 jours" in r.output


def test_retention_bucket_jamais_versionne(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg(None, None, "none")))
    corps = _capture(mock_api, _cfg())
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID, "--days", "60"])
    assert r.exit_code == 1
    assert corps == []
    assert "enable" in r.output


def test_retention_raccourcie_refusee(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg("Suspended", 90)))
    corps = _capture(mock_api, _cfg("Suspended", 10))
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID, "-d", "10"], input="n\n")
    assert r.exit_code != 0
    assert corps == []


def test_retention_sans_duree_refusee(runner, mock_api) -> None:
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID])
    assert r.exit_code != 0


# ── erreurs de l'API ────────────────────────────────────────────────────────

def test_conflit_409_message_de_l_api(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg("Enabled", 30)))
    mock_api.put(URL).mock(return_value=httpx.Response(
        409, json={"detail": "Une règle de cycle de vie garde les versions 365 jours : indiquez au moins 365 jours."}))
    r = runner.invoke(app, ["bucket", "versioning", "retention", BID, "-d", "60"])
    assert r.exit_code == 1
    assert "au moins 365 jours" in _texte(r)


def test_panne_502_enregistre_non_applique(runner, mock_api) -> None:
    mock_api.put(URL).mock(return_value=httpx.Response(
        502, json={"detail": "Versionnage enregistré mais non appliqué (stockage injoignable)."}))
    r = runner.invoke(app, ["bucket", "versioning", "enable", BID])
    assert r.exit_code == 1
    assert "non appliqué" in _texte(r)
    assert "versioning show" in _texte(r)


def test_pas_de_commande_disable(runner) -> None:
    r = runner.invoke(app, ["bucket", "versioning", "disable", BID])
    assert r.exit_code != 0
    aide = runner.invoke(app, ["bucket", "versioning", "--help"]).output.lower()
    assert "disable" not in aide and "désactiv" not in aide.replace("jamais désactiver", "")
