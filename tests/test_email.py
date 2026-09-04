"""Tests de `cetic email` — domaines, adresses, alias, jetons (issue cetic-cloud-cli#45).

Contrat amont : routes `/v1/email/*` de cetic-cloud-platform#932.
Ce qui est vérifié ici et ne se devine pas :
- le mot de passe n'est JAMAIS un argument de ligne de commande ;
- `--forward` / `--to` remplacent la liste, ils ne l'enrichissent pas ;
- le quota par défaut vient de l'API : omis, il n'est pas envoyé ;
- la fiche d'un domaine rend les enregistrements DNS copiables tels quels.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app

DOMAIN_ID = "11111111-1111-1111-1111-111111111111"
ACCOUNT_ID = "22222222-2222-2222-2222-222222222222"
ALIAS_ID = "33333333-3333-3333-3333-333333333333"
TOKEN_ID = "44444444-4444-4444-4444-444444444444"
FQDN = "exemple.com"
ADDRESS = f"contact@{FQDN}"
PASSWORD = "motdepasse-long"


def _domain(*, status: str = "active") -> dict[str, Any]:
    return {
        "id": DOMAIN_ID,
        "name": FQDN,
        "status": status,
        "verified_at": "2026-09-01T10:00:00Z" if status == "active" else None,
        "dkim_generated_at": "2026-09-01T10:00:00Z",
        "externally_managed": False,
        "created_at": "2026-09-01T09:00:00Z",
        "accounts_count": 2,
        "aliases_count": 1,
    }


def _domain_detail(*, status: str = "pending_verification") -> dict[str, Any]:
    d = _domain(status=status)
    d.update(
        {
            "verification": {
                "type": "TXT",
                "name": f"_ccp-verification.{FQDN}",
                "value": "ccp-verify=xyz789",
                "status": "missing",
                "purpose": "Prouve que le domaine vous appartient.",
            },
            "records": [
                {
                    "type": "MX",
                    "name": FQDN,
                    "value": "10 mail.cloud.cetic-group.com.",
                    "hostname": "mail.cloud.cetic-group.com",
                    "priority": 10,
                    "status": "missing",
                    "purpose": "Dirige le courrier entrant vers la plateforme.",
                },
                {
                    "type": "TXT",
                    "name": FQDN,
                    "value": "v=spf1 include:cloud.cetic-group.com -all",
                    "status": "conflict",
                    "exceeds_lookup_limit": True,
                    "purpose": "Autorise la plateforme à émettre pour ce domaine.",
                },
            ],
            "client_config": {
                "incoming": {"protocol": "imap", "hostname": "mail.cloud.cetic-group.com",
                             "port": 993, "security": "tls"},
                "outgoing": {"protocol": "smtp", "hostname": "mail.cloud.cetic-group.com",
                             "port": 465, "security": "tls"},
                "username_hint": "Utilisez l'adresse complète comme nom d'utilisateur.",
            },
        }
    )
    return d


def _account(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": ACCOUNT_ID,
        "address": ADDRESS,
        "quota_bytes": 1073741824,
        "usage_bytes": 52428800,
        "usage_updated_at": "2026-09-04T08:00:00Z",
        "enabled": True,
        "enable_imap": True,
        "enable_pop": False,
        "is_system_managed": False,
        "send_as_any_address": False,
        "send_as_pending": False,
        "forward_enabled": False,
        "forward_destination": [],
        "forward_keep": True,
        "comment": None,
        "displayed_name": None,
        "created_at": "2026-09-01T10:00:00Z",
    }
    base.update(over)
    return base


def _alias(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": ALIAS_ID,
        "address": f"info@{FQDN}",
        "destinations": [ADDRESS],
        "wildcard": False,
        "comment": None,
        "created_at": "2026-09-01T10:00:00Z",
    }
    base.update(over)
    return base


def _mock_domain_list(mock_api) -> None:
    mock_api.get("/v1/email/domains").mock(
        return_value=httpx.Response(200, json=[_domain()])
    )


def _mock_account_list(mock_api, accounts: list[dict[str, Any]] | None = None) -> None:
    mock_api.get("/v1/email/accounts").mock(
        return_value=httpx.Response(200, json=accounts or [_account()])
    )


# ---------------------------------------------------------------------------
# Domaines
# ---------------------------------------------------------------------------


def test_domain_list_columns(runner, mock_api) -> None:
    _mock_domain_list(mock_api)
    result = runner.invoke(app, ["email", "domain", "list"])
    assert result.exit_code == 0, result.output
    assert FQDN in result.output
    assert "Adresses" in result.output


def test_domain_create_sends_only_the_name(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_domain(status="pending_verification"))

    mock_api.post("/v1/email/domains").mock(side_effect=_capture)
    result = runner.invoke(app, ["email", "domain", "create", FQDN])
    assert result.exit_code == 0, result.output
    assert captured == {"name": FQDN}
    # Le nom est réservé, rien n'est routé : la suite doit être dite.
    assert "domain show" in result.output


def test_domain_show_renders_records_copiable_and_client_config(runner, mock_api) -> None:
    _mock_domain_list(mock_api)
    mock_api.get(f"/v1/email/domains/{DOMAIN_ID}").mock(
        return_value=httpx.Response(200, json=_domain_detail())
    )
    result = runner.invoke(app, ["email", "domain", "show", FQDN])
    assert result.exit_code == 0, result.output
    # Le TXT de possession est rendu avec les autres, en tête.
    assert "ccp-verify=xyz789" in result.output
    # La valeur d'un MX est ENTIÈRE (priorité comprise) et la priorité est
    # aussi donnée à part : les interfaces DNS demandent deux champs.
    assert "10 mail.cloud.cetic-group.com." in result.output
    assert "conflict" in result.output
    # Bloc « Configuration client ».
    assert "993" in result.output
    assert "adresse complète" in result.output
    # SPF au-delà des 10 recherches : la valeur est bonne mais inapplicable.
    assert "10 recherches" in result.output


def test_domain_verify_posts_on_the_verify_route(runner, mock_api) -> None:
    _mock_domain_list(mock_api)
    route = mock_api.post(f"/v1/email/domains/{DOMAIN_ID}/verify").mock(
        return_value=httpx.Response(200, json=_domain(status="active"))
    )
    result = runner.invoke(app, ["email", "domain", "verify", FQDN])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "active" in result.output


def test_domain_verify_relays_a_missing_proof(runner, mock_api) -> None:
    _mock_domain_list(mock_api)
    mock_api.post(f"/v1/email/domains/{DOMAIN_ID}/verify").mock(return_value=httpx.Response(
        409, json={"detail": "Le TXT de vérification n'est pas publié."},
    ))
    result = runner.invoke(app, ["email", "domain", "verify", FQDN])
    assert result.exit_code == 1
    assert "n'est pas publié" in result.output


def test_domain_recheck_redisplays_the_records(runner, mock_api) -> None:
    _mock_domain_list(mock_api)
    route = mock_api.post(f"/v1/email/domains/{DOMAIN_ID}/recheck").mock(
        return_value=httpx.Response(200, json=_domain_detail())
    )
    result = runner.invoke(app, ["email", "domain", "recheck", FQDN])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "ccp-verify=xyz789" in result.output


def test_domain_delete_relays_the_refusal_on_children(runner, mock_api) -> None:
    _mock_domain_list(mock_api)
    mock_api.delete(f"/v1/email/domains/{DOMAIN_ID}").mock(return_value=httpx.Response(
        409, json={"detail": "Le domaine porte encore des adresses."},
    ))
    result = runner.invoke(app, ["email", "domain", "delete", FQDN, "--yes"])
    assert result.exit_code == 1
    assert "porte encore des adresses" in result.output


def test_domain_unknown_name_fails_clearly(runner, mock_api) -> None:
    mock_api.get("/v1/email/domains").mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(app, ["email", "domain", "show", "inconnu.com"])
    assert result.exit_code == 1
    assert "Aucun domaine de messagerie" in result.output


# ---------------------------------------------------------------------------
# Adresses
# ---------------------------------------------------------------------------


def test_account_list_columns_show_quota_and_usage(runner, mock_api) -> None:
    _mock_account_list(mock_api)
    result = runner.invoke(app, ["email", "account", "list"])
    assert result.exit_code == 0, result.output
    assert ADDRESS in result.output
    assert "1.0 Go" in result.output
    assert "50.0 Mo" in result.output


def test_account_list_filters_by_domain_id(runner, mock_api) -> None:
    seen: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_account()])

    _mock_domain_list(mock_api)
    mock_api.get("/v1/email/accounts").mock(side_effect=_capture)
    result = runner.invoke(app, ["email", "account", "list", "--domain", FQDN])
    assert result.exit_code == 0, result.output
    # Le nom du domaine est résolu en UUID : l'API ne filtre que par identifiant.
    assert seen["params"] == {"domain_id": DOMAIN_ID}


def test_account_create_reads_the_password_on_stdin(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_account())

    mock_api.post("/v1/email/accounts").mock(side_effect=_capture)
    result = runner.invoke(
        app, ["email", "account", "create", ADDRESS], input=f"{PASSWORD}\n"
    )
    assert result.exit_code == 0, result.output
    assert captured == {
        "address": ADDRESS,
        "password": PASSWORD,
        "enable_imap": True,
        "enable_pop": False,
    }
    # Le quota par défaut vient de l'API : rien n'est envoyé quand il est omis.
    assert "quota_gb" not in captured
    # Et il ne ressort jamais dans la sortie.
    assert PASSWORD not in result.output


def test_account_create_has_no_password_option_at_all(runner, mock_api) -> None:
    """Un mot de passe en argument finit dans l'historique du shell."""
    result = runner.invoke(
        app, ["email", "account", "create", ADDRESS, "--password", PASSWORD]
    )
    assert result.exit_code != 0
    assert not any(call.request.method == "POST" for call in mock_api.calls)


