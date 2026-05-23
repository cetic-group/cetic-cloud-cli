"""Tests pour `cetic key` — add (avec scope) + list (colonne Scope) + delete (403)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from cetic.main import app


KEY_ID = "11111111-2222-3333-4444-555555555555"
KEY_ID2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
KEY_ID3 = "99999999-8888-7777-6666-555555555555"
TENANT_ID = "12345678-1234-1234-1234-123456789012"


def _key(
    kid: str = KEY_ID,
    name: str = "laptop",
    scope: str = "user",
    fingerprint: str = "SHA256:abcdef0123456789",
) -> dict[str, Any]:
    return {
        "id": kid,
        "name": name,
        "fingerprint": fingerprint,
        "scope": scope,
        "created_by_tenant_id": TENANT_ID,
        "created_at": "2026-05-22T10:00:00Z",
    }


@pytest.fixture
def pubkey_file(tmp_path: Path) -> Path:
    """Crée un fichier .pub minimaliste pour --file."""
    p = tmp_path / "id_ed25519.pub"
    p.write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyDataHere user@host\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# add — scope par défaut + scope explicite
# ---------------------------------------------------------------------------


def test_add_default_scope_is_user(runner, mock_api, pubkey_file):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_key(scope="user"))

    mock_api.post("/v1/ssh-keys").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["key", "add", "--name", "laptop", "--file", str(pubkey_file)],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["scope"] == "user"
    assert captured["body"]["name"] == "laptop"
    assert "public_key" in captured["body"]
    assert "Clé ajoutée" in result.stdout
    assert "user" in result.stdout


def test_add_scope_org(runner, mock_api, pubkey_file):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_key(scope="org"))

    mock_api.post("/v1/ssh-keys").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["key", "add", "--name", "shared-org", "--file", str(pubkey_file), "--scope", "org"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["scope"] == "org"
    assert "org" in result.stdout


def test_add_scope_tenant(runner, mock_api, pubkey_file):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_key(scope="tenant"))

    mock_api.post("/v1/ssh-keys").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["key", "add", "--name", "ops-bastion", "--file", str(pubkey_file), "--scope", "tenant"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["scope"] == "tenant"
    assert "tenant" in result.stdout


def test_add_scope_case_insensitive(runner, mock_api, pubkey_file):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_key(scope="tenant"))

    mock_api.post("/v1/ssh-keys").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["key", "add", "--name", "k", "--file", str(pubkey_file), "--scope", "TENANT"],
    )
    assert result.exit_code == 0, result.stdout
    # Normalisé en lowercase avant d'être envoyé.
    assert captured["body"]["scope"] == "tenant"


def test_add_scope_invalid_rejected(runner, mock_api, pubkey_file):
    result = runner.invoke(
        app,
        ["key", "add", "--name", "k", "--file", str(pubkey_file), "--scope", "world"],
    )
    assert result.exit_code == 1
    assert "invalide" in result.stdout.lower()
    # Aucun POST n'a été émis.
    assert not any(call.request.method == "POST" for call in mock_api.calls)


# ---------------------------------------------------------------------------
# list — colonne Scope + JSON exposé tel quel
# ---------------------------------------------------------------------------


def test_list_table_shows_scope_column(runner, mock_api):
    mock_api.get("/v1/ssh-keys").mock(
        return_value=httpx.Response(
            200,
            json=[
                _key(KEY_ID, "perso", scope="user"),
                _key(KEY_ID2, "team", scope="org"),
                _key(KEY_ID3, "ops", scope="tenant"),
            ],
        )
    )
    result = runner.invoke(app, ["key", "list"])
    assert result.exit_code == 0, result.stdout
    # En-tête de colonne présent.
    assert "Scope" in result.stdout
    # Les 3 valeurs apparaissent.
    assert "user" in result.stdout
    assert "org" in result.stdout
    assert "tenant" in result.stdout
    assert "perso" in result.stdout
    assert "team" in result.stdout
    assert "ops" in result.stdout


def test_list_json_exposes_scope_and_tenant_id(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    payload = [
        _key(KEY_ID, "perso", scope="user"),
        _key(KEY_ID2, "team", scope="org"),
    ]
    mock_api.get("/v1/ssh-keys").mock(return_value=httpx.Response(200, json=payload))

    result = runner.invoke(app, ["key", "list"])
    assert result.exit_code == 0, result.stdout
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["scope"] == "user"
    assert data[0]["created_by_tenant_id"] == TENANT_ID
    assert data[1]["scope"] == "org"


def test_list_empty(runner, mock_api):
    mock_api.get("/v1/ssh-keys").mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(app, ["key", "list"])
    assert result.exit_code == 0, result.stdout
    assert "Aucune clé" in result.stdout


def test_list_legacy_payload_without_scope_renders_user(runner, mock_api):
    """Backend ancien qui ne renvoie pas encore `scope` → fallback `user`."""
    legacy = {
        "id": KEY_ID,
        "name": "old",
        "fingerprint": "SHA256:xxx",
        "created_at": "2026-05-22T10:00:00Z",
    }
    mock_api.get("/v1/ssh-keys").mock(return_value=httpx.Response(200, json=[legacy]))
    result = runner.invoke(app, ["key", "list"])
    assert result.exit_code == 0, result.stdout
    assert "user" in result.stdout


# ---------------------------------------------------------------------------
# delete — 403 doit s'afficher proprement en FR
# ---------------------------------------------------------------------------


def test_delete_403_renders_friendly_message(runner, mock_api):
    mock_api.delete(f"/v1/ssh-keys/{KEY_ID}").mock(
        return_value=httpx.Response(
            403, json={"detail": "not the owner of this user-scoped key"}
        )
    )
    result = runner.invoke(app, ["key", "delete", KEY_ID, "--force"])
    assert result.exit_code == 1
    assert "Accès refusé" in result.stdout
    # Mention explicite des règles de scope dans le message.
    assert "user" in result.stdout
    assert "owner" in result.stdout or "admin" in result.stdout


def test_delete_404_renders_friendly_message(runner, mock_api):
    mock_api.delete(f"/v1/ssh-keys/{KEY_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(app, ["key", "delete", KEY_ID, "--force"])
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


def test_delete_aborted_without_force(runner, mock_api):
    result = runner.invoke(app, ["key", "delete", KEY_ID], input="n\n")
    assert result.exit_code != 0  # Abort
    assert not any(call.request.method == "DELETE" for call in mock_api.calls)


def test_delete_success(runner, mock_api):
    mock_api.delete(f"/v1/ssh-keys/{KEY_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["key", "delete", KEY_ID, "--force"])
    assert result.exit_code == 0, result.stdout
    assert "supprimée" in result.stdout
