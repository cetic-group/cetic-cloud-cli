"""Clés S3 par bucket.

`POST /v1/object-storage/keys` répond 410 depuis les clés par bucket : la
création passe par `POST /v1/buckets/{id}/keys`. Les anciennes clés de
l'organisation restent listables et révocables sans `--bucket`.
"""
from __future__ import annotations

import json

import httpx

from cetic.main import app

B = "44444444-4444-4444-4444-444444444444"
K = "55555555-5555-5555-5555-555555555555"


def test_create_par_bucket(runner, mock_api):
    route = mock_api.post(f"/v1/buckets/{B}/keys").mock(return_value=httpx.Response(201, json={
        "id": K, "access_key": "AK", "secret_key": "SK", "endpoint_url": "https://s3",
        "s3_bucket_name": "cl-b-1"}))
    ancien = mock_api.post("/v1/object-storage/keys").mock(return_value=httpx.Response(410))
    result = runner.invoke(app, ["bucket", "key", "create", "--bucket", B, "--label", "ci",
                                 "--access", "read", "--expires-days", "30"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(route.calls.last.request.content) == {
        "label": "ci", "access_level": "read", "expires_in_days": 30}
    assert not ancien.called
    assert "cl-b-1" in result.stdout


def test_list_et_revoke_par_bucket(runner, mock_api):
    mock_api.get(f"/v1/buckets/{B}/keys").mock(return_value=httpx.Response(200, json=[
        {"id": K, "label": "ci", "region": "RNN", "access_level": "read",
         "access_key_prefix": "AKXX", "expires_at": None}]))
    rev = mock_api.delete(f"/v1/buckets/{B}/keys/{K}").mock(return_value=httpx.Response(204))
    result = runner.invoke(app, ["bucket", "key", "list", "--bucket", B], env={"CCP_OUTPUT": "json"})
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)[0]["id"] == K
    result = runner.invoke(app, ["bucket", "key", "revoke", K, "--bucket", B, "--yes"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert rev.called


def test_anciennes_cles_sans_bucket(runner, mock_api):
    mock_api.get("/v1/object-storage/keys").mock(return_value=httpx.Response(200, json=[]))
    rev = mock_api.delete(f"/v1/object-storage/keys/{K}").mock(return_value=httpx.Response(204))
    assert runner.invoke(app, ["bucket", "key", "list"]).exit_code == 0
    assert runner.invoke(app, ["bucket", "key", "revoke", K, "--yes"]).exit_code == 0
    assert rev.called



def test_list_refuse_bucket_et_region_ensemble(runner, mock_api):
    result = runner.invoke(app, ["bucket", "key", "list", "--bucket", B, "--region", "RNN"])
    assert result.exit_code == 1
    assert not mock_api.calls
