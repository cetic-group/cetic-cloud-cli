"""Tests pour `cetic iam` — couvre CRUD roles + attach/detach + simulate + who-am-i."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from cetic.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


ROLE_ID = "11111111-2222-3333-4444-555555555555"
ROLE2_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BUILTIN_ID = "00000000-0000-0000-0000-000000000001"
TENANT_ID = "12345678-1234-1234-1234-123456789012"
ORG_ID = "abcd1234-abcd-1234-abcd-1234567890ab"
MEMBER_ID = "ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb"
SA_ID = "deadbeef-cafe-babe-face-feedfacefeed"
ASSIGN_ID = "feedface-1234-5678-9abc-def012345678"


def _role(rid: str = ROLE_ID, name: str = "RegistryReader", built_in: bool = False) -> dict[str, Any]:
    return {
        "id": rid,
        "tenant_id": TENANT_ID if not built_in else None,
        "org_id": ORG_ID if not built_in else None,
        "name": name,
        "description": "Read-only registry",
        "policy_document": {
            "version": "2026-05-10",
            "statements": [
                {
                    "effect": "Allow",
                    "actions": ["registry:Pull", "registry:List"],
                    "resources": [
                        f"arn:ccp:registry:rnn:{TENANT_ID}:registry/*"
                    ],
                }
            ],
        },
        "policy_hash": "deadbeef" * 8,
        "is_built_in": built_in,
        "created_at": "2026-05-10T10:00:00Z",
        "updated_at": "2026-05-10T10:00:00Z",
    }


def _me() -> dict[str, Any]:
    return {
        "id": TENANT_ID,
        "email": "user@cetic-group.com",
        "first_name": "Test",
        "last_name": "User",
        "company_name": "Cetic",
        "status": "active",
        "active_org_id": ORG_ID,
    }


def _member(mid: str = MEMBER_ID, email: str = "user@cetic-group.com") -> dict[str, Any]:
    return {
        "id": mid,
        "email": email,
        "role": "member",
        "accepted_at": "2026-01-01T10:00:00Z",
        "created_at": "2026-01-01T10:00:00Z",
    }


def _sa(sid: str = SA_ID, name: str = "ci-pipeline") -> dict[str, Any]:
    return {
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


def _write_policy(tmp_path: Path, *, valid: bool = True) -> Path:
    """Écrit un policy file de test (par défaut valide)."""
    if valid:
        doc = {
            "statements": [
                {
                    "sid": "AllowReadOnly",
                    "effect": "Allow",
                    "actions": ["registry:Pull", "registry:List"],
                    "resources": [
                        f"arn:ccp:registry:rnn:{TENANT_ID}:registry/*"
                    ],
                }
            ]
        }
    else:
        doc = {"statements": [{"effect": "Allow"}]}
    file = tmp_path / "policy.json"
    file.write_text(json.dumps(doc), encoding="utf-8")
    return file


# ---------------------------------------------------------------------------
# Roles list / get
# ---------------------------------------------------------------------------


def test_roles_list_default(runner, mock_api):
    mock_api.get("/v1/iam/roles").mock(
        return_value=httpx.Response(200, json=[_role(), _role(ROLE2_ID, "CustomRoleB")])
    )
    result = runner.invoke(app, ["iam", "roles", "list"])
    assert result.exit_code == 0, result.stdout
    assert "RegistryReader" in result.stdout
    assert "CustomRoleB" in result.stdout
    assert "Rôles IAM (2)" in result.stdout


def test_roles_list_built_in_filter(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_role(BUILTIN_ID, "AdminAll", built_in=True)])

    mock_api.get("/v1/iam/roles").mock(side_effect=_capture)
    result = runner.invoke(app, ["iam", "roles", "list", "--built-in"])
    assert result.exit_code == 0, result.stdout
    assert captured["params"].get("built_in") == "true"


def test_roles_list_custom_filter(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    mock_api.get("/v1/iam/roles").mock(side_effect=_capture)
    result = runner.invoke(app, ["iam", "roles", "list", "--custom"])
    assert result.exit_code == 0
    assert captured["params"].get("built_in") == "false"


def test_roles_list_built_in_and_custom_conflict(runner, mock_api):
    result = runner.invoke(app, ["iam", "roles", "list", "--built-in", "--custom"])
    assert result.exit_code == 1
    assert "mutuellement exclusifs" in result.stdout


def test_roles_get_masks_policy_by_default(runner, mock_api):
    mock_api.get("/v1/iam/roles").mock(
        return_value=httpx.Response(200, json=[_role()])
    )
    mock_api.get(f"/v1/iam/roles/{ROLE_ID}").mock(
        return_value=httpx.Response(200, json=_role())
    )
    result = runner.invoke(app, ["iam", "roles", "get", "RegistryReader"])
    assert result.exit_code == 0, result.stdout
    assert "masqué" in result.stdout
    assert "registry:Pull" not in result.stdout


def test_roles_get_reveal_policy(runner, mock_api):
    mock_api.get(f"/v1/iam/roles/{ROLE_ID}").mock(
        return_value=httpx.Response(200, json=_role())
    )
    result = runner.invoke(app, ["iam", "roles", "get", ROLE_ID, "--reveal-policy"])
    assert result.exit_code == 0, result.stdout
    assert "registry:Pull" in result.stdout


def test_roles_get_404_french(runner, mock_api):
    mock_api.get(f"/v1/iam/roles/{ROLE_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(app, ["iam", "roles", "get", ROLE_ID])
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


# ---------------------------------------------------------------------------
# Roles create / update / delete
# ---------------------------------------------------------------------------


def test_roles_create_happy_path(runner, mock_api, tmp_path):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_role())

    mock_api.post("/v1/iam/roles").mock(side_effect=_capture)
    policy = _write_policy(tmp_path)
    result = runner.invoke(
        app,
        ["iam", "roles", "create",
         "--name", "RegistryReader",
         "--policy-file", str(policy),
         "--description", "Read-only"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["name"] == "RegistryReader"
    assert captured["body"]["description"] == "Read-only"
    assert captured["body"]["policy_document"]["statements"][0]["effect"] == "Allow"


def test_roles_create_policy_file_missing(runner, mock_api, tmp_path):
    result = runner.invoke(
        app,
        ["iam", "roles", "create",
         "--name", "X",
         "--policy-file", str(tmp_path / "does-not-exist.json")],
    )
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


def test_roles_create_policy_invalid_json(runner, mock_api, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{", encoding="utf-8")
    result = runner.invoke(
        app,
        ["iam", "roles", "create", "--name", "X", "--policy-file", str(bad)],
    )
    assert result.exit_code == 1
    assert "JSON invalide" in result.stdout


def test_roles_create_policy_missing_statements(runner, mock_api, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": "2026-05-10"}), encoding="utf-8")
    result = runner.invoke(
        app,
        ["iam", "roles", "create", "--name", "X", "--policy-file", str(bad)],
    )
    assert result.exit_code == 1
    assert "statements" in result.stdout.lower()


def test_roles_create_policy_bad_arn(runner, mock_api, tmp_path):
    bad_doc = {
        "statements": [{
            "effect": "Allow",
            "actions": ["registry:Pull"],
            "resources": ["arn:aws:registry:rnn:UUID:foo"],  # 'aws' au lieu de 'ccp'
        }]
    }
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bad_doc), encoding="utf-8")
    result = runner.invoke(
        app,
        ["iam", "roles", "create", "--name", "X", "--policy-file", str(bad)],
    )
    assert result.exit_code == 1
    assert "ARN invalide" in result.stdout


def test_roles_update_no_changes_aborts(runner, mock_api):
    result = runner.invoke(app, ["iam", "roles", "update", ROLE_ID])
    assert result.exit_code == 1
    assert "modification" in result.stdout.lower()


def test_roles_update_description_only(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_role())

    mock_api.patch(f"/v1/iam/roles/{ROLE_ID}").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["iam", "roles", "update", ROLE_ID, "--description", "Updated desc"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"description": "Updated desc"}


def test_roles_update_with_policy(runner, mock_api, tmp_path):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_role())

    mock_api.patch(f"/v1/iam/roles/{ROLE_ID}").mock(side_effect=_capture)
    policy = _write_policy(tmp_path)
    result = runner.invoke(
        app,
        ["iam", "roles", "update", ROLE_ID, "--policy-file", str(policy)],
    )
    assert result.exit_code == 0, result.stdout
    assert "policy_document" in captured["body"]


def test_roles_delete_with_yes(runner, mock_api):
    mock_api.delete(f"/v1/iam/roles/{ROLE_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["iam", "roles", "delete", ROLE_ID, "--yes"])
    assert result.exit_code == 0
    assert "supprimé" in result.stdout


def test_roles_delete_409_conflict(runner, mock_api):
    mock_api.delete(f"/v1/iam/roles/{ROLE_ID}").mock(
        return_value=httpx.Response(409, json={"detail": "role has assignments"})
    )
    result = runner.invoke(app, ["iam", "roles", "delete", ROLE_ID, "--yes"])
    assert result.exit_code == 1
    assert "Conflit" in result.stdout


def test_roles_delete_built_in_403(runner, mock_api):
    mock_api.delete(f"/v1/iam/roles/{BUILTIN_ID}").mock(
        return_value=httpx.Response(403, json={"detail": "built-in role"})
    )
    result = runner.invoke(app, ["iam", "roles", "delete", BUILTIN_ID, "--yes"])
    assert result.exit_code == 1
    assert "refusé" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Built-ins list
# ---------------------------------------------------------------------------


def test_builtins_list(runner, mock_api):
    mock_api.get("/v1/iam/built-in-roles").mock(
        return_value=httpx.Response(200, json=[
            _role(BUILTIN_ID, "AdminAll", built_in=True),
            _role(ROLE2_ID, "ReadOnlyAll", built_in=True),
        ])
    )
    result = runner.invoke(app, ["iam", "built-ins", "list"])
    assert result.exit_code == 0, result.stdout
    assert "AdminAll" in result.stdout
    assert "ReadOnlyAll" in result.stdout


# ---------------------------------------------------------------------------
# Roles attach / detach
# ---------------------------------------------------------------------------


def test_roles_attach_by_sa_name(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={
            "id": ASSIGN_ID,
            "role_id": ROLE_ID,
            "tenant_id": TENANT_ID,
            "org_id": ORG_ID,
            "principal_type": "service_account",
            "principal_id": SA_ID,
            "created_at": "2026-05-10T10:00:00Z",
        })

    mock_api.get("/v1/iam/roles").mock(
        return_value=httpx.Response(200, json=[_role()])
    )
    mock_api.get("/v1/service-accounts").mock(
        return_value=httpx.Response(200, json=[_sa()])
    )
    mock_api.post(f"/v1/iam/roles/{ROLE_ID}/assignments").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["iam", "roles", "attach", "RegistryReader",
         "--principal-type", "service_account",
         "--principal-id", "ci-pipeline"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["principal_type"] == "service_account"
    assert captured["body"]["principal_id"] == SA_ID
    assert "service_account" in result.stdout


def test_roles_attach_invalid_principal_type(runner, mock_api):
    result = runner.invoke(
        app,
        ["iam", "roles", "attach", ROLE_ID,
         "--principal-type", "user",  # invalide
         "--principal-id", "x"],
    )
    assert result.exit_code == 1
    assert "principal-type" in result.stdout.lower()


def test_roles_attach_with_expires_at(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={
            "id": ASSIGN_ID, "role_id": ROLE_ID, "tenant_id": TENANT_ID,
            "org_id": ORG_ID, "principal_type": "api_key",
            "principal_id": SA_ID, "expires_at": "2027-01-01T00:00:00Z",
            "created_at": "2026-05-10T10:00:00Z",
        })

    mock_api.post(f"/v1/iam/roles/{ROLE_ID}/assignments").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["iam", "roles", "attach", ROLE_ID,
         "--principal-type", "api_key",
         "--principal-id", SA_ID,
         "--expires-at", "2027-01-01T00:00:00Z"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["expires_at"] == "2027-01-01T00:00:00Z"


def test_roles_detach_found(runner, mock_api):
    mock_api.get("/v1/iam/roles").mock(
        return_value=httpx.Response(200, json=[_role()])
    )
    mock_api.get(f"/v1/iam/roles/{ROLE_ID}/assignments").mock(
        return_value=httpx.Response(200, json=[{
            "id": ASSIGN_ID, "role_id": ROLE_ID, "tenant_id": TENANT_ID,
            "org_id": ORG_ID, "principal_type": "service_account",
            "principal_id": SA_ID,
            "created_at": "2026-05-10T10:00:00Z",
        }])
    )
    mock_api.delete(f"/v1/iam/roles/{ROLE_ID}/assignments/{ASSIGN_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["iam", "roles", "detach", ASSIGN_ID, "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "Assignment supprimé" in result.stdout


def test_roles_detach_not_found(runner, mock_api):
    mock_api.get("/v1/iam/roles").mock(
        return_value=httpx.Response(200, json=[_role()])
    )
    mock_api.get(f"/v1/iam/roles/{ROLE_ID}/assignments").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = runner.invoke(app, ["iam", "roles", "detach", ASSIGN_ID, "--yes"])
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


# ---------------------------------------------------------------------------
# who-am-i + simulate
# ---------------------------------------------------------------------------


def test_who_am_i_basic(runner, mock_api):
    mock_api.get("/v1/tenants/me").mock(
        return_value=httpx.Response(200, json=_me())
    )
    result = runner.invoke(app, ["iam", "who-am-i"])
    assert result.exit_code == 0, result.stdout
    assert "user@cetic-group.com" in result.stdout


def test_who_am_i_with_effective_permissions(runner, mock_api):
    mock_api.get("/v1/tenants/me").mock(
        return_value=httpx.Response(200, json=_me())
    )
    mock_api.get("/v1/members").mock(
        return_value=httpx.Response(200, json=[_member()])
    )
    mock_api.get(
        f"/v1/iam/principals/org_member/{MEMBER_ID}/effective-permissions"
    ).mock(
        return_value=httpx.Response(200, json=[
            {
                "role_id": ROLE_ID,
                "role_name": "RegistryReader",
                "is_built_in": False,
                "statement_sid": "AllowReadOnly",
                "effect": "Allow",
                "actions": ["registry:Pull"],
                "resources": ["*"],
                "condition": None,
                "expires_at": None,
            }
        ])
    )
    result = runner.invoke(app, ["iam", "who-am-i", "--effective-permissions"])
    assert result.exit_code == 0, result.stdout
    assert "RegistryReader" in result.stdout


def test_simulate_allow_decision(runner, mock_api):
    mock_api.get("/v1/tenants/me").mock(
        return_value=httpx.Response(200, json=_me())
    )
    mock_api.get("/v1/members").mock(
        return_value=httpx.Response(200, json=[_member()])
    )
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "decision": {
                "allow": True,
                "reason": "Allow",
                "matched_statement_sid": "AllowReadOnly",
                "matched_role_id": ROLE_ID,
            },
            "matched_statements": [],
        })

    mock_api.post("/v1/iam/simulate").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["iam", "simulate",
         "--action", "registry:Pull",
         "--resource", f"arn:ccp:registry:rnn:{TENANT_ID}:registry/foo"],
    )
    assert result.exit_code == 0, result.stdout
    assert "ALLOW" in result.stdout
    assert captured["body"]["action"] == "registry:Pull"


def test_simulate_invalid_arn_rejected_locally(runner, mock_api):
    """L'ARN est validé côté CLI avant POST."""
    result = runner.invoke(
        app,
        ["iam", "simulate",
         "--action", "registry:Pull",
         "--resource", "arn:aws:registry:rnn:UUID:foo"],  # aws au lieu de ccp
    )
    assert result.exit_code == 1
    assert "ARN invalide" in result.stdout


