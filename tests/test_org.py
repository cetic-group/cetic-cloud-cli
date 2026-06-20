"""Tests `cetic org` — focus régression `switch` (config.set_value) + alias `-h`."""

from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app


def test_org_switch_persists_token(runner, mock_api, monkeypatch) -> None:
    """`switch` doit persister le nouveau JWT via config.set_value (régression).

    Bug historique : la commande appelait `config.set(...)` (inexistant) →
    AttributeError. On vérifie que set_value est appelé avec le token renvoyé.
    """
    captured: dict[str, Any] = {}

    def fake_set_value(key: str, value: str) -> None:
        captured[key] = value

    monkeypatch.setattr("cetic.config.set_value", fake_set_value)

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"access_token": "new-jwt-token", "active_org_id": "org-123"}
        )

    mock_api.post("/v1/auth/switch-org").mock(side_effect=handler)

    result = runner.invoke(app, ["org", "switch", "org-123"])

    assert result.exit_code == 0, result.output
    # Le backend attend target_org_id, pas org_id (sinon retour silencieux sur
    # l'org par défaut — bug live 2026-06-20).
    assert captured["body"] == {"target_org_id": "org-123"}
    assert captured.get("api_key") == "new-jwt-token"
    assert "Org active mise à jour" in result.output


def test_org_switch_warns_when_backend_returns_other_org(runner, mock_api, monkeypatch) -> None:
    """Si le backend renvoie une autre org que celle demandée, on n'affiche pas ✓."""
    monkeypatch.setattr("cetic.config.set_value", lambda k, v: None)
    mock_api.post("/v1/auth/switch-org").mock(
        return_value=httpx.Response(
            200, json={"access_token": "tok", "active_org_id": "default-org"}
        )
    )

    result = runner.invoke(app, ["org", "switch", "org-123"])

    assert result.exit_code == 0, result.output
    assert "Org active mise à jour" not in result.output
    assert "default-org" in result.output


def test_org_switch_no_token_in_response(runner, mock_api, monkeypatch) -> None:
    """Si l'API ne renvoie pas de token, on n'appelle pas set_value mais on n'échoue pas."""
    called = {"set": False}

    def fake_set_value(key: str, value: str) -> None:
        called["set"] = True

    monkeypatch.setattr("cetic.config.set_value", fake_set_value)
    mock_api.post("/v1/auth/switch-org").mock(return_value=httpx.Response(200, json={}))

    result = runner.invoke(app, ["org", "switch", "org-123"])

    assert result.exit_code == 0, result.output
    assert called["set"] is False


def test_org_switch_api_error(runner, mock_api) -> None:
    """Erreur API → exit 1 + message d'erreur."""
    mock_api.post("/v1/auth/switch-org").mock(
        return_value=httpx.Response(404, json={"detail": "org introuvable"})
    )

    result = runner.invoke(app, ["org", "switch", "bad-id"])

    assert result.exit_code == 1
    assert "introuvable" in result.output


def test_dash_h_alias_root(runner) -> None:
    """`-h` est un alias de `--help` à la racine."""
    result = runner.invoke(app, ["-h"])
    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_dash_h_alias_subcommand(runner) -> None:
    """`-h` fonctionne aussi sur une sous-commande (propagation Click)."""
    result = runner.invoke(app, ["org", "-h"])
    assert result.exit_code == 0
    assert "switch" in result.output


def test_dash_v_version(runner) -> None:
    """`-v` affiche la version (alias de --version)."""
    from cetic import __version__

    result = runner.invoke(app, ["-v"])
    assert result.exit_code == 0
    assert __version__ in result.output