def test_account_create_without_password_on_stdin_fails(runner, mock_api) -> None:
    result = runner.invoke(app, ["email", "account", "create", ADDRESS], input="\n")
    assert result.exit_code == 1
    assert "entrée standard" in result.output
    assert not mock_api.calls


def test_account_create_sends_the_optional_fields(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_account())

    mock_api.post("/v1/email/accounts").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["email", "account", "create", ADDRESS, "--quota-gb", "5", "--pop3",
         "--comment", "boîte partagée", "--displayed-name", "Service commercial"],
        input=f"{PASSWORD}\n",
    )
    assert result.exit_code == 0, result.output
    assert captured["quota_gb"] == 5
    assert captured["enable_pop"] is True
    assert captured["comment"] == "boîte partagée"
    assert captured["displayed_name"] == "Service commercial"


def test_account_create_relays_a_weak_password_refusal(runner, mock_api) -> None:
    mock_api.post("/v1/email/accounts").mock(return_value=httpx.Response(
        422, json={"detail": "Le mot de passe doit faire au moins 12 caractères."},
    ))
    result = runner.invoke(app, ["email", "account", "create", ADDRESS], input="court\n")
    assert result.exit_code == 1
    assert "12 caractères" in result.output


def test_account_show_includes_client_config(runner, mock_api) -> None:
    _mock_account_list(mock_api)
    detail = _account()
    detail["client_config"] = _domain_detail()["client_config"]
    mock_api.get(f"/v1/email/accounts/{ACCOUNT_ID}").mock(
        return_value=httpx.Response(200, json=detail)
    )
    result = runner.invoke(app, ["email", "account", "show", ADDRESS])
    assert result.exit_code == 0, result.output
    assert "Configuration client" in result.output
    assert "465" in result.output