def test_simulate_explicit_principal(runner, mock_api):
    mock_api.get("/v1/tenants/me").mock(
        return_value=httpx.Response(200, json=_me())
    )
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "decision": {"allow": False, "reason": "ImplicitDeny"},
            "matched_statements": [],
        })

    mock_api.post("/v1/iam/simulate").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["iam", "simulate",
         "--action", "bucket:GetObject",
         "--resource", "*",
         "--principal-type", "service_account",
         "--principal-id", SA_ID],
    )
    assert result.exit_code == 0, result.stdout
    assert "DENY" in result.stdout
    assert "ImplicitDeny" in result.stdout
    assert captured["body"]["principal"]["type"] == "service_account"
    assert captured["body"]["principal"]["id"] == SA_ID


# ---------------------------------------------------------------------------
# Wire-up & branding guards
# ---------------------------------------------------------------------------


def test_iam_app_registered_in_main():
    from cetic.main import app as main_app
    group_names = [g.name for g in main_app.registered_groups if g.typer_instance is not None]
    assert "iam" in group_names, \
        "iam_app non enregistré dans main.app"


def test_iam_subcommands_count():
    from cetic.commands import iam

    top_level = [c.name for c in iam.app.registered_commands]
    # who-am-i, simulate
    assert len(top_level) == 2
    role_cmds = [c.name for c in iam.roles_app.registered_commands]
    # list, get, create, update, delete, attach, detach
    assert len(role_cmds) == 7
    bi_cmds = [c.name for c in iam.builtins_app.registered_commands]
    # list
    assert len(bi_cmds) == 1


