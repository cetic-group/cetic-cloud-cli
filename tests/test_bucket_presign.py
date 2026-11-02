"""Tests pour `cetic bucket presign` (#1806)."""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app

BID = "11111111-2222-3333-4444-555555555555"
URL = f"/v1/buckets/{BID}/presign"


def _rep(**kw: Any) -> dict[str, Any]:
    base = {"url": "https://s3.test/signed", "key": "u/a.png", "method": "PUT",
            "expires_at": "2026-11-01T10:00:00Z", "password_protected": False}
    base.update(kw)
    return base


def _capture(mock_api, reponse: dict[str, Any]) -> dict[str, Any]:
    cap: dict[str, Any] = {}

    def _h(req: httpx.Request) -> httpx.Response:
        cap.update(json.loads(req.content))
        return httpx.Response(200, json=reponse)

    mock_api.post(URL).mock(side_effect=_h)
    return cap


def test_get_par_defaut(runner, mock_api) -> None:
    cap = _capture(mock_api, _rep(method="GET"))
    r = runner.invoke(app, ["bucket", "presign", BID, "u/a.png"])
    assert r.exit_code == 0, r.output
    assert cap == {"key": "u/a.png", "method": "GET", "expires_in": 3600}
    assert "https://s3.test/signed" in r.output


def test_put_borne_envoie_taille_et_type(runner, mock_api) -> None:
    cap = _capture(mock_api, _rep(required_headers={"Content-Length": "2048", "Content-Type": "image/png"}))
    r = runner.invoke(app, ["bucket", "presign", BID, "u/a.png", "-m", "put", "-e", "600",
                            "--content-length", "2048", "--content-type", "image/png"])
    assert r.exit_code == 0, r.output
    assert cap == {"key": "u/a.png", "method": "PUT", "expires_in": 600,
                   "content_length": 2048, "content_type": "image/png"}
    assert "Content-Length" in r.output and "à l'identique" in r.output


def test_borne_refusee_en_get(runner, mock_api) -> None:
    r = runner.invoke(app, ["bucket", "presign", BID, "k", "--content-length", "10"])
    assert r.exit_code == 1
    assert "PUT" in r.output


def test_taille_invalide(runner, mock_api) -> None:
    r = runner.invoke(app, ["bucket", "presign", BID, "k", "-m", "PUT", "--content-length", "0"])
    assert r.exit_code == 1
    assert "supérieur à 0" in r.output


def test_methode_invalide(runner, mock_api) -> None:
    r = runner.invoke(app, ["bucket", "presign", BID, "k", "-m", "DELETE"])
    assert r.exit_code == 1


def test_mot_de_passe_transmis(runner, mock_api) -> None:
    cap = _capture(mock_api, _rep(method="GET", password_protected=True))
    r = runner.invoke(app, ["bucket", "presign", BID, "k", "--password", "secret"])
    assert r.exit_code == 0, r.output
    assert cap["password"] == "secret"


def test_refus_de_la_plateforme_affiche_la_raison(runner, mock_api) -> None:
    mock_api.post(URL).mock(return_value=httpx.Response(
        422, json={"detail": "La taille demandée (5000 octets) dépasse le maximum autorisé (1000 octets)."}))
    r = runner.invoke(app, ["bucket", "presign", BID, "k", "-m", "PUT", "--content-length", "5000"])
    assert r.exit_code == 1
    assert "dépasse le maximum" in r.output