def test_account_update_maps_forward_flags(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_account())

    _mock_account_list(mock_api)
    mock_api.patch(f"/v1/email/accounts/{ACCOUNT_ID}").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "email", "account", "update", ADDRESS,
        "--forward", "a@ailleurs.com", "--forward", "b@ailleurs.com",
        "--no-forward-keep", "--disable", "--force-password-change",
    ])
    assert result.exit_code == 0, result.output
    assert captured == {
        "enabled": False,
        "forward_destination": ["a@ailleurs.com", "b@ailleurs.com"],
        "forward_enabled": True,
        "forward_keep": False,
        "change_pw_next_login": True,
    }


def test_account_update_no_forward_disables_it(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_account())

    _mock_account_list(mock_api)
    mock_api.patch(f"/v1/email/accounts/{ACCOUNT_ID}").mock(side_effect=_capture)
    result = runner.invoke(app, ["email", "account", "update", ADDRESS, "--no-forward"])
    assert result.exit_code == 0, result.output
    assert captured == {"forward_enabled": False}


def test_account_update_forward_and_no_forward_are_exclusive(runner, mock_api) -> None:
    result = runner.invoke(app, [
        "email", "account", "update", ADDRESS, "--forward", "a@b.com", "--no-forward",
    ])
    assert result.exit_code == 1
    assert not any(call.request.method == "PATCH" for call in mock_api.calls)


