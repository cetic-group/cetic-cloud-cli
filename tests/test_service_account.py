"""Tests pour `cetic service-account` — couvre create reveal-once + rotate + list scope + revoke."""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app


SA_ID = "deadbeef-cafe-babe-face-feedfacefeed"
SA_ID2 = "11112222-3333-4444-5555-666677778888"
TENANT_ID = "12345678-1234-1234-1234-123456789012"
ORG_ID = "abcd1234-abcd-1234-abcd-1234567890ab"


def _sa(sid: str = SA_ID, name: str = "ci-pipeline", token: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": sid,
        "tenant_id": TENANT_ID,
        "org_id": ORG_ID,
        "name": name,
        "description": None,
        "token_prefix": "ccp_sa_AbCdEfGh",
        "last_used_at": None,
        "expires_at": None,
        "rotated_at": None,
        "created_at": "2026-05-10T10:00:00Z",
    }
    if token is not None:
        payload["token"] = token
    return payload


# ---------------------------------------------------------------------------
# list / get
# ---------------------------------------------------------------------------


def test_list_service_accounts(runner, mock_api):
    mock_api.get("/v1/service-accounts").mock(
        return_value=httpx.Response(200, json=[_sa(), _sa(SA_ID2, "another")])
    )
    result = runner.invoke(app, ["service-account", "list"])
    assert result.exit_code == 0, result.stdout
    assert "ci-pipeline" in result.stdout
    assert "another" in result.stdout
    assert "Service accounts (2)" in result.stdout


def test_list_never_reveals_token(runner, mock_api):
    """La liste ne doit JAMAIS contenir un token complet."""
    sa_with_token = _sa(token="ccp_sa_PLAINTEXT_TOKEN_SHOULD_NOT_LEAK")
    mock_api.get("/v1/service-accounts").mock(
        return_value=httpx.Response(200, json=[sa_with_token])
    )
    result = runner.invoke(app, ["service-account", "list"])
    assert result.exit_code == 0
    # Le préfixe (8-16 chars) est OK, mais pas le token full.
    assert "PLAINTEXT_TOKEN_SHOULD_NOT_LEAK" not in result.stdout


def test_get_by_id(runner, mock_api):
    mock_api.get(f"/v1/service-accounts/{SA_ID}").mock(
        return_value=httpx.Response(200, json=_sa())
    )
    result = runner.invoke(app, ["service-account", "get", SA_ID])
    assert result.exit_code == 0, result.stdout
    assert "ci-pipeline" in result.stdout


def test_get_by_name(runner, mock_api):
    mock_api.get("/v1/service-accounts").mock(
        return_value=httpx.Response(200, json=[_sa()])
    )
    mock_api.get(f"/v1/service-accounts/{SA_ID}").mock(
        return_value=httpx.Response(200, json=_sa())
    )
    result = runner.invoke(app, ["service-account", "get", "ci-pipeline"])
    assert result.exit_code == 0, result.stdout
    assert "ci-pipeline" in result.stdout


# ---------------------------------------------------------------------------
# create — reveal-once + keyring
# ---------------------------------------------------------------------------


def test_create_reveals_token_once(runner, mock_api, mock_keyring):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_sa(token="ccp_sa_FullTokenAbCdEf"))

    mock_api.post("/v1/service-accounts").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["service-account", "create", "--name", "ci-pipeline", "--expires-in-days", "365"],
        input="n\n",  # refuse keyring save
    )
    assert result.exit_code == 0, result.stdout
    # Affiché 1×.
    assert "ccp_sa_FullTokenAbCdEf" in result.stdout
    assert captured["body"]["name"] == "ci-pipeline"
    assert captured["body"]["expires_in_days"] == 365
    # Non sauvegardé dans le trousseau (réponse "n").
    assert f"cetic-service-account::{SA_ID}" not in mock_keyring


def test_create_save_keyring_flag(runner, mock_api, mock_keyring):
    mock_api.post("/v1/service-accounts").mock(
        return_value=httpx.Response(201, json=_sa(token="ccp_sa_StoredInKeyring"))
    )
    result = runner.invoke(
        app,
        ["service-account", "create", "--name", "ci",
         "--save-keyring"],
    )
    assert result.exit_code == 0, result.stdout
    # Stocké automatiquement sans prompt.
    assert mock_keyring[f"cetic-service-account::{SA_ID}"] == "ccp_sa_StoredInKeyring"


def test_create_with_description(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_sa(token="ccp_sa_X"))

    mock_api.post("/v1/service-accounts").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["service-account", "create", "--name", "ci",
         "--description", "Pipeline GitHub Actions",
         "--save-keyring"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["description"] == "Pipeline GitHub Actions"


# ---------------------------------------------------------------------------
# rotate — invalide ancien token
# ---------------------------------------------------------------------------


def test_rotate_returns_new_token(runner, mock_api, mock_keyring):
    mock_keyring[f"cetic-service-account::{SA_ID}"] = "ccp_sa_OldToken"
    rotated = _sa(token="ccp_sa_NewToken123")
    rotated["rotated_at"] = "2026-05-11T00:00:00Z"
    mock_api.post(f"/v1/service-accounts/{SA_ID}/rotate").mock(
        return_value=httpx.Response(200, json=rotated)
    )
    result = runner.invoke(
        app,
        ["service-account", "rotate", SA_ID, "--save-keyring"],
    )
    assert result.exit_code == 0, result.stdout
    assert "ccp_sa_NewToken123" in result.stdout
    assert "désormais invalide" in result.stdout or "invalide" in result.stdout.lower()
    # Le trousseau a été mis à jour.
    assert mock_keyring[f"cetic-service-account::{SA_ID}"] == "ccp_sa_NewToken123"


def test_rotate_by_name(runner, mock_api, mock_keyring):
    mock_api.get("/v1/service-accounts").mock(
        return_value=httpx.Response(200, json=[_sa()])
    )
    mock_api.post(f"/v1/service-accounts/{SA_ID}/rotate").mock(
        return_value=httpx.Response(200, json=_sa(token="ccp_sa_Rotated"))
    )
    result = runner.invoke(
        app,
        ["service-account", "rotate", "ci-pipeline"],
        input="n\n",  # refuse keyring
    )
    assert result.exit_code == 0, result.stdout
    assert "ccp_sa_Rotated" in result.stdout


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------


def test_revoke_with_yes(runner, mock_api, mock_keyring):
    mock_keyring[f"cetic-service-account::{SA_ID}"] = "ccp_sa_X"
    mock_api.delete(f"/v1/service-accounts/{SA_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["service-account", "revoke", SA_ID, "--yes"])
    assert result.exit_code == 0
    assert "supprimé" in result.stdout
    # Trousseau cleané.
    assert f"cetic-service-account::{SA_ID}" not in mock_keyring


def test_revoke_aborted_when_no(runner, mock_api):
    result = runner.invoke(app, ["service-account", "revoke", SA_ID], input="n\n")
    assert result.exit_code != 0  # Abort
    assert not any(call.request.method == "DELETE" for call in mock_api.calls)


def test_revoke_404_french(runner, mock_api):
    mock_api.delete(f"/v1/service-accounts/{SA_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(app, ["service-account", "revoke", SA_ID, "--yes"])
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


# ---------------------------------------------------------------------------
# Wire-up
# ---------------------------------------------------------------------------


def test_service_account_app_registered_in_main():
    from cetic.main import app as main_app

    typer_groups = [g.typer_instance for g in main_app.registered_groups if g.typer_instance is not None]
    assert any(g.info.name == "service-account" for g in typer_groups), \
        "service_account_app non enregistré dans main.app"
