"""Tests de `cetic dns` — zones privées et rrsets (issue cetic-cloud-cli#49).

Contrat amont : `apps/api/app/services/DNS_CONTRACT.md` (cetic-cloud-platform#1387).
Ce qui est vérifié ici et ne se devine pas :
- `record set` REMPLACE le couple (nom, type) — POST si absent, PATCH sinon ;
- le rrset s'adresse par (nom, type), l'UUID est résolu depuis le listing ;
- la zone est portée par un VPC (`vpc_id`), jamais par un sous-réseau ;
- un domaine public naît en attente et rend sa preuve de possession.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cetic.commands.dns import find_record_set, normalize_record_type, qualify
from cetic.main import app

ZONE_ID = "11111111-1111-1111-1111-111111111111"
VPC_ID = "22222222-2222-2222-2222-222222222222"
VNET_A = "33333333-3333-3333-3333-333333333333"
RRSET_ID = "44444444-4444-4444-4444-444444444444"
ZONE_NAME = "corp.internal"


def _zone(
    *,
    status: str = "active",
    name: str = ZONE_NAME,
    challenge: dict[str, Any] | None = None,
    endpoints: bool = True,
) -> dict[str, Any]:
    return {
        "id": ZONE_ID,
        "name": name,
        "vpc_id": VPC_ID,
        "region": "RNN",
        "status": status,
        "default_ttl": 3600,
        "dnssec_enabled": False,
        "error_message": None,
        "created_at": "2026-09-01T10:00:00Z",
        "record_sets_count": 2,
        "resolver": {
            "addresses": ["10.0.0.51"],
            "endpoints": (
                [{"address": "10.0.0.51", "vnet_id": VNET_A,
                  "vnet_name": "bureau", "vnet_cidr": "10.0.0.0/24"}]
                if endpoints
                else []
            ),
            "tier": "dev",
            "status": "active",
            "ns_hostname": "ns1.dns.cloud.cetic-group.com",
            "applies_to_new_guests_only": True,
        },
        "ownership_challenge": challenge,
    }


def _rrset(
    *,
    name: str = f"www.{ZONE_NAME}",
    record_type: str = "A",
    records: list[str] | None = None,
    system: bool = False,
    rid: str = RRSET_ID,
) -> dict[str, Any]:
    return {
        "id": rid,
        "zone_id": ZONE_ID,
        "name": name,
        "record_type": record_type,
        "ttl": 3600,
        "records": records if records is not None else ["10.0.0.10"],
        "is_system_managed": system,
        "created_at": "2026-09-01T10:00:00Z",
    }


def _mock_zone(mock_api, zone: dict[str, Any] | None = None) -> None:
    """Résolution d'une zone par UUID (GET direct de la fiche)."""
    mock_api.get(f"/v1/dns/zones/{ZONE_ID}").mock(
        return_value=httpx.Response(200, json=zone or _zone())
    )


# ---------------------------------------------------------------------------
# Helpers purs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("www", f"www.{ZONE_NAME}"),
        ("WWW", f"www.{ZONE_NAME}"),
        (f"www.{ZONE_NAME}", f"www.{ZONE_NAME}"),
        (f"www.{ZONE_NAME}.", f"www.{ZONE_NAME}"),
        ("@", ZONE_NAME),
        (ZONE_NAME, ZONE_NAME),
        ("_sip._tcp", f"_sip._tcp.{ZONE_NAME}"),
    ],
)
def test_qualify_accepts_relative_absolute_and_apex(given: str, expected: str) -> None:
    assert qualify(given, ZONE_NAME) == expected


def test_qualify_does_not_swallow_a_similar_suffix() -> None:
    # `notcorp.internal` ne se termine PAS par `.corp.internal` : il est relatif.
    assert qualify("notcorp.internal", ZONE_NAME) == f"notcorp.internal.{ZONE_NAME}"