def test_account_update_without_any_field_changes_nothing(runner, mock_api) -> None:
    result = runner.invoke(app, ["email", "account", "update", ADDRESS])
    assert result.exit_code == 1
    assert "Rien à modifier" in result.output
    assert not mock_api.calls


def test_account_password_posts_on_the_password_route(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(204)

    _mock_account_list(mock_api)
    mock_api.post(f"/v1/email/accounts/{ACCOUNT_ID}/password").mock(side_effect=_capture)
    result = runner.invoke(
        app, ["email", "account", "password", ADDRESS], input=f"{PASSWORD}\n"
    )
    assert result.exit_code == 0, result.output
    assert captured == {"password": PASSWORD}
    assert PASSWORD not in result.output


def test_account_delete_calls_delete(runner, mock_api) -> None:
    _mock_account_list(mock_api)
    route = mock_api.delete(f"/v1/email/accounts/{ACCOUNT_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["email", "account", "delete", ADDRESS, "--yes"])
    assert result.exit_code == 0, result.output
    assert route.called


def test_account_show_exposes_send_as_read_only(runner, mock_api) -> None:
    """« Envoyer en tant que » se lit ici ; il ne s'active que via l'IAM/console."""
    _mock_account_list(mock_api, [_account(send_as_any_address=True, send_as_pending=True)])
    mock_api.get(f"/v1/email/accounts/{ACCOUNT_ID}").mock(
        return_value=httpx.Response(200, json=_account(send_as_any_address=True,
                                                       send_as_pending=True))
    )
    result = runner.invoke(app, ["email", "account", "show", ADDRESS])
    assert result.exit_code == 0, result.output
    assert "send_as_any_address" in result.output
    assert "send_as_pending" in result.output

    # Aucune commande n'écrit ce drapeau depuis le CLI.
    written = runner.invoke(app, ["email", "account", "send-as", ADDRESS, "--enable"])
    assert written.exit_code != 0


# ---------------------------------------------------------------------------
# Jetons d'application
# ---------------------------------------------------------------------------


def test_token_create_reveals_the_value_once(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={
            "id": TOKEN_ID, "comment": "sauvegarde", "authorized_ips": ["203.0.113.7/32"],
            "created_at": "2026-09-04T08:00:00Z", "token": "ccp_mail_secret",
        })

    _mock_account_list(mock_api)
    mock_api.post(f"/v1/email/accounts/{ACCOUNT_ID}/tokens").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "email", "account", "token", "create", ADDRESS,
        "--comment", "sauvegarde", "--ip", "203.0.113.7/32",
    ])
    assert result.exit_code == 0, result.output
    assert captured == {"comment": "sauvegarde", "authorized_ips": ["203.0.113.7/32"]}
    assert "ccp_mail_secret" in result.output
    # La liste fait foi avant toute révocation : l'identifiant rendu est indicatif.
    assert "token list" in result.output


