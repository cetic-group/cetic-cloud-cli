"""Tests pour `cetic bucket cors` (#1806)."""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app

BID = "11111111-2222-3333-4444-555555555555"
URL = f"/v1/buckets/{BID}/cors"
REGLE = {"allowed_origins": ["https://app.example.com"], "allowed_methods": ["GET"],
         "allowed_headers": [], "expose_headers": [], "max_age_seconds": 3600}


def _cfg(rules: list[dict[str, Any]] | None = None, status: str = "applied", rev: str = "rev1") -> dict[str, Any]:
    return {"rules": rules if rules is not None else [REGLE], "status": status,
            "error_message": None, "revision": rev}


def test_show(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg()))
    r = runner.invoke(app, ["bucket", "cors", "show", BID])
    assert r.exit_code == 0, r.output
    assert "app.example.com" in r.output


def test_set_preset_envoie_la_revision(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg([], "none", "rev0")))
    cap: dict[str, Any] = {}

    def _h(req: httpx.Request) -> httpx.Response:
        cap.update(json.loads(req.content))
        return httpx.Response(200, json=_cfg())

    mock_api.put(URL).mock(side_effect=_h)
    r = runner.invoke(app, ["bucket", "cors", "set", BID, "-o", "https://a.example.com,https://b.example.com",
                            "--preset", "upload"])
    assert r.exit_code == 0, r.output
    assert cap["expected_revision"] == "rev0"
    assert cap["rules"][0]["allowed_origins"] == ["https://a.example.com", "https://b.example.com"]
    assert cap["rules"][0]["allowed_methods"] == ["PUT", "POST"]


def test_set_add_conserve_les_regles_existantes(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg()))
    cap: dict[str, Any] = {}

    def _h(req: httpx.Request) -> httpx.Response:
        cap.update(json.loads(req.content))
        return httpx.Response(200, json=_cfg())

    mock_api.put(URL).mock(side_effect=_h)
    r = runner.invoke(app, ["bucket", "cors", "set", BID, "-o", "https://b.example.com", "--add"])
    assert r.exit_code == 0, r.output
    assert len(cap["rules"]) == 2


def test_set_force_sans_revision(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg()))
    cap: dict[str, Any] = {}

    def _h(req: httpx.Request) -> httpx.Response:
        cap.update(json.loads(req.content))
        return httpx.Response(200, json=_cfg())

    mock_api.put(URL).mock(side_effect=_h)
    r = runner.invoke(app, ["bucket", "cors", "set", BID, "-o", "https://a.example.com", "--force"])
    assert r.exit_code == 0, r.output
    assert "expected_revision" not in cap


def test_set_conflit_409(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg()))
    mock_api.put(URL).mock(return_value=httpx.Response(409, json={"detail": "Les règles CORS ont changé"}))
    r = runner.invoke(app, ["bucket", "cors", "set", BID, "-o", "https://a.example.com"])
    assert r.exit_code == 1
    assert "ont changé" in r.output and "--force" in r.output


def test_set_fichier(runner, mock_api, tmp_path) -> None:
    f = tmp_path / "cors.json"
    f.write_text(json.dumps({"rules": [REGLE]}))
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg([], "none")))
    cap: dict[str, Any] = {}

    def _h(req: httpx.Request) -> httpx.Response:
        cap.update(json.loads(req.content))
        return httpx.Response(200, json=_cfg())

    mock_api.put(URL).mock(side_effect=_h)
    r = runner.invoke(app, ["bucket", "cors", "set", BID, "--file", str(f)])
    assert r.exit_code == 0, r.output
    assert cap["rules"] == [REGLE]


def test_set_fichier_et_options_refuse(runner, mock_api, tmp_path) -> None:
    f = tmp_path / "c.json"
    f.write_text("[]")
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg()))
    r = runner.invoke(app, ["bucket", "cors", "set", BID, "--file", str(f), "-o", "https://a.example.com"])
    assert r.exit_code == 1


def test_set_sans_origine(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg()))
    r = runner.invoke(app, ["bucket", "cors", "set", BID, "--preset", "read"])
    assert r.exit_code == 1
    assert "origine" in r.output


def test_set_methode_invalide(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg()))
    r = runner.invoke(app, ["bucket", "cors", "set", BID, "-o", "https://a.example.com", "-m", "PATCH"])
    assert r.exit_code == 1
    assert "PATCH" in r.output


def test_delete_avec_revision(runner, mock_api) -> None:
    mock_api.get(URL).mock(return_value=httpx.Response(200, json=_cfg()))
    route = mock_api.delete(URL).mock(return_value=httpx.Response(204))
    r = runner.invoke(app, ["bucket", "cors", "delete", BID, "-y"])
    assert r.exit_code == 0, r.output
    assert route.calls.last.request.url.params["expected_revision"] == "rev1"


def test_test_autorise(runner, mock_api) -> None:
    mock_api.post(URL + "/test").mock(return_value=httpx.Response(
        200, json={"allowed": True, "rule_index": 0, "reason": None, "header": None, "ignored_headers": []}))
    r = runner.invoke(app, ["bucket", "cors", "test", BID, "-o", "https://app.example.com"])
    assert r.exit_code == 0, r.output
    assert "Autorisé" in r.output


def test_test_refuse(runner, mock_api) -> None:
    mock_api.post(URL + "/test").mock(return_value=httpx.Response(
        200, json={"allowed": False, "rule_index": None, "reason": "method", "header": None,
                   "ignored_headers": ["Origin"]}))
    r = runner.invoke(app, ["bucket", "cors", "test", BID, "-o", "https://app.example.com", "-m", "PUT"])
    assert r.exit_code == 1
    assert "méthode" in r.output
