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
    return runner.invoke(app, [*BASE, "create", GW_ID, "--name", "site", *extra])


def test_create_bucket_sends_kind_bucket_and_canonical_prefix(runner, mock_api):
    post = mock_api.post(TG_URL).mock(
        return_value=httpx.Response(201, json={"id": TG_ID, "name": "site"})
    )
    result = _create(runner, "--type", "bucket", "--bucket", BUCKET_ID, "--prefix", "/site//v1/")
    assert result.exit_code == 0, result.output
    assert json.loads(post.calls.last.request.content) == {
        "name": "site",
        "kind": "bucket",
        "bucket_id": BUCKET_ID,
        "bucket_prefix": "site/v1",
    }


def test_create_bucket_without_prefix_omits_it(runner, mock_api):
    post = mock_api.post(TG_URL).mock(return_value=httpx.Response(201, json={"id": TG_ID}))
    assert _create(runner, "--type", "bucket", "--bucket", BUCKET_ID).exit_code == 0
    assert json.loads(post.calls.last.request.content) == {
        "name": "site",
        "kind": "bucket",
        "bucket_id": BUCKET_ID,
    }


def test_create_bucket_by_name_resolves_id(runner, mock_api):
    mock_api.get("/v1/buckets").mock(
        return_value=httpx.Response(200, json=[{"id": BUCKET_ID, "name": "mon-bucket"}])
    )
    post = mock_api.post(TG_URL).mock(return_value=httpx.Response(201, json={"id": TG_ID}))
    assert _create(runner, "--type", "bucket", "--bucket", "mon-bucket").exit_code == 0
    assert json.loads(post.calls.last.request.content)["bucket_id"] == BUCKET_ID


def test_create_upstream_body_unchanged(runner, mock_api):
    post = mock_api.post(TG_URL).mock(return_value=httpx.Response(201, json={"id": TG_ID}))
    assert _create(runner).exit_code == 0
    assert json.loads(post.calls.last.request.content) == {
        "name": "site",
        "algorithm": "roundrobin",
    }


@pytest.mark.parametrize(
    "args, fragment",
    [
        (["--type", "bucket"], "--bucket est obligatoire"),
        (
            ["--type", "bucket", "--bucket", BUCKET_ID, "--algorithm", "leastconn"],
            "--algorithm ne s'applique pas",
        ),
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
    mock_api.post(TG_URL).mock(
        return_value=httpx.Response(
            422,
            json={"detail": "Ce bucket est privé. Pour l'instant, seuls les buckets publics..."},
        )
    )
    result = _create(runner, "--type", "bucket", "--bucket", BUCKET_ID)
    assert result.exit_code == 1
    out = result.output.replace("\n", " ")
    assert "ce bucket est privé" in out and "--public" in out


def test_update_prefix_sends_only_prefix(runner, mock_api):
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(
        return_value=httpx.Response(200, json={"id": TG_ID})
    )
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--prefix", "/docs/"])
    assert result.exit_code == 0, result.output
    body = json.loads(patch.calls.last.request.content)
    assert body == {"bucket_prefix": "docs"}
    assert "kind" not in body and "bucket_id" not in body


def test_update_empty_prefix_means_whole_bucket(runner, mock_api):
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(
        return_value=httpx.Response(200, json={"id": TG_ID})
    )
    assert (
        runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--prefix", ""]).exit_code
        == 0
    )
    assert json.loads(patch.calls.last.request.content) == {"bucket_prefix": ""}


def test_update_refuses_type_change(runner, mock_api):
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--type", "upstream"])
    assert result.exit_code == 1
    assert "type d'un target group ne peut pas être modifié" in result.output.replace("\n", " ")
    assert not mock_api.calls


def test_update_bucket_sends_bucket_id_never_kind(runner, mock_api):
    other = "dddddddd-1111-2222-3333-444444444444"
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(
        return_value=httpx.Response(200, json={"id": TG_ID})
    )
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--bucket", other])
    assert result.exit_code == 0, result.output
    assert json.loads(patch.calls.last.request.content) == {"bucket_id": other}


def test_update_bucket_by_name_and_prefix_together(runner, mock_api):
    mock_api.get("/v1/buckets").mock(
        return_value=httpx.Response(200, json=[{"id": BUCKET_ID, "name": "mon-bucket"}])
    )
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(
        return_value=httpx.Response(200, json={"id": TG_ID})
    )
    result = runner.invoke(
        app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--bucket", "mon-bucket", "--prefix", "a/b"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(patch.calls.last.request.content) == {
        "bucket_id": BUCKET_ID,
        "bucket_prefix": "a/b",
    }


NOT_BUCKET_422 = "Le bucket et le préfixe ne s'appliquent qu'à un groupe de cibles de type bucket."
PRIVATE_422 = (
    "Ce bucket est privé. Pour l'instant, seuls les buckets publics peuvent être "
    "servis par la passerelle : rendez-le public ou choisissez un autre bucket."
)
DRIFT_409 = (
    "Ce bucket est marqué public, mais le stockage ne le rend pas lisible publiquement. "
    "Réappliquez sa visibilité (public) puis réessayez."
)


@pytest.mark.parametrize("args", [["--prefix", "x"], ["--bucket", BUCKET_ID]])
def test_update_bucket_options_on_server_target_group_explained(runner, mock_api, args):
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(
        return_value=httpx.Response(422, json={"detail": NOT_BUCKET_422})
    )
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, *args])
    assert result.exit_code == 1
    assert "de type serveurs" in " ".join(result.output.split())
    assert [c.request.method for c in mock_api.calls] == ["PATCH"]  # pas de pré-lecture
    assert patch.called