def test_token_list_never_shows_a_value(runner, mock_api) -> None:
    _mock_account_list(mock_api)
    mock_api.get(f"/v1/email/accounts/{ACCOUNT_ID}/tokens").mock(
        return_value=httpx.Response(200, json=[{
            "id": TOKEN_ID, "comment": "sauvegarde", "authorized_ips": [],
            "created_at": "2026-09-04T08:00:00Z",
        }])
    )
    result = runner.invoke(app, ["email", "account", "token", "list", ADDRESS])
    assert result.exit_code == 0, result.output
    assert TOKEN_ID in result.output
    assert "sauvegarde" in result.output


def test_token_revoke_deletes_the_right_token(runner, mock_api) -> None:
    _mock_account_list(mock_api)
    route = mock_api.delete(f"/v1/email/accounts/{ACCOUNT_ID}/tokens/{TOKEN_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, [
        "email", "account", "token", "revoke", ADDRESS, TOKEN_ID, "--yes",
    ])
    assert result.exit_code == 0, result.output
    assert route.called


# ---------------------------------------------------------------------------
# Alias
# ---------------------------------------------------------------------------


def test_alias_create_body(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_alias(destinations=[ADDRESS, "b@ailleurs.com"]))

    mock_api.post("/v1/email/aliases").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "email", "alias", "create", f"info@{FQDN}",
        "--to", ADDRESS, "--to", "b@ailleurs.com",
    ])
    assert result.exit_code == 0, result.output
    assert captured == {
        "address": f"info@{FQDN}",
        "destinations": [ADDRESS, "b@ailleurs.com"],
        "wildcard": False,
    }


def test_alias_create_wildcard(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_alias(address=f"*@{FQDN}", wildcard=True))

    mock_api.post("/v1/email/aliases").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "email", "alias", "create", f"*@{FQDN}", "--to", ADDRESS, "--wildcard",
    ])
    assert result.exit_code == 0, result.output
    assert captured["wildcard"] is True
    assert captured["address"] == f"*@{FQDN}"


def test_alias_create_requires_a_destination(runner, mock_api) -> None:
    result = runner.invoke(app, ["email", "alias", "create", f"info@{FQDN}"])
    assert result.exit_code != 0
    assert not mock_api.calls


def test_alias_update_replaces_destinations(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_alias(destinations=["seul@ailleurs.com"]))

    mock_api.get("/v1/email/aliases").mock(
        return_value=httpx.Response(200, json=[_alias()])
    )
    mock_api.patch(f"/v1/email/aliases/{ALIAS_ID}").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "email", "alias", "update", f"info@{FQDN}", "--to", "seul@ailleurs.com",
    ])
    assert result.exit_code == 0, result.output
    # `destinations` remplace : la liste envoyée est exactement celle demandée.
    assert captured == {"destinations": ["seul@ailleurs.com"]}


def test_alias_update_without_field_changes_nothing(runner, mock_api) -> None:
    result = runner.invoke(app, ["email", "alias", "update", f"info@{FQDN}"])
    assert result.exit_code == 1
    assert "Rien à modifier" in result.output
    assert not mock_api.calls


def test_alias_delete(runner, mock_api) -> None:
    mock_api.get("/v1/email/aliases").mock(
        return_value=httpx.Response(200, json=[_alias()])
    )
    route = mock_api.delete(f"/v1/email/aliases/{ALIAS_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["email", "alias", "delete", f"info@{FQDN}", "--yes"])
    assert result.exit_code == 0, result.output
    assert route.called


def test_alias_list_filters_by_domain(runner, mock_api) -> None:
    seen: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[_alias()])

    _mock_domain_list(mock_api)
    mock_api.get("/v1/email/aliases").mock(side_effect=_capture)
    result = runner.invoke(app, ["email", "alias", "list", "--domain", FQDN])
    assert result.exit_code == 0, result.output
    assert seen["params"] == {"domain_id": DOMAIN_ID}


def test_email_group_is_registered(runner) -> None:
    from cetic.main import app as main_app

    assert "email" in [g.name for g in main_app.registered_groups]
