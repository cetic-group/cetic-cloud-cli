"""Target groups de type bucket et contrôle DNS d'un listener."""

from __future__ import annotations

import json

import httpx
import pytest

from cetic.main import app
from tests.test_appgw import GW_ID, LISTENER_ID, TG_ID

BUCKET_ID = "bbbbbbbb-1111-2222-3333-444444444444"
TG_URL = f"/v1/app-gateways/{GW_ID}/target-groups"
BASE = ["appgw", "tg"]


def _create(runner, *extra):
    return runner.invoke(
        app, [*BASE, "create", GW_ID, "--name", "site", *extra]
    )


def test_create_bucket_sends_kind_bucket_and_canonical_prefix(runner, mock_api):
    post = mock_api.post(TG_URL).mock(return_value=httpx.Response(201, json={"id": TG_ID, "name": "site"}))
    result = _create(runner, "--type", "bucket", "--bucket", BUCKET_ID, "--prefix", "/site//v1/")
    assert result.exit_code == 0, result.output
    assert json.loads(post.calls.last.request.content) == {
        "name": "site", "kind": "bucket", "bucket_id": BUCKET_ID, "bucket_prefix": "site/v1",
    }


def test_create_bucket_without_prefix_omits_it(runner, mock_api):
    post = mock_api.post(TG_URL).mock(return_value=httpx.Response(201, json={"id": TG_ID}))
    assert _create(runner, "--type", "bucket", "--bucket", BUCKET_ID).exit_code == 0
    assert json.loads(post.calls.last.request.content) == {
        "name": "site", "kind": "bucket", "bucket_id": BUCKET_ID,
    }


def test_create_bucket_by_name_resolves_id(runner, mock_api):
    mock_api.get("/v1/buckets").mock(return_value=httpx.Response(200, json=[{"id": BUCKET_ID, "name": "mon-bucket"}]))
    post = mock_api.post(TG_URL).mock(return_value=httpx.Response(201, json={"id": TG_ID}))
    assert _create(runner, "--type", "bucket", "--bucket", "mon-bucket").exit_code == 0
    assert json.loads(post.calls.last.request.content)["bucket_id"] == BUCKET_ID


def test_create_upstream_body_unchanged(runner, mock_api):
    post = mock_api.post(TG_URL).mock(return_value=httpx.Response(201, json={"id": TG_ID}))
    assert _create(runner).exit_code == 0
    assert json.loads(post.calls.last.request.content) == {"name": "site", "algorithm": "roundrobin"}


@pytest.mark.parametrize(
    "args, fragment",
    [
        (["--type", "bucket"], "--bucket est obligatoire"),
        (["--type", "bucket", "--bucket", BUCKET_ID, "--algorithm", "leastconn"], "--algorithm ne s'applique pas"),
        (["--bucket", BUCKET_ID], "qu'avec --type bucket"),
        (["--prefix", "x"], "qu'avec --type bucket"),
        (["--type", "bucket", "--bucket", BUCKET_ID, "--prefix", "a/../b"], "segment"),
        (["--type", "bucket", "--bucket", BUCKET_ID, "--prefix", "a b"], "lettres, chiffres"),
        (["--type", "bucket", "--bucket", BUCKET_ID, "--prefix", "a" * 513], "trop long"),
    ],
)
def test_create_validation_refused_without_api_call(runner, mock_api, args, fragment):
    result = _create(runner, *args)
    assert result.exit_code == 1, result.output
    assert fragment in result.output.replace("\n", " ")
    assert not mock_api.calls


def test_private_bucket_refusal_is_explicit(runner, mock_api):
    mock_api.post(TG_URL).mock(return_value=httpx.Response(
        422, json={"detail": "Ce bucket est privé. Pour l'instant, seuls les buckets publics..."}))
    result = _create(runner, "--type", "bucket", "--bucket", BUCKET_ID)
    assert result.exit_code == 1
    out = result.output.replace("\n", " ")
    assert "ce bucket est privé" in out and "--public" in out


def test_update_prefix_sends_only_prefix(runner, mock_api):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=[BUCKET_TG]))
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(return_value=httpx.Response(200, json={"id": TG_ID}))
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--prefix", "/docs/"])
    assert result.exit_code == 0, result.output
    body = json.loads(patch.calls.last.request.content)
    assert body == {"bucket_prefix": "docs"}
    assert "kind" not in body and "bucket_id" not in body


def test_update_empty_prefix_means_whole_bucket(runner, mock_api):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=[BUCKET_TG]))
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(return_value=httpx.Response(200, json={"id": TG_ID}))
    assert runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--prefix", ""]).exit_code == 0
    assert json.loads(patch.calls.last.request.content) == {"bucket_prefix": ""}


BUCKET_TG = {"id": TG_ID, "name": "site", "kind": "bucket", "bucket_id": BUCKET_ID}
SERVER_TG = {"id": TG_ID, "name": "api", "algorithm": "roundrobin", "members": []}


def test_update_refuses_type_change(runner, mock_api):
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--type", "upstream"])
    assert result.exit_code == 1
    assert "type d'un target group ne peut pas être modifié" in result.output.replace("\n", " ")
    assert not mock_api.calls


def test_update_bucket_sends_bucket_id_never_kind(runner, mock_api):
    other = "dddddddd-1111-2222-3333-444444444444"
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=[BUCKET_TG]))
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(return_value=httpx.Response(200, json={"id": TG_ID}))
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--bucket", other])
    assert result.exit_code == 0, result.output
    assert json.loads(patch.calls.last.request.content) == {"bucket_id": other}