def test_find_record_set_matches_on_name_and_type() -> None:
    rows = [
        _rrset(name=f"www.{ZONE_NAME}", record_type="A"),
        _rrset(name=f"www.{ZONE_NAME}", record_type="AAAA", rid="other"),
        _rrset(name=ZONE_NAME, record_type="NS", system=True, rid="ns"),
    ]
    assert find_record_set(rows, fqdn=f"www.{ZONE_NAME}", record_type="AAAA")["id"] == "other"
    assert find_record_set(rows, fqdn=f"api.{ZONE_NAME}", record_type="A") is None


def test_normalize_record_type_rejects_unknown() -> None:
    import typer

    assert normalize_record_type("a") == "A"
    with pytest.raises(typer.BadParameter):
        normalize_record_type("SOA")


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


def test_zone_list_columns(runner, mock_api) -> None:
    mock_api.get("/v1/dns/zones").mock(return_value=httpx.Response(200, json=[_zone()]))
    result = runner.invoke(app, ["dns", "zone", "list"])
    assert result.exit_code == 0, result.output
    assert ZONE_NAME in result.output
    assert VPC_ID in result.output


def test_zone_create_body_matches_contract(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_zone(status="provisioning"))

    mock_api.post("/v1/dns/zones").mock(side_effect=_capture)
    result = runner.invoke(app, ["dns", "zone", "create", ZONE_NAME, "--vpc", VPC_ID])
    assert result.exit_code == 0, result.output
    # La zone est portée par le VPC — pas par un sous-réseau — et le TTL omis
    # laisse le réglage de la plateforme s'appliquer.
    assert captured == {
        "name": ZONE_NAME,
        "vpc_id": VPC_ID,
        "tier": "dev",
        "dnssec_enabled": False,
    }


def test_zone_create_sends_ttl_and_dnssec_when_asked(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_zone(status="provisioning"))

    mock_api.post("/v1/dns/zones").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "dns", "zone", "create", ZONE_NAME, "--vpc", VPC_ID,
        "--tier", "PROD", "--ttl", "600", "--dnssec",
    ])
    assert result.exit_code == 0, result.output
    assert captured["tier"] == "prod"
    assert captured["default_ttl"] == 600
    assert captured["dnssec_enabled"] is True


def test_zone_create_resolves_vpc_by_name(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_zone(status="provisioning"))

    mock_api.get("/v1/vpcs").mock(return_value=httpx.Response(200, json=[
        {"id": VPC_ID, "name": "prod", "region": "RNN"},
    ]))
    mock_api.post("/v1/dns/zones").mock(side_effect=_capture)
    result = runner.invoke(app, ["dns", "zone", "create", ZONE_NAME, "--vpc", "prod"])
    assert result.exit_code == 0, result.output
    assert captured["vpc_id"] == VPC_ID


def test_zone_create_invalid_tier_never_calls_the_api(runner, mock_api) -> None:
    result = runner.invoke(app, [
        "dns", "zone", "create", ZONE_NAME, "--vpc", VPC_ID, "--tier", "ha",
    ])
    assert result.exit_code == 1
    assert not any(call.request.method == "POST" for call in mock_api.calls)


def test_zone_create_tier_conflict_relays_the_api_message(runner, mock_api) -> None:
    """Le message porte le niveau effectif du réseau : on le rend tel quel."""
    mock_api.post("/v1/dns/zones").mock(return_value=httpx.Response(
        409, json={"detail": "Ce réseau est déjà servi au niveau « prod ». "
                             "Toutes ses zones partagent ce serveur."},
    ))
    result = runner.invoke(app, ["dns", "zone", "create", ZONE_NAME, "--vpc", VPC_ID])
    assert result.exit_code == 1
    assert "déjà servi au niveau" in result.output
    assert "prod" in result.output


