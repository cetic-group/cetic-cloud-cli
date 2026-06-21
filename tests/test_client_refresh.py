"""Régression : le client CLI doit rafraîchir le JWT expiré (401) via le
`refresh_token` stocké, au lieu de remonter « Token invalide ou expiré ».

Bug d'origine : le JWT d'accès (cetic auth login / --sso) expire en ~15 min ;
le client envoyait le Bearer mais n'implémentait jamais le refresh (malgré son
docstring) → toute commande passé ce délai échouait en 401 (ex. `cetic ssh`
après un clone long → « signature du certificat refusée — Token invalide ou
expiré »). L'utilisateur devait re-login.
"""

from __future__ import annotations

import httpx
import pytest

from cetic import client, config


def test_get_refreshes_jwt_on_401(monkeypatch, mock_api):
    """401 → refresh transparent via refresh_token → rejeu → succès."""
    monkeypatch.setattr("cetic.config._load_file", lambda: {"refresh_token": "rt-1"})
    saved: dict[str, str] = {}
    monkeypatch.setattr(config, "set_value", lambda k, v: saved.__setitem__(k, v))

    foo = mock_api.get("/v1/foo")
    foo.side_effect = [
        httpx.Response(401, json={"detail": "Token invalide ou expiré"}),
        httpx.Response(200, json={"ok": True}),
    ]
    refresh = mock_api.post("/v1/auth/refresh").mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "new-jwt", "refresh_token": "rt-2", "expires_in": 900},
        )
    )

    out = client.get("/v1/foo")

    assert out == {"ok": True}
    assert refresh.called, "le refresh aurait dû être tenté"
    assert saved["api_key"] == "new-jwt"  # nouveau JWT persisté
    assert saved["refresh_token"] == "rt-2"  # rotation prise en compte


def test_no_refresh_loop_without_refresh_token(monkeypatch, mock_api):
    """Sans refresh_token (ex. clé d'API), le 401 remonte tel quel — pas de boucle."""
    monkeypatch.setattr("cetic.config._load_file", lambda: {})
    mock_api.get("/v1/foo").mock(
        return_value=httpx.Response(401, json={"detail": "Token invalide ou expiré"})
    )

    with pytest.raises(client.APIError) as ei:
        client.get("/v1/foo")
    assert ei.value.status_code == 401


def test_refresh_failure_surfaces_original_401(monkeypatch, mock_api):
    """Si le refresh échoue (refresh_token périmé), le 401 d'origine est affiché."""
    monkeypatch.setattr("cetic.config._load_file", lambda: {"refresh_token": "stale"})
    monkeypatch.setattr(config, "set_value", lambda k, v: None)

    mock_api.get("/v1/foo").mock(
        return_value=httpx.Response(401, json={"detail": "Token invalide ou expiré"})
    )
    mock_api.post("/v1/auth/refresh").mock(
        return_value=httpx.Response(401, json={"detail": "Refresh token invalide ou expiré"})
    )

    with pytest.raises(client.APIError) as ei:
        client.get("/v1/foo")
    assert ei.value.status_code == 401
