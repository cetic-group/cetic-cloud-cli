"""Tests pour `cetic registry` — couvre les 16 commandes + erreurs API."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cetic.main import app


# ---------------------------------------------------------------------------
# Fixtures de payloads
# ---------------------------------------------------------------------------

REG_ID = "11111111-2222-3333-4444-555555555555"
REG2_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _registry_payload(rid: str = REG_ID, name: str = "myreg", **overrides: Any) -> dict[str, Any]:
    base = {
        "id": rid,
        "name": name,
        "region": "RNN",
        "expose_public": False,
        "expose_private": True,
        "status": "running",
        "url": f"https://{name}-{rid[:8]}.registry-rnn.cloud.cetic-group.com",
        "storage_used_gb": 0,
        "last_push_at": None,
        "admin_username": "admin",
        "admin_password": "s3cr3t-pwd",
        "s3_access_key": "AKIAFAKE",
        "s3_secret_key": "supersecret",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Top-level CRUD
# ---------------------------------------------------------------------------


def test_create_happy_path_posts_body_and_offers_keyring(runner, mock_api, mock_keyring):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_registry_payload())

    mock_api.post("/v1/registries").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "registry", "create",
            "-n", "myreg",
            "-r", "RNN",
            "--public",
            "--private",
            "--tag", "env=prod",
            "--tag", "team=core",
        ],
        input="y\n",  # accept "Sauvegarder dans le trousseau ?"
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert captured["body"]["name"] == "myreg"
    assert captured["body"]["expose_public"] is True
    assert captured["body"]["expose_private"] is True
    assert "vpc_id" not in captured["body"]
    assert "vnet_id" not in captured["body"]
    assert "exposure" not in captured["body"]
    assert captured["body"]["tags"] == {"env": "prod", "team": "core"}
    # Le mot de passe est affiché 1× dans la sortie.
    assert "s3cr3t-pwd" in result.stdout
    # Et stocké dans le keyring stub (oui à la sauvegarde).
    assert mock_keyring[f"cetic-registry::{REG_ID}:admin"] == "s3cr3t-pwd"


def test_create_default_is_private_only(runner, mock_api, mock_keyring):
    """Sans flags, par défaut : --no-public --private (privé seul)."""
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_registry_payload())

    mock_api.post("/v1/registries").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["registry", "create", "-n", "myreg", "-r", "RNN"],
        input="n\n",
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["expose_public"] is False
    assert captured["body"]["expose_private"] is True


def test_create_no_exposure_aborts(runner, mock_api):
    """`--no-public --no-private` doit échouer côté CLI avant l'API."""
    result = runner.invoke(
        app,
        ["registry", "create", "-n", "x", "-r", "RNN", "--no-public", "--no-private"],
    )
    assert result.exit_code == 1
    assert "exposition" in result.stdout.lower()


def test_create_quota_409_message_french(runner, mock_api):
    mock_api.post("/v1/registries").mock(
        return_value=httpx.Response(409, json={"detail": "max_registries=2 limit reached"})
    )
    result = runner.invoke(
        app,
        ["registry", "create", "-n", "x", "-r", "RNN"],
    )
    assert result.exit_code == 1
    assert "Quota atteint" in result.stdout


def test_create_invalid_tag_format(runner, mock_api):
    result = runner.invoke(
        app,
        ["registry", "create", "-n", "x", "-r", "RNN", "--tag", "novalue"],
    )
    assert result.exit_code == 1
    assert "Tag invalide" in result.stdout


# ---------------------------------------------------------------------------
# `update` (PATCH)
# ---------------------------------------------------------------------------


def test_update_toggles_expose_public(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_registry_payload(expose_public=True))

    mock_api.patch(f"/v1/registries/{REG_ID}").mock(side_effect=_capture)
    result = runner.invoke(app, ["registry", "update", REG_ID, "--public"])
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"expose_public": True}


def test_update_disable_public(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_registry_payload(expose_public=False))

    mock_api.patch(f"/v1/registries/{REG_ID}").mock(side_effect=_capture)
    result = runner.invoke(app, ["registry", "update", REG_ID, "--no-public"])
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"expose_public": False}


def test_update_tags_only(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_registry_payload())

    mock_api.patch(f"/v1/registries/{REG_ID}").mock(side_effect=_capture)
    result = runner.invoke(
        app, ["registry", "update", REG_ID, "--tags", "env=prod,team=core"]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"tags": {"env": "prod", "team": "core"}}