def test_update_private_bucket_refusal_is_explicit(runner, mock_api):
    mock_api.patch(f"{TG_URL}/{TG_ID}").mock(
        return_value=httpx.Response(422, json={"detail": PRIVATE_422})
    )
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--bucket", BUCKET_ID])
    assert result.exit_code == 1
    out = result.output.replace("\n", " ")
    assert "ce bucket est privé" in out and "--public" in out


def test_update_server_target_group_body_unchanged(runner, mock_api):
    patch = mock_api.patch(f"{TG_URL}/{TG_ID}").mock(
        return_value=httpx.Response(200, json={"id": TG_ID})
    )
    assert (
        runner.invoke(
            app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--name", "x", "--algorithm", "source"]
        ).exit_code
        == 0
    )
    assert json.loads(patch.calls.last.request.content) == {"name": "x", "algorithm": "source"}


BUCKET_TG_ID = "cccccccc-1111-2222-3333-444444444444"
DRIFT_TG_ID = "eeeeeeee-1111-2222-3333-444444444444"
SERVER = {
    "id": TG_ID,
    "name": "api-pool",
    "algorithm": "leastconn",
    "hc_path": "/h",
    "members": [{}, {}],
}
# Valeurs réellement écrites par l'API : origin_status = "ok" | "drift" | absent.
BUCKET_OK = {
    "id": BUCKET_TG_ID,
    "name": "site",
    "kind": "bucket",
    "bucket_id": BUCKET_ID,
    "bucket_prefix": "site/v1",
    "origin_status": "ok",
    "origin_error": None,
}
BUCKET_PENDING = {
    "id": "ffffffff-1111-2222-3333-444444444444",
    "name": "nouveau",
    "kind": "bucket",
    "bucket_id": BUCKET_ID,
    "bucket_prefix": "",
    "origin_status": None,
}
BUCKET_DRIFT = {
    "id": DRIFT_TG_ID,
    "name": "derive",
    "kind": "bucket",
    "bucket_id": BUCKET_ID,
    "bucket_prefix": "",
    "origin_status": "drift",
    "origin_error": DRIFT_409,
}
GROUPS = [SERVER, BUCKET_OK, BUCKET_PENDING, BUCKET_DRIFT]


def _origin(runner, group_id):
    result = runner.invoke(app, [*BASE, "list", GW_ID])
    assert result.exit_code == 0, result.output
    return " ".join(result.output.split())


def test_list_table_shows_bucket_columns_and_keeps_servers(runner, mock_api):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=GROUPS))
    out = _origin(runner, None)
    for text in (
        "Type",
        "Bucket",
        "Dossier",
        "État de l'origine",
        "api-pool",
        "leastconn",
        "upstream",
        BUCKET_ID,
        "site/v1",
    ):
        assert text in out, text


