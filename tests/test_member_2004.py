"""`member invite` et `member set-role`."""
from __future__ import annotations

import json

import httpx

from cetic.main import app

M = "77777777-7777-7777-7777-777777777777"


def test_invite_lit_les_memberships(runner, mock_api):
    mock_api.post("/v1/members").mock(return_value=httpx.Response(201, json={
        "email": "a@b.fr", "memberships": [{"id": M, "email": "a@b.fr", "role": "member"}]}))
    result = runner.invoke(app, ["member", "invite", "--email", "a@b.fr"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert M in result.stdout


def test_set_role_par_patch_et_erreur_fidele(runner, mock_api):
    route = mock_api.patch(f"/v1/members/{M}").mock(return_value=httpx.Response(200, json={"id": M}))
    post = mock_api.post(f"/v1/members/{M}").mock(return_value=httpx.Response(405))
    result = runner.invoke(app, ["member", "set-role", M, "--role", "admin"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(route.calls.last.request.content) == {"role": "admin"}
    assert not post.called

    mock_api.patch(f"/v1/members/{M}").mock(return_value=httpx.Response(
        403, json={"detail": "Rôle owner non attribuable"}))
    result = runner.invoke(app, ["member", "set-role", M, "--role", "owner"])
    assert result.exit_code == 1
    assert "owner non attribuable" in result.stdout + result.stderr