def test_update_no_changes_aborts(runner, mock_api):
    result = runner.invoke(app, ["registry", "update", REG_ID])
    assert result.exit_code == 1
    assert "modification" in result.stdout.lower()


def test_attach_ip_deprecated_message(runner, mock_api):
    result = runner.invoke(app, ["registry", "attach-ip", REG_ID])
    assert result.exit_code == 2
    assert "update" in result.stdout
    assert "--public" in result.stdout


def test_detach_ip_deprecated_message(runner, mock_api):
    result = runner.invoke(app, ["registry", "detach-ip", REG_ID])
    assert result.exit_code == 2
    assert "update" in result.stdout
    assert "--no-public" in result.stdout


def test_list_table_format(runner, mock_api):
    mock_api.get("/v1/registries").mock(
        return_value=httpx.Response(200, json=[_registry_payload(), _registry_payload(REG2_ID, "other")])
    )
    result = runner.invoke(app, ["registry", "list"])
    assert result.exit_code == 0
    assert "myreg" in result.stdout
    assert "other" in result.stdout
    assert "Registries (2)" in result.stdout


def test_list_json_format(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/registries").mock(
        return_value=httpx.Response(200, json=[_registry_payload()])
    )
    result = runner.invoke(app, ["registry", "list"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed[0]["name"] == "myreg"


def test_list_yaml_format(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "yaml")
    mock_api.get("/v1/registries").mock(
        return_value=httpx.Response(200, json=[_registry_payload()])
    )
    result = runner.invoke(app, ["registry", "list"])
    assert result.exit_code == 0
    assert "name: myreg" in result.stdout


def test_get_masks_secrets_by_default(runner, mock_api):
    mock_api.get("/v1/registries").mock(
        return_value=httpx.Response(200, json=[_registry_payload()])
    )
    mock_api.get(f"/v1/registries/{REG_ID}").mock(
        return_value=httpx.Response(200, json=_registry_payload())
    )
    result = runner.invoke(app, ["registry", "get", "myreg"])
    assert result.exit_code == 0
    assert "s3cr3t-pwd" not in result.stdout
    assert "supersecret" not in result.stdout
    assert "***" in result.stdout


def test_get_reveals_secrets_with_flag(runner, mock_api):
    mock_api.get(f"/v1/registries/{REG_ID}").mock(
        return_value=httpx.Response(200, json=_registry_payload())
    )
    result = runner.invoke(app, ["registry", "get", REG_ID, "--reveal-secrets"])
    assert result.exit_code == 0
    assert "s3cr3t-pwd" in result.stdout


def test_get_404_french(runner, mock_api):
    mock_api.get(f"/v1/registries/{REG_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(app, ["registry", "get", REG_ID])
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


def test_get_401_french(runner, mock_api):
    mock_api.get(f"/v1/registries/{REG_ID}").mock(
        return_value=httpx.Response(401, json={"detail": "Unauthorized"})
    )
    result = runner.invoke(app, ["registry", "get", REG_ID])
    assert result.exit_code == 1
    assert "Non authentifié" in result.stdout


def test_get_500_french(runner, mock_api):
    mock_api.get(f"/v1/registries/{REG_ID}").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )
    result = runner.invoke(app, ["registry", "get", REG_ID])
    assert result.exit_code == 1
    assert "Erreur serveur" in result.stdout


def test_delete_with_yes_flag(runner, mock_api, mock_keyring):
    mock_keyring["cetic-registry::" + REG_ID + ":admin"] = "x"
    mock_api.delete(f"/v1/registries/{REG_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["registry", "delete", REG_ID, "--yes"])
    assert result.exit_code == 0
    assert "supprimée" in result.stdout
    assert "cetic-registry::" + REG_ID + ":admin" not in mock_keyring


def test_delete_aborted_when_no(runner, mock_api):
    result = runner.invoke(app, ["registry", "delete", REG_ID], input="n\n")
    assert result.exit_code != 0  # Abort
    # L'API ne doit pas avoir été appelée.
    assert not any(call.request.method == "DELETE" for call in mock_api.calls)


# ---------------------------------------------------------------------------
# `login`
# ---------------------------------------------------------------------------


def test_login_uses_keyring_password(runner, mock_api, mock_keyring, mock_subprocess):
    mock_keyring["cetic-registry::" + REG_ID + ":admin"] = "stored-pwd"
    mock_api.get(f"/v1/registries/{REG_ID}").mock(
        return_value=httpx.Response(200, json=_registry_payload())
    )
    result = runner.invoke(app, ["registry", "login", REG_ID])
    assert result.exit_code == 0, result.stdout
    cmd = mock_subprocess["args"]
    assert cmd[0] == "docker"
    assert cmd[1] == "login"
    # Hostname dérivé de `url = https://myreg-<id8>.registry-rnn.cloud.cetic-group.com`
    assert any("registry-rnn.cloud.cetic-group.com" in part for part in cmd), cmd
    assert "--password-stdin" in cmd
    assert mock_subprocess["input"] == "stored-pwd"


def test_login_keyring_miss_prompts(runner, mock_api, mock_keyring, mock_subprocess, monkeypatch):
    mock_api.get(f"/v1/registries/{REG_ID}").mock(
        return_value=httpx.Response(200, json=_registry_payload())
    )
    # Patch le prompt masqué — getpass ne lit pas depuis CliRunner.input.
    monkeypatch.setattr(
        "cetic.commands.registry.prompt_password", lambda _label="": "prompted-pwd"
    )
    result = runner.invoke(app, ["registry", "login", REG_ID])
    assert result.exit_code == 0, result.stdout
    assert mock_subprocess["input"] == "prompted-pwd"


def test_login_docker_missing(runner, mock_api, monkeypatch):
    monkeypatch.setattr("cetic.commands.registry.shutil.which", lambda _: None)
    result = runner.invoke(app, ["registry", "login", REG_ID])
    assert result.exit_code == 1
    assert "docker" in result.stdout.lower()


def test_login_docker_fails(runner, mock_api, mock_keyring, mock_subprocess):
    mock_keyring["cetic-registry::" + REG_ID + ":admin"] = "x"
    mock_subprocess["returncode_override"] = 1
    mock_api.get(f"/v1/registries/{REG_ID}").mock(
        return_value=httpx.Response(200, json=_registry_payload())
    )
    result = runner.invoke(app, ["registry", "login", REG_ID])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Sub-app `user`
# ---------------------------------------------------------------------------


def test_user_add_returns_password(runner, mock_api, mock_keyring):
    mock_api.post(f"/v1/registries/{REG_ID}/users").mock(
        return_value=httpx.Response(201, json={"username": "ci", "password": "ci-pwd"})
    )
    result = runner.invoke(
        app, ["registry", "user", "add", REG_ID, "--username", "ci"], input="n\n"
    )
    assert result.exit_code == 0, result.stdout
    assert "ci-pwd" in result.stdout


def test_user_list(runner, mock_api):
    mock_api.get(f"/v1/registries/{REG_ID}/users").mock(
        return_value=httpx.Response(200, json=[
            {"username": "ci", "kind": "robot", "created_at": "2026-01-01T10:00:00Z"},
            {"username": "alice", "kind": "human", "created_at": "2026-02-02T10:00:00Z"},
        ])
    )
    result = runner.invoke(app, ["registry", "user", "list", REG_ID])
    assert result.exit_code == 0
    assert "ci" in result.stdout
    assert "alice" in result.stdout


def test_user_reset(runner, mock_api, mock_keyring):
    mock_api.post(f"/v1/registries/{REG_ID}/users/ci/reset-password").mock(
        return_value=httpx.Response(200, json={"password": "new-pwd"})
    )
    result = runner.invoke(
        app, ["registry", "user", "reset", REG_ID, "ci", "--yes"], input="n\n"
    )
    assert result.exit_code == 0, result.stdout
    assert "new-pwd" in result.stdout


def test_user_delete(runner, mock_api, mock_keyring):
    mock_keyring["cetic-registry::" + REG_ID + ":ci"] = "x"
    mock_api.delete(f"/v1/registries/{REG_ID}/users/ci").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["registry", "user", "delete", REG_ID, "ci", "--yes"])
    assert result.exit_code == 0
    assert "cetic-registry::" + REG_ID + ":ci" not in mock_keyring


# ---------------------------------------------------------------------------
# Sub-app `acl`
# ---------------------------------------------------------------------------


def test_acl_set(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "a-id", "username": "ci",
                                          "repo": "myapp/*", "actions": ["pull", "push"]})

    mock_api.put(f"/v1/registries/{REG_ID}/acls").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["registry", "acl", "set", REG_ID,
         "--repo", "myapp/*", "--actions", "pull,push", "--user", "ci"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["repo"] == "myapp/*"
    assert captured["body"]["actions"] == ["pull", "push"]
    assert captured["body"]["username"] == "ci"


def test_acl_set_empty_actions(runner, mock_api):
    result = runner.invoke(
        app,
        ["registry", "acl", "set", REG_ID, "--repo", "x", "--actions", ""],
    )
    assert result.exit_code == 1


def test_acl_list(runner, mock_api):
    mock_api.get(f"/v1/registries/{REG_ID}/acls").mock(
        return_value=httpx.Response(200, json=[
            {"id": "11111111-acl-aaaa-bbbb-cccccccccccc",
             "username": "ci", "repo": "myapp/*", "actions": ["pull", "push"]},
        ])
    )
    result = runner.invoke(app, ["registry", "acl", "list", REG_ID])
    assert result.exit_code == 0
    assert "myapp/*" in result.stdout
    assert "pull,push" in result.stdout


def test_acl_remove(runner, mock_api):
    mock_api.delete(f"/v1/registries/{REG_ID}/acls/acl-id").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["registry", "acl", "remove", REG_ID, "acl-id", "--yes"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# `repos`, `tags`, `tag delete`, `gc`
# ---------------------------------------------------------------------------


def test_repos_single_page(runner, mock_api):
    mock_api.get(f"/v1/registries/{REG_ID}/repositories").mock(
        return_value=httpx.Response(200, json=["myapp/api", "myapp/web"])
    )
    result = runner.invoke(app, ["registry", "repos", REG_ID])
    assert result.exit_code == 0, result.stdout
    assert "myapp/api" in result.stdout
    assert "myapp/web" in result.stdout


def test_repos_pagination_with_all(runner, mock_api):
    page1_resp = httpx.Response(
        200,
        json=["myapp/a", "myapp/b"],
        headers={"Link": '</v2/_catalog?n=100&last=myapp%2Fb>; rel="next"'},
    )
    page2_resp = httpx.Response(200, json=["myapp/c"])

    calls: list[dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        if "last" in request.url.params:
            return page2_resp
        return page1_resp

    mock_api.get(f"/v1/registries/{REG_ID}/repositories").mock(side_effect=_handler)
    result = runner.invoke(app, ["registry", "repos", REG_ID, "--all"])
    assert result.exit_code == 0, result.stdout
    assert "myapp/a" in result.stdout
    assert "myapp/c" in result.stdout
    assert len(calls) == 2
    assert calls[1].get("last") == "myapp/b"


def test_repos_structured_detail_error_flattened(runner, mock_api):
    """Review PR #42 : `_fetch_repos_page` (httpx direct pour lire le header
    `Link`) doit passer par `_raise_for_status` → `e.detail` aplati en string.
    Un 409 à `detail` structuré (contrat #618) ferait sinon planter
    `_format_api_error` sur `(e.detail or "").lower()` (dict → AttributeError)
    et afficherait le repr Python du dict."""
    mock_api.get(f"/v1/registries/{REG_ID}/repositories").mock(
        return_value=httpx.Response(
            409,
            json={"detail": {"code": "conflict", "message": "Opération en conflit"}},
        )
    )
    result = runner.invoke(app, ["registry", "repos", REG_ID])
    assert result.exit_code == 1, result.stdout
    # message aplati affiché, pas le repr du dict ni un crash
    assert "Opération en conflit" in result.stdout
    assert "'code'" not in result.stdout
    assert "Traceback" not in result.stdout


def test_tags_list(runner, mock_api):
    mock_api.get(f"/v1/registries/{REG_ID}/repositories/myapp/api/tags").mock(
        return_value=httpx.Response(200, json=[
            {"name": "v1.0.0", "digest": "sha256:abcdef0123456789abcd",
             "pushed_at": "2026-04-01T10:00:00Z", "size_human": "123 MB"},
        ])
    )
    result = runner.invoke(app, ["registry", "tags", REG_ID, "myapp/api"])
    assert result.exit_code == 0, result.stdout
    assert "v1.0.0" in result.stdout


def test_tag_delete(runner, mock_api):
    mock_api.delete(
        f"/v1/registries/{REG_ID}/repositories/myapp/api/tags/v1.0.0"
    ).mock(return_value=httpx.Response(204))
    result = runner.invoke(
        app, ["registry", "tag", "delete", REG_ID, "myapp/api", "v1.0.0", "--yes"]
    )
    assert result.exit_code == 0


def test_gc_no_wait(runner, mock_api):
    mock_api.post(f"/v1/registries/{REG_ID}/garbage-collect").mock(
        return_value=httpx.Response(202, json={"job_id": "job-1"})
    )
    result = runner.invoke(app, ["registry", "gc", REG_ID])
    assert result.exit_code == 0, result.stdout
    assert "job-1" in result.stdout


def test_gc_with_wait(runner, mock_api, monkeypatch):
    monkeypatch.setattr("cetic.commands.registry.time.sleep", lambda _: None)
    mock_api.post(f"/v1/registries/{REG_ID}/garbage-collect").mock(
        return_value=httpx.Response(202, json={"job_id": "job-1"})
    )
    states = iter([
        httpx.Response(200, json={"status": "running"}),
        httpx.Response(200, json={"status": "succeeded", "duration_seconds": 42}),
    ])
    mock_api.get(f"/v1/registries/{REG_ID}/garbage-collect/job-1").mock(
        side_effect=lambda req: next(states)
    )
    result = runner.invoke(app, ["registry", "gc", REG_ID, "--wait"])
    assert result.exit_code == 0, result.stdout
    assert "succeeded" in result.stdout


# ---------------------------------------------------------------------------
# Helpers internes / branding guard (CCP, pas l'ancienne marque)
# ---------------------------------------------------------------------------


def test_no_legacy_brand_terminology():
    """Garde-fou : aucune référence à l'ancien branding dans les nouveaux fichiers.

    On scanne uniquement les fichiers de production (pas le fichier de test
    lui-même, sinon la liste des tokens devient un faux positif).
    """
    import pathlib

    root = pathlib.Path(__file__).parent.parent
    files = [
        root / "cetic" / "commands" / "registry.py",
        root / "cetic" / "_secrets.py",
        root / "cetic" / "_resolve.py",
    ]
    forbidden = ("cloud-lake", "cloudlake", "cloud_lake")
    for f in files:
        text = f.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{f} contient interdit : {token}"


def test_registry_app_command_count():
    """v0.41.0 : 12 top-level (8 originales + update + 2 stubs dépréciés + resize-disk) + 4 user + 3 acl + 1 tag delete = 20."""
    from cetic.commands import registry

    top_level = [c.name for c in registry.app.registered_commands]
    # create, list, get, delete, login, gc, repos, tags, update, attach-ip, detach-ip, resize-disk
    assert len(top_level) == 12
    assert "update" in top_level or any(c.name is None and c.callback.__name__ == "update" for c in registry.app.registered_commands)

    user_cmds = [c.name for c in registry.user_app.registered_commands]
    assert len(user_cmds) == 4  # add, list, reset, delete

    acl_cmds = [c.name for c in registry.acl_app.registered_commands]
    assert len(acl_cmds) == 3  # set, list, remove

    tag_cmds = [c.name for c in registry.tag_app.registered_commands]
    assert len(tag_cmds) == 1  # delete

    total = len(top_level) + len(user_cmds) + len(acl_cmds) + len(tag_cmds)
    assert total == 20


def test_redact_helper():
    from cetic.commands.registry import _redact

    item = {"id": "x", "name": "n", "admin_password": "p", "s3_secret_key": "k"}
    out = _redact(item)
    assert out["admin_password"] == "***"
    assert out["s3_secret_key"] == "***"
    assert out["id"] == "x"


def test_format_api_error_messages():
    from cetic import client as client_mod
    from cetic.commands.registry import _format_api_error

    assert "Non authentifié" in _format_api_error(client_mod.APIError(401, "x"))
    assert "Accès refusé" in _format_api_error(client_mod.APIError(403, "x"))
    assert "introuvable" in _format_api_error(client_mod.APIError(404, "x"))
    assert "Quota atteint" in _format_api_error(
        client_mod.APIError(409, "max_registries=2 reached")
    )
    assert "Conflit" in _format_api_error(client_mod.APIError(409, "duplicate name"))
    assert "Erreur serveur" in _format_api_error(client_mod.APIError(503, "x"))


def test_resolve_id_fallback_to_name(mock_api):
    """resolve_id doit lister la collection si l'arg n'est pas un UUID."""
    from cetic._resolve import resolve_id

    mock_api.get("/v1/registries").mock(
        return_value=httpx.Response(200, json=[_registry_payload(), _registry_payload(REG2_ID, "other")])
    )
    assert resolve_id("/v1/registries", "other") == REG2_ID


def test_resolve_id_passthrough_uuid(mock_api):
    from cetic._resolve import resolve_id

    # Aucun appel API attendu si déjà UUID.
    assert resolve_id("/v1/registries", REG_ID) == REG_ID