def test_zone_create_public_domain_prints_the_ownership_record(runner, mock_api) -> None:
    challenge = {
        "record_name": "_ccp-dns-verification.exemple.com",
        "record_type": "TXT",
        "record_value": "ccp-verify=abc123",
        "reason": "Ce nom est un domaine public.",
    }
    mock_api.post("/v1/dns/zones").mock(return_value=httpx.Response(
        201, json=_zone(status="pending_verification", name="exemple.com", challenge=challenge),
    ))
    result = runner.invoke(app, ["dns", "zone", "create", "exemple.com", "--vpc", VPC_ID])
    assert result.exit_code == 0, result.output
    assert "_ccp-dns-verification.exemple.com" in result.output
    assert "ccp-verify=abc123" in result.output
    assert "zone verify" in result.output


def test_service_unavailable_says_retrying_is_pointless(runner, mock_api) -> None:
    mock_api.get("/v1/dns/zones").mock(return_value=httpx.Response(
        503, json={"detail": "Le service DNS n'est pas déployé sur cette plateforme."},
    ))
    result = runner.invoke(app, ["dns", "zone", "list"])
    assert result.exit_code == 1
    assert "réessayer n'y changera rien" in result.output.lower()


def test_zone_get_table_shows_resolver_per_subnet_and_the_guest_caveat(
    runner, mock_api
) -> None:
    _mock_zone(mock_api)
    result = runner.invoke(app, ["dns", "zone", "get", ZONE_ID])
    assert result.exit_code == 0, result.output
    assert "10.0.0.51" in result.output
    assert "bureau" in result.output          # le sous-réseau de CETTE adresse
    assert "10.0.0.0/24" in result.output
    # Sans cette phrase, l'absence d'effet sur les machines existantes passe
    # pour une panne.
    assert "à leur" in result.output and "création" in result.output


def test_zone_get_without_resolver_says_so(runner, mock_api) -> None:
    zone = _zone(status="provisioning", endpoints=False)
    zone["resolver"]["addresses"] = []
    mock_api.get(f"/v1/dns/zones/{ZONE_ID}").mock(return_value=httpx.Response(200, json=zone))
    result = runner.invoke(app, ["dns", "zone", "get", ZONE_ID])
    assert result.exit_code == 0, result.output
    assert "pas encore debout" in result.output


def test_zone_get_json_is_the_raw_payload(runner, monkeypatch, mock_api) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    _mock_zone(mock_api)
    result = runner.invoke(app, ["dns", "zone", "get", ZONE_ID])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["resolver"]["endpoints"][0]["vnet_id"] == VNET_A
    assert data["id"] == ZONE_ID


def test_zone_get_resolves_by_name(runner, mock_api) -> None:
    mock_api.get("/v1/dns/zones").mock(return_value=httpx.Response(200, json=[_zone()]))
    _mock_zone(mock_api)
    result = runner.invoke(app, ["dns", "zone", "get", ZONE_NAME])
    assert result.exit_code == 0, result.output
    assert ZONE_NAME in result.output


def test_zone_get_unknown_name_fails_without_a_second_call(runner, mock_api) -> None:
    mock_api.get("/v1/dns/zones").mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(app, ["dns", "zone", "get", "absente.internal"])
    assert result.exit_code == 1
    assert "Aucune zone" in result.output


def test_zone_verify_posts_and_reports_status(runner, mock_api) -> None:
    _mock_zone(mock_api, _zone(status="pending_verification"))
    route = mock_api.post(f"/v1/dns/zones/{ZONE_ID}/verify").mock(
        return_value=httpx.Response(200, json=_zone(status="provisioning"))
    )
    result = runner.invoke(app, ["dns", "zone", "verify", ZONE_ID])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "provisioning" in result.output


def test_zone_verify_still_pending_exits_nonzero(runner, mock_api) -> None:
    challenge = {
        "record_name": "_ccp-dns-verification.exemple.com",
        "record_type": "TXT",
        "record_value": "ccp-verify=abc123",
    }
    _mock_zone(mock_api, _zone(status="pending_verification", name="exemple.com"))
    mock_api.post(f"/v1/dns/zones/{ZONE_ID}/verify").mock(return_value=httpx.Response(
        200, json=_zone(status="pending_verification", name="exemple.com", challenge=challenge),
    ))
    result = runner.invoke(app, ["dns", "zone", "verify", ZONE_ID])
    assert result.exit_code == 1
    assert "ccp-verify=abc123" in result.output