def test_update_bucket_by_name_and_prefix_together(runner, mock_api):
    mock_api.get("/v1/buckets").mock(return_value=httpx.Response(200, json=[{"id": BUCKET_ID, "name": "mon-bucket"}]))
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=[BUCKET_TG]))
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(return_value=httpx.Response(200, json={"id": TG_ID}))
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID,
                                 "--bucket", "mon-bucket", "--prefix", "a/b"])
    assert result.exit_code == 0, result.output
    assert json.loads(patch.calls.last.request.content) == {"bucket_id": BUCKET_ID, "bucket_prefix": "a/b"}


@pytest.mark.parametrize("args", [["--prefix", "x"], ["--bucket", BUCKET_ID]])
def test_update_server_target_group_refuses_bucket_options(runner, mock_api, args):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=[SERVER_TG]))
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(return_value=httpx.Response(200, json={}))
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, *args])
    assert result.exit_code == 1
    assert "ne s'appliquent qu'à un target group de type" in " ".join(result.output.split())
    assert not patch.called


def test_update_private_bucket_refusal_is_explicit(runner, mock_api):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=[BUCKET_TG]))
    mock_api.patch(f"{TG_URL}/{TG_ID}").mock(return_value=httpx.Response(
        422, json={"detail": "Ce bucket est privé. Pour l'instant..."}))
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--bucket", BUCKET_ID])
    assert result.exit_code == 1
    out = result.output.replace("\n", " ")
    assert "ce bucket est privé" in out and "--public" in out


def test_update_server_target_group_body_unchanged(runner, mock_api):
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(return_value=httpx.Response(200, json={"id": TG_ID}))
    assert runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--name", "x", "--algorithm", "source"]).exit_code == 0
    assert json.loads(patch.calls.last.request.content) == {"name": "x", "algorithm": "source"}


GROUPS = [
    {"id": TG_ID, "name": "api-pool", "algorithm": "leastconn", "hc_path": "/h", "members": [{}, {}]},
    {"id": "cccccccc-1111-2222-3333-444444444444", "name": "site", "kind": "bucket",
     "bucket_id": BUCKET_ID, "bucket_prefix": "site/v1", "origin_status": "error",
     "origin_error": "bucket redevenu privé", "algorithm": "roundrobin", "members": []},
]


def test_list_table_shows_bucket_columns_and_keeps_servers(runner, mock_api):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=GROUPS))
    result = runner.invoke(app, [*BASE, "list", GW_ID])
    assert result.exit_code == 0, result.output
    out = result.output
    for text in ("Type", "Bucket", "Dossier", "État de l'origine", "api-pool", "leastconn",
                 "upstream", BUCKET_ID, "site/v1", "bucket redevenu privé"):
        assert text in out, text


def test_list_json_and_yaml(runner, mock_api, monkeypatch):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=GROUPS))
    monkeypatch.setenv("CCP_OUTPUT", "json")
    rows = json.loads(runner.invoke(app, [*BASE, "list", GW_ID]).output)
    assert rows[0]["type"] == "upstream" and rows[0]["members"] == "2"
    assert rows[1]["bucket"] == BUCKET_ID and rows[1]["folder"] == "site/v1"
    monkeypatch.setenv("CCP_OUTPUT", "yaml")
    result = runner.invoke(app, [*BASE, "list", GW_ID])
    assert result.exit_code == 0 and "site/v1" in result.output


def test_get_bucket_target_group_alias_and_raw_json(runner, mock_api, monkeypatch):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=GROUPS))
    result = runner.invoke(app, ["appgw", "target-group", "get", GW_ID, "--tg-id", "cccccccc"])
    assert result.exit_code == 0, result.output
    assert "site/v1" in result.output and "bucket redevenu privé" in result.output
    monkeypatch.setenv("CCP_OUTPUT", "json")
    raw = json.loads(runner.invoke(app, [*BASE, "get", GW_ID, "--tg-id", "cccccccc"]).output)
    assert raw == GROUPS[1]


def test_get_unknown_target_group(runner, mock_api):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=GROUPS))
    result = runner.invoke(app, [*BASE, "get", GW_ID, "--tg-id", "zzz"])
    assert result.exit_code == 1


DNS_URL = f"/v1/app-gateways/{GW_ID}/listeners/{LISTENER_ID}/dns-check"


@pytest.mark.parametrize("state, fragment", [
    ("ok", "OK"), ("mismatch", "Incorrect"), ("missing", "Absent"),
    ("unknown", "Inconnu"), ("no_ip", "pas encore d'adresse"), ("unsupported", "non disponible"),
])
def test_dns_check_states(runner, mock_api, state, fragment):
    mock_api.get(DNS_URL).mock(return_value=httpx.Response(200, json={
        "hostname": "www.example.com", "state": state, "record_type": "A",
        "expected_ip": "203.0.113.10", "resolved": ["203.0.113.99"]}))
    result = runner.invoke(app, ["appgw", "listener", "dns-check", GW_ID, LISTENER_ID])
    assert result.exit_code == 0, result.output
    out = result.output.replace("\n", " ")
    assert "www.example.com" in out and "203.0.113.10" in out and fragment in out


def test_dns_check_by_hostname_and_json(runner, mock_api, monkeypatch):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/listeners").mock(
        return_value=httpx.Response(200, json=[{"id": LISTENER_ID, "hostname": "www.example.com"}]))
    payload = {"hostname": "www.example.com", "state": "ok", "record_type": "A",
               "expected_ip": "203.0.113.10", "resolved": ["203.0.113.10"]}
    mock_api.get(DNS_URL).mock(return_value=httpx.Response(200, json=payload))
    monkeypatch.setenv("CCP_OUTPUT", "json")
    result = runner.invoke(app, ["appgw", "listener", "dns-check", GW_ID, "www.example.com"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload
