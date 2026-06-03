"""Vérifie que le client envoie X-CCP-Client: cli + User-Agent: cetic-cli/<version>."""
from __future__ import annotations

import httpx

from cetic import __version__, client


def test_headers_include_ccp_client_and_user_agent(mock_api):
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(200, json={"ok": True})

    mock_api.get("/v1/regions").mock(side_effect=_handler)
    client.get("/v1/regions")
    assert captured.get("x-ccp-client") == "cli"
    assert captured.get("user-agent") == f"cetic-cli/{__version__}"
    # Le token de test (conftest cfg_env) est toujours envoyé.
    assert captured.get("authorization") == "Bearer test-token"


def test_headers_without_token(mock_api, monkeypatch):
    monkeypatch.delenv("CCP_API_KEY", raising=False)
    # Neutralise uniquement le token (pas api_url, sinon fallback sur l'URL prod).
    monkeypatch.setattr("cetic.client._get_token", lambda: None)
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(200, json={})

    mock_api.get("/v1/regions").mock(side_effect=_handler)
    client.get("/v1/regions")
    assert captured.get("x-ccp-client") == "cli"
    assert "authorization" not in captured
