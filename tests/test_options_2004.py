"""cetic-cloud-platform#2004 — options qui étaient sans effet."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cetic.main import app


def test_secret_tags_et_ancien_label(runner, mock_api):
    route = mock_api.post("/v1/secrets").mock(return_value=httpx.Response(201, json={
        "id": "s1", "name": "db", "version": 1}))
    result = runner.invoke(app, ["secret", "create", "db", "--data", "p=x",
                                 "--tag", "env:prod", "--label", "app=web"])
    assert result.exit_code == 0, result.stdout + result.stderr
    body = json.loads(route.calls.last.request.content)
    assert body["tags"] == ["env:prod", "app:web"]
    assert "labels" not in body


def test_secret_list_affiche_les_tags(runner, mock_api):
    mock_api.get("/v1/secrets").mock(return_value=httpx.Response(200, json=[{
        "id": "s1", "name": "db", "version": 1, "tags": ["env:prod"],
        "created_at": "2026-11-21T10:00:00Z", "updated_at": "2026-11-21T10:00:00Z"}]))
    result = runner.invoke(app, ["secret", "list"], env={"CCP_OUTPUT": "json"})
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "env:prod" in result.stdout


@pytest.mark.parametrize(("cmd", "path"), [
    (["ip", "list"], "/v1/public-ips"),
    (["lb", "list"], "/v1/load-balancers"),
    (["appgw", "list"], "/v1/app-gateways"),
    (["k8s", "list"], "/v1/k8s/clusters"),
])
def test_region_filtree_cote_cli(runner, mock_api, cmd, path):
    base = {"status": "active", "plan": "small", "k8s_version": "1.34.8"}
    mock_api.get(path).mock(return_value=httpx.Response(200, json=[
        {**base, "id": "a", "name": "rnn-1", "region": "RNN"},
        {**base, "id": "b", "name": "par-1", "region": "PAR"},
    ]))
    result = runner.invoke(app, [*cmd, "--region", "rnn"], env={"CCP_OUTPUT": "json"})
    assert result.exit_code == 0, result.stdout + result.stderr
    ids = [r["id"] for r in json.loads(result.stdout)]
    assert ids == ["a"]


def test_quota_requests_status_filtre(runner, mock_api):
    mock_api.get("/v1/quotas/requests").mock(return_value=httpx.Response(200, json=[
        {"id": "r1", "field": "max_vms", "requested_value": 10, "status": "pending"},
        {"id": "r2", "field": "max_vms", "requested_value": 20, "status": "approved"},
    ]))
    result = runner.invoke(app, ["quota", "requests", "--status", "approved"],
                           env={"CCP_OUTPUT": "json"})
    assert result.exit_code == 0, result.stdout + result.stderr
    assert [r["id"] for r in json.loads(result.stdout)] == ["r2"]


def test_service_account_expires_at_converti_en_jours(runner, mock_api):
    route = mock_api.post("/v1/service-accounts").mock(return_value=httpx.Response(201, json={
        "id": "sa1", "name": "ci", "token": "ccp_sa_x"}))
    date = (datetime.now(UTC).date() + timedelta(days=30)).isoformat()
    result = runner.invoke(app, ["service-account", "create", "--name", "ci",
                                 "--expires-at", date], input="n\n")
    assert result.exit_code == 0, result.stdout + result.stderr
    body = json.loads(route.calls.last.request.content)
    assert body == {"name": "ci", "expires_in_days": 30}


def test_service_account_expires_at_passee_refusee(runner, mock_api):
    result = runner.invoke(app, ["service-account", "create", "--name", "ci",
                                 "--expires-at", "2020-01-01"])
    assert result.exit_code == 1


@pytest.mark.parametrize(("cle", "valeur", "avert"), [("lang", "en", "français"),
                                                      ("region", "PAR", "--region")])
def test_config_set_signale_les_reglages_non_appliques(runner, monkeypatch, cle, valeur, avert):
    monkeypatch.setattr("cetic.config.set_value", lambda k, v: None)
    result = runner.invoke(app, ["config", "set", cle, valeur])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert avert in result.stdout
