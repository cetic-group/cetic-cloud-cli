"""cetic-cloud-platform#2004 — `support create` et `support get` alignés sur l'API.

- catégories de l'API : technical | billing | abuse | feature_request | other
  (`question`, l'ancien défaut, était refusé en 422) ;
- la création répond `{ticket: …}` (TicketGatedResponse) : plus de KeyError ;
- auteurs des messages : `customer` | `admin` | `system`.
"""
from __future__ import annotations

import json

import httpx

from cetic.main import app

T = "66666666-6666-6666-6666-666666666666"


def test_create_categorie_par_defaut_et_reponse_enveloppee(runner, mock_api):
    route = mock_api.post("/v1/support/tickets").mock(return_value=httpx.Response(201, json={
        "ticket": {"id": T, "subject": "Aide", "messages": []},
        "max_priority_allowed": "normal", "priority_capped_from": "urgent",
        "upgrade_message": "Priorité limitée par votre plan."}))
    result = runner.invoke(app, ["support", "create", "--subject", "Aide", "--body", "bonjour",
                                 "--priority", "urgent"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(route.calls.last.request.content)["category"] == "other"
    assert T in result.stdout
    assert "Priorité limitée" in result.stdout


def test_get_distingue_le_client_du_support(runner, mock_api):
    mock_api.get(f"/v1/support/tickets/{T}").mock(return_value=httpx.Response(200, json={
        "id": T, "subject": "Aide", "status": "open", "priority": "normal", "category": "other",
        "created_at": "2026-11-21T10:00:00Z",
        "messages": [
            {"author_type": "customer", "body": "question du client", "created_at": "2026-11-21T10:00"},
            {"author_type": "admin", "body": "réponse du support", "created_at": "2026-11-21T11:00"},
        ]}))
    result = runner.invoke(app, ["support", "get", T])
    assert result.exit_code == 0, result.stdout + result.stderr
    lignes = result.stdout.splitlines()
    i_client = next(i for i, l in enumerate(lignes) if "question du client" in l)
    i_support = next(i for i, l in enumerate(lignes) if "réponse du support" in l)
    assert "Vous" in lignes[i_client - 1]
    assert "Support CETIC" in lignes[i_support - 1]


def test_subscribe_402_oriente_vers_la_console(runner, mock_api):
    mock_api.post("/v1/support/subscribe").mock(return_value=httpx.Response(
        402, json={"detail": "Moyen de paiement requis"}))
    result = runner.invoke(app, ["support", "plan", "subscribe", "standard", "--yes"])
    assert result.exit_code == 2, result.stdout + result.stderr
    sortie = result.stdout + result.stderr
    assert "console" in sortie
    assert "add-card" not in sortie