def test_no_legacy_brand_in_iam_files():
    """Garde-fou : aucune référence à cloud-lake/cloudlake."""
    import pathlib

    root = pathlib.Path(__file__).parent.parent
    files = [
        root / "cetic" / "commands" / "iam.py",
        root / "cetic" / "commands" / "service_account.py",
        root / "cetic" / "_iam_arn.py",
        root / "cetic" / "_load.py",
        root / "cetic" / "_format.py",
    ]
    forbidden = ("cloud-lake", "cloudlake", "cloud_lake")
    for f in files:
        text = f.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{f} contient interdit : {token}"


def test_iam_arn_parse_strict():
    from cetic._iam_arn import match_arn, parse_arn

    parsed = parse_arn(f"arn:ccp:registry:rnn:{TENANT_ID}:registry/foo")
    assert parsed.service == "registry"
    assert parsed.region == "rnn"
    assert match_arn("*", f"arn:ccp:registry:rnn:{TENANT_ID}:registry/foo")
    assert not match_arn(
        f"arn:ccp:bucket:rnn:{TENANT_ID}:*",
        f"arn:ccp:registry:rnn:{TENANT_ID}:registry/foo",
    )


def test_format_decision_colors():
    from cetic._format import _format_decision

    allow = _format_decision({"allow": True, "reason": "Allow"})
    assert "green" in allow.lower() or "ALLOW" in allow
    deny = _format_decision({"allow": False, "reason": "ExplicitDeny"})
    assert "red" in deny.lower() or "DENY" in deny
    implicit = _format_decision({"allow": False, "reason": "ImplicitDeny"})
    assert "ImplicitDeny" in implicit
