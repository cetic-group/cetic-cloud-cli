"""cetic-cloud-platform#2004 — validations alignées sur l'API."""
from __future__ import annotations

import json

import httpx
import pytest

from cetic._iam_arn import parse_arn
from cetic.main import app

TENANT = "11111111-1111-1111-1111-111111111111"


@pytest.mark.parametrize("service", ["secrets", "dns", "email", "vpn", "bastion", "appgw",
                                     "schedules", "blockchain", "ccks", "topology"])
def test_arn_des_services_de_l_api_accepte(service):
    assert parse_arn(f"arn:ccp:{service}:rnn:{TENANT}:*").service == service


@pytest.mark.parametrize("type_", ["secret", "bastion", "vpn_gateway"])
def test_tag_set_types_ajoutes(runner, mock_api, type_):
    route = mock_api.put("/v1/tags").mock(return_value=httpx.Response(200, json={"tags": ["a"]}))
    result = runner.invoke(app, ["tag", "set", "--type", type_, "--id", TENANT, "--tag", "a"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(route.calls.last.request.content)["resource_type"] == type_


def test_tag_set_type_inconnu_refuse_avant_l_api(runner, mock_api):
    result = runner.invoke(app, ["tag", "set", "--type", "machin", "--id", TENANT, "--tag", "a"])
    assert result.exit_code == 2
    assert not mock_api.calls
