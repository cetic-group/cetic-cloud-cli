"""Tests `cetic member orgs` — la forme réelle de la réponse API (#59)."""

from __future__ import annotations

import json

import httpx

from cetic.main import app

# Forme renvoyée par `GET /v1/members/accessible-orgs` : des comptes, pas des
# organisations (aucune clé `id` ni `name`).
REPONSE = [
    {"tenant_id": "11111111-1111-1111-1111-111111111111", "email": "moi@exemple.fr",
     "company_name": None, "role": "owner"},
    {"tenant_id": "22222222-2222-2222-2222-222222222222", "email": "client@exemple.fr",
     "company_name": "Exemple SAS", "role": "admin"},
]


def test_member_orgs_lit_la_forme_reelle_de_l_api(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/members/accessible-orgs").mock(
        return_value=httpx.Response(200, json=REPONSE))

    result = runner.invoke(app, ["member", "orgs"])

    assert result.exit_code == 0, result.output
    lignes = json.loads(result.output)
    assert lignes == [
        {"tenant_id": REPONSE[0]["tenant_id"], "name": "moi@exemple.fr", "role": "owner"},
        {"tenant_id": REPONSE[1]["tenant_id"], "name": "Exemple SAS", "role": "admin"},
    ]


def test_member_orgs_tableau_sans_keyerror(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "table")
    mock_api.get("/v1/members/accessible-orgs").mock(
        return_value=httpx.Response(200, json=REPONSE))

    result = runner.invoke(app, ["member", "orgs"])

    assert result.exit_code == 0, result.output
    assert "Comptes accessibles (2)" in result.output
    assert "Exemple SAS" in result.output


def test_member_orgs_champs_absents(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/members/accessible-orgs").mock(
        return_value=httpx.Response(200, json=[{}]))

    result = runner.invoke(app, ["member", "orgs"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [{"tenant_id": "—", "name": "—", "role": "—"}]