def test_zone_delete_confirms_then_deletes(runner, mock_api) -> None:
    _mock_zone(mock_api)
    route = mock_api.delete(f"/v1/dns/zones/{ZONE_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["dns", "zone", "delete", ZONE_ID, "--yes"])
    assert result.exit_code == 0, result.output
    assert route.called


def test_zone_delete_refused_relays_the_reason(runner, mock_api) -> None:
    _mock_zone(mock_api)
    mock_api.delete(f"/v1/dns/zones/{ZONE_ID}").mock(return_value=httpx.Response(
        409, json={"detail": "La zone porte encore des enregistrements."},
    ))
    result = runner.invoke(app, ["dns", "zone", "delete", ZONE_ID, "--yes"])
    assert result.exit_code == 1
    assert "porte encore des enregistrements" in result.output


# ---------------------------------------------------------------------------
# Enregistrements
# ---------------------------------------------------------------------------


def test_record_list_shows_values_and_platform_ownership(runner, mock_api) -> None:
    _mock_zone(mock_api)
    mock_api.get(f"/v1/dns/zones/{ZONE_ID}/records").mock(return_value=httpx.Response(
        200,
        json=[
            _rrset(records=["10.0.0.10", "10.0.0.11"]),
            _rrset(name=ZONE_NAME, record_type="NS", records=["ns1.dns.cloud.cetic-group.com."],
                   system=True, rid="ns"),
        ],
    ))
    result = runner.invoke(app, ["dns", "record", "list", ZONE_ID])
    assert result.exit_code == 0, result.output
    assert "10.0.0.10, 10.0.0.11" in result.output
    assert "plateforme" in result.output


def test_record_list_json_keeps_uuid_and_value_list(runner, monkeypatch, mock_api) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    _mock_zone(mock_api)
    mock_api.get(f"/v1/dns/zones/{ZONE_ID}/records").mock(
        return_value=httpx.Response(200, json=[_rrset(records=["10.0.0.10"])])
    )
    result = runner.invoke(app, ["dns", "record", "list", ZONE_ID])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data[0]["id"] == RRSET_ID
    assert data[0]["records"] == ["10.0.0.10"]


def test_record_set_creates_when_the_couple_is_absent(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_rrset(records=["10.0.0.10"]))

    _mock_zone(mock_api)
    mock_api.get(f"/v1/dns/zones/{ZONE_ID}/records").mock(
        return_value=httpx.Response(200, json=[])
    )
    mock_api.post(f"/v1/dns/zones/{ZONE_ID}/records").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "dns", "record", "set", ZONE_ID, "www", "a", "10.0.0.10",
    ])
    assert result.exit_code == 0, result.output
    # Le nom part tel qu'il a été saisi (l'API accepte le relatif), le type est
    # normalisé, et le TTL omis laisse le défaut de l'API.
    assert captured == {"name": "www", "record_type": "A", "records": ["10.0.0.10"]}


def test_record_set_replaces_the_whole_rrset_by_patch(runner, mock_api) -> None:
    captured: dict[str, Any] = {}
    seen_path: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        seen_path["path"] = request.url.path
        return httpx.Response(200, json=_rrset(records=["10.0.0.11"]))

    _mock_zone(mock_api)
    mock_api.get(f"/v1/dns/zones/{ZONE_ID}/records").mock(
        return_value=httpx.Response(200, json=[_rrset(records=["10.0.0.10"])])
    )
    mock_api.patch(f"/v1/dns/zones/{ZONE_ID}/records/{RRSET_ID}").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "dns", "record", "set", ZONE_ID, "www", "A", "10.0.0.11", "--ttl", "300",
    ])
    assert result.exit_code == 0, result.output
    # Le rrset est adressé par son UUID, résolu depuis le listing sur (nom, type).
    assert seen_path["path"].endswith(f"/records/{RRSET_ID}")
    # `records` REMPLACE : 10.0.0.10 n'est pas conservé, et `name`/`record_type`
    # ne sont pas renvoyés (ils identifient le rrset).
    assert captured == {"records": ["10.0.0.11"], "ttl": 300}
    assert "remplacé" in result.output