def test_origin_states_use_real_server_values(runner, mock_api):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=GROUPS))
    out = _origin(runner, None)
    assert "OK" in out  # origin_status = ok
    assert "En attente" in out  # origin_status absent
    assert "Dérive" in out  # origin_status = drift
    assert "ne le rend pas lisible publiquement" in out  # vrai message du serveur
    assert "Erreur :" not in out


def test_drift_is_not_shown_as_ok(runner, mock_api):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=[BUCKET_DRIFT]))
    out = _origin(runner, None)
    assert "Dérive" in out and "OK" not in out


def test_list_hides_bucket_columns_without_bucket_target_group(runner, mock_api):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=[SERVER]))
    out = _origin(runner, None)
    assert "Algorithme" in out and "api-pool" in out
    for hidden in ("Dossier", "État de l'origine", "Bucket"):
        assert hidden not in out, hidden


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
    result = runner.invoke(app, ["appgw", "target-group", "get", GW_ID, "--tg-id", DRIFT_TG_ID])
    assert result.exit_code == 0, result.output
    assert "Dérive" in result.output
    monkeypatch.setenv("CCP_OUTPUT", "json")
    raw = json.loads(runner.invoke(app, [*BASE, "get", GW_ID, "--tg-id", BUCKET_TG_ID]).output)
    assert raw == BUCKET_OK


def test_get_server_target_group_without_kind(runner, mock_api):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=[SERVER]))
    result = runner.invoke(app, [*BASE, "get", GW_ID, "--tg-id", TG_ID])
    assert result.exit_code == 0, result.output
    assert "api-pool" in result.output and "Dossier" not in result.output


@pytest.mark.parametrize("value", ["zzz", "", BUCKET_TG_ID[:8]])
def test_get_requires_exact_uuid(runner, mock_api, value):
    mock_api.get(TG_URL).mock(return_value=httpx.Response(200, json=GROUPS))
    result = runner.invoke(app, [*BASE, "get", GW_ID, "--tg-id", value])
    assert result.exit_code == 1
    assert "ambigu" not in result.output and "aucun target group" in result.output


DNS_URL = f"/v1/app-gateways/{GW_ID}/listeners/{LISTENER_ID}/dns-check"


@pytest.mark.parametrize(
    "state, fragment",
    [
        ("ok", "OK"),
        ("mismatch", "Incorrect"),
        ("missing", "Absent"),
        ("unknown", "Inconnu"),
        ("no_ip", "pas encore d'adresse"),
        ("unsupported", "non disponible"),
    ],
)
def test_dns_check_states(runner, mock_api, state, fragment):
    mock_api.get(DNS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "hostname": "www.example.com",
                "state": state,
                "record_type": "A",
                "expected_ip": "203.0.113.10",
                "resolved": ["203.0.113.99"],
            },
        )
    )
    result = runner.invoke(app, ["appgw", "listener", "dns-check", GW_ID, LISTENER_ID])
    assert result.exit_code == 0, result.output
    out = result.output.replace("\n", " ")
    assert "www.example.com" in out and "203.0.113.10" in out and fragment in out


def test_dns_check_by_hostname_and_json(runner, mock_api, monkeypatch):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/listeners").mock(
        return_value=httpx.Response(200, json=[{"id": LISTENER_ID, "hostname": "www.example.com"}])
    )
    payload = {
        "hostname": "www.example.com",
        "state": "ok",
        "record_type": "A",
        "expected_ip": "203.0.113.10",
        "resolved": ["203.0.113.10"],
    }
    mock_api.get(DNS_URL).mock(return_value=httpx.Response(200, json=payload))
    monkeypatch.setenv("CCP_OUTPUT", "json")
    result = runner.invoke(app, ["appgw", "listener", "dns-check", GW_ID, "www.example.com"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


def _dns(runner, ident="www.example.com"):
    return runner.invoke(app, ["appgw", "listener", "dns-check", GW_ID, ident])


def test_dns_check_hostname_is_case_insensitive(runner, mock_api):
    mock_api.get(f"/v1/app-gateways/{GW_ID}/listeners").mock(
        return_value=httpx.Response(200, json=[{"id": LISTENER_ID, "hostname": "WWW.Example.com"}])
    )
    mock_api.get(DNS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "hostname": "www.example.com",
                "state": "ok",
                "record_type": "A",
                "expected_ip": "203.0.113.10",
                "resolved": ["203.0.113.10"],
            },
        )
    )
    assert _dns(runner, "www.EXAMPLE.com").exit_code == 0


