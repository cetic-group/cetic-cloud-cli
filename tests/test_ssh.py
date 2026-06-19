"""Tests pour `cetic ssh` / `cetic scp`.

Couvre :
- helpers purs (parse IP, CIDR, VPC d'un bastion, découpe TARGET:chemin) ;
- sélection du bastion **par VPC de la cible** (le bug corrigé : on ne prend
  plus le premier bastion venu quand plusieurs existent) ;
- garde-fous d'arguments de `scp`.
"""
from __future__ import annotations

import httpx
import pytest

from cetic.commands import ssh as ssh_cmd
from cetic.main import app

VPC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
VPC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# ── helpers purs ─────────────────────────────────────────────────────────────
def test_parse_ip_valid_and_invalid():
    assert ssh_cmd._parse_ip("10.111.0.53") is not None
    assert ssh_cmd._parse_ip("web-01") is None
    assert ssh_cmd._parse_ip("not.an.ip") is None


def test_cidr_contains():
    ip = ssh_cmd._parse_ip("10.111.0.53")
    assert ssh_cmd._cidr_contains("10.111.0.0/23", ip) is True
    assert ssh_cmd._cidr_contains("10.20.0.0/16", ip) is False
    assert ssh_cmd._cidr_contains(None, ip) is False
    assert ssh_cmd._cidr_contains("garbage", ip) is False


def test_bastion_vpc_ids_merges_primary_and_secondary():
    b = {"vpc_id": VPC_A, "vpc_ids": [VPC_A, VPC_B]}
    assert ssh_cmd._bastion_vpc_ids(b) == {VPC_A, VPC_B}
    assert ssh_cmd._bastion_vpc_ids({"vpc_id": VPC_A}) == {VPC_A}
    assert ssh_cmd._bastion_vpc_ids({"vpc_ids": []}) == set()


def test_split_remote():
    assert ssh_cmd._split_remote("10.0.1.42:/etc/hosts") == ("10.0.1.42", "/etc/hosts")
    assert ssh_cmd._split_remote("web-01:/srv") == ("web-01", "/srv")
    assert ssh_cmd._split_remote("./local/path") is None
    assert ssh_cmd._split_remote(":nohost") is None


# ── sélection du bastion par VPC ─────────────────────────────────────────────
def _bastion(host: str, vpc: str) -> dict:
    return {"endpoint_host": host, "status": "active", "vpc_id": vpc, "vpc_ids": [vpc]}


def test_explicit_bastion_wins():
    assert ssh_cmd._resolve_bastion_host("explicit.host", "10.0.0.1") == "explicit.host"


def test_single_bastion_used(mock_api):
    mock_api.get("/v1/bastions").mock(
        return_value=httpx.Response(200, json=[_bastion("b1", VPC_A)])
    )
    assert ssh_cmd._resolve_bastion_host(None, "10.111.0.53") == "b1"


def test_picks_bastion_matching_target_vpc(mock_api):
    # Deux bastions dans deux VPC ; la cible 10.111.0.53 est dans un VNet du VPC B.
    mock_api.get("/v1/bastions").mock(return_value=httpx.Response(200, json=[
        _bastion("bastion-a", VPC_A),
        _bastion("bastion-b", VPC_B),
    ]))
    mock_api.get("/v1/vpcs").mock(return_value=httpx.Response(200, json=[
        {"id": VPC_A, "cidr": None},
        {"id": VPC_B, "cidr": None},
    ]))
    mock_api.get(f"/v1/vpcs/{VPC_A}/vnets").mock(
        return_value=httpx.Response(200, json=[{"cidr": "10.20.0.0/23", "vpc_id": VPC_A}])
    )
    mock_api.get(f"/v1/vpcs/{VPC_B}/vnets").mock(
        return_value=httpx.Response(200, json=[{"cidr": "10.111.0.0/23", "vpc_id": VPC_B}])
    )
    assert ssh_cmd._resolve_bastion_host(None, "10.111.0.53") == "bastion-b"


def test_picks_bastion_via_vpc_cidr_fast_path(mock_api):
    mock_api.get("/v1/bastions").mock(return_value=httpx.Response(200, json=[
        _bastion("bastion-a", VPC_A),
        _bastion("bastion-b", VPC_B),
    ]))
    mock_api.get("/v1/vpcs").mock(return_value=httpx.Response(200, json=[
        {"id": VPC_A, "cidr": "10.20.0.0/16"},
        {"id": VPC_B, "cidr": "10.111.0.0/16"},
    ]))
    assert ssh_cmd._resolve_bastion_host(None, "10.111.0.53") == "bastion-b"


def test_no_bastion_covers_target_vpc_errors(mock_api):
    # La cible est dans un 3ᵉ VPC (C) qu'aucun bastion ne dessert.
    import typer
    vpc_c = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    mock_api.get("/v1/bastions").mock(return_value=httpx.Response(200, json=[
        _bastion("bastion-a", VPC_A),
        _bastion("bastion-b", VPC_B),
    ]))
    mock_api.get("/v1/vpcs").mock(return_value=httpx.Response(200, json=[
        {"id": VPC_A, "cidr": "10.20.0.0/16"},
        {"id": VPC_B, "cidr": "10.30.0.0/16"},
        {"id": vpc_c, "cidr": "10.111.0.0/16"},
    ]))
    with pytest.raises(typer.Exit):
        ssh_cmd._resolve_bastion_host(None, "10.111.0.53")


def test_ambiguous_non_ip_target_errors(mock_api):
    import typer
    mock_api.get("/v1/bastions").mock(return_value=httpx.Response(200, json=[
        _bastion("bastion-a", VPC_A),
        _bastion("bastion-b", VPC_B),
    ]))
    with pytest.raises(typer.Exit):
        ssh_cmd._resolve_bastion_host(None, "web-01")


# ── scp : garde-fous d'arguments ─────────────────────────────────────────────
def test_scp_rejects_two_local_paths(runner):
    result = runner.invoke(app, ["scp", "./a", "./b"])
    assert result.exit_code == 1
    assert "exactement une extrémité" in (result.stdout + result.stderr)


def test_scp_rejects_two_remote_paths(runner):
    result = runner.invoke(app, ["scp", "10.0.0.1:/a", "10.0.0.2:/b"])
    assert result.exit_code == 1