def test_record_set_accepts_several_values_and_the_apex(runner, mock_api) -> None:
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json=_rrset(name=ZONE_NAME, record_type="MX"))

    _mock_zone(mock_api)
    mock_api.get(f"/v1/dns/zones/{ZONE_ID}/records").mock(
        return_value=httpx.Response(200, json=[])
    )
    mock_api.post(f"/v1/dns/zones/{ZONE_ID}/records").mock(side_effect=_capture)
    result = runner.invoke(app, [
        "dns", "record", "set", ZONE_ID, "@", "MX",
        "10 mail1.exemple.com.", "20 mail2.exemple.com.",
    ])
    assert result.exit_code == 0, result.output
    assert captured["records"] == ["10 mail1.exemple.com.", "20 mail2.exemple.com."]


def test_record_set_matches_an_absolute_name(runner, mock_api) -> None:
    """`www.corp.internal` et `www` désignent le même rrset."""
    _mock_zone(mock_api)
    mock_api.get(f"/v1/dns/zones/{ZONE_ID}/records").mock(
        return_value=httpx.Response(200, json=[_rrset()])
    )
    route = mock_api.patch(f"/v1/dns/zones/{ZONE_ID}/records/{RRSET_ID}").mock(
        return_value=httpx.Response(200, json=_rrset(records=["10.0.0.99"]))
    )
    result = runner.invoke(app, [
        "dns", "record", "set", ZONE_ID, f"www.{ZONE_NAME}.", "A", "10.0.0.99",
    ])
    assert result.exit_code == 0, result.output
    assert route.called


def test_record_set_invalid_type_never_calls_the_api(runner, mock_api) -> None:
    result = runner.invoke(app, ["dns", "record", "set", ZONE_ID, "www", "SOA", "x"])
    assert result.exit_code != 0
    assert not mock_api.calls


def test_record_set_on_a_system_rrset_relays_the_refusal(runner, mock_api) -> None:
    _mock_zone(mock_api)
    mock_api.get(f"/v1/dns/zones/{ZONE_ID}/records").mock(return_value=httpx.Response(
        200, json=[_rrset(name=ZONE_NAME, record_type="NS", system=True)],
    ))
    mock_api.patch(f"/v1/dns/zones/{ZONE_ID}/records/{RRSET_ID}").mock(
        return_value=httpx.Response(409, json={"detail": "Enregistrement système : lecture seule."})
    )
    result = runner.invoke(app, [
        "dns", "record", "set", ZONE_ID, "@", "NS", "ns9.exemple.com.",
    ])
    assert result.exit_code == 1
    assert "lecture seule" in result.output


def test_record_delete_resolves_the_couple_then_deletes(runner, mock_api) -> None:
    _mock_zone(mock_api)
    mock_api.get(f"/v1/dns/zones/{ZONE_ID}/records").mock(
        return_value=httpx.Response(200, json=[_rrset()])
    )
    route = mock_api.delete(f"/v1/dns/zones/{ZONE_ID}/records/{RRSET_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["dns", "record", "delete", ZONE_ID, "www", "A", "--yes"])
    assert result.exit_code == 0, result.output
    assert route.called


def test_record_delete_unknown_couple_fails_clearly(runner, mock_api) -> None:
    _mock_zone(mock_api)
    mock_api.get(f"/v1/dns/zones/{ZONE_ID}/records").mock(
        return_value=httpx.Response(200, json=[_rrset()])
    )
    result = runner.invoke(app, ["dns", "record", "delete", ZONE_ID, "api", "A", "--yes"])
    assert result.exit_code == 1
    assert "Aucun enregistrement A" in result.output
    assert not any(call.request.method == "DELETE" for call in mock_api.calls)


def test_dns_group_is_registered(runner) -> None:
    from cetic.main import app as main_app

    assert "dns" in [g.name for g in main_app.registered_groups]