def test_dns_check_zero_and_many_matches_have_distinct_messages(runner, mock_api):
    listeners = mock_api.get(f"/v1/app-gateways/{GW_ID}/listeners")
    listeners.mock(return_value=httpx.Response(200, json=[]))
    none = _dns(runner)
    listeners.mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": LISTENER_ID, "hostname": "www.example.com"},
                {"id": "99999999-1111-2222-3333-444444444444", "hostname": "WWW.example.com"},
            ],
        )
    )
    many = _dns(runner)
    assert none.exit_code == many.exit_code == 1
    assert "aucun listener" in " ".join(none.output.split())
    assert "2 listeners correspondent" in " ".join(many.output.split())
    assert "UUID" in many.output


def test_dns_check_rate_limited(runner, mock_api):
    mock_api.get(DNS_URL).mock(
        return_value=httpx.Response(429, json={"detail": "Rate limit exceeded"})
    )
    result = runner.invoke(app, ["appgw", "listener", "dns-check", GW_ID, LISTENER_ID])
    assert result.exit_code == 1
    assert "Trop de requêtes" in " ".join(result.output.split())


def test_dns_check_network_error_is_not_swallowed(runner, mock_api):
    mock_api.get(DNS_URL).mock(side_effect=httpx.ConnectError("réseau coupé"))
    result = runner.invoke(app, ["appgw", "listener", "dns-check", GW_ID, LISTENER_ID])
    assert result.exit_code != 0
    assert isinstance(result.exception, httpx.ConnectError)


def test_create_bucket_drift_conflict_is_reported(runner, mock_api):
    mock_api.post(TG_URL).mock(return_value=httpx.Response(409, json={"detail": DRIFT_409}))
    result = _create(runner, "--type", "bucket", "--bucket", BUCKET_ID)
    assert result.exit_code == 1
    assert "ne le rend pas lisible publiquement" in " ".join(result.output.split())


def test_create_unknown_bucket_is_404(runner, mock_api):
    mock_api.post(TG_URL).mock(
        return_value=httpx.Response(404, json={"detail": "Bucket introuvable."})
    )
    result = _create(runner, "--type", "bucket", "--bucket", BUCKET_ID)
    assert result.exit_code == 1
    assert "introuvable" in result.output.lower()


def test_unrelated_422_keeps_generic_message(runner, mock_api):
    # un 422 qui parle d'un autre bucket « privé » ne doit pas déclencher le conseil --public
    mock_api.post(TG_URL).mock(
        return_value=httpx.Response(422, json={"detail": "Nom de groupe de cibles privé réservé."})
    )
    result = _create(runner, "--type", "bucket", "--bucket", BUCKET_ID)
    assert result.exit_code == 1
    out = " ".join(result.output.split())
    assert "--public" not in out and "Paramètres invalides" in out


def test_update_bucket_422_other_reason_keeps_generic_message(runner, mock_api):
    mock_api.patch(f"{TG_URL}/{TG_ID}").mock(
        return_value=httpx.Response(
            422, json={"detail": "Le bucket doit être dans la même région que la passerelle."}
        )
    )
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--bucket", BUCKET_ID])
    assert result.exit_code == 1
    out = " ".join(result.output.split())
    assert "même région" in out and "--public" not in out


def test_update_unknown_target_group_is_404(runner, mock_api):
    mock_api.patch(f"{TG_URL}/{TG_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "Groupe de cibles introuvable."})
    )
    result = runner.invoke(app, [*BASE, "update", GW_ID, "--tg-id", TG_ID, "--prefix", "x"])
    assert result.exit_code == 1
    assert "introuvable" in result.output.lower()


def test_create_invalid_type_rejected_by_cli(runner, mock_api):
    result = _create(runner, "--type", "serveur")
    assert result.exit_code == 2
    assert not mock_api.calls
