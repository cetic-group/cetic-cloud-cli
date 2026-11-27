"""Commandes compute alignées sur les routes de l'API.

- start/stop/reboot : `POST /v1/{containers|vm-instances}/{id}/actions`
  (`{"action": …}` ; le redémarrage d'un conteneur s'appelle `restart`) ;
- scale d'un scale set : `PATCH /v1/{container|vm}-scale-sets/{id}`
  (`desired_instances`), aucune route `/scale` ;
- colonne Replicas des listes : `desired_instances`.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cetic.main import app

ID = "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize(("cmd", "action"), [("start", "start"), ("stop", "stop"), ("reboot", "restart")])
def test_container_actions(runner, mock_api, cmd, action):
    route = mock_api.post(f"/v1/containers/{ID}/actions").mock(
        return_value=httpx.Response(200, json={"id": ID}))
    result = runner.invoke(app, ["container", cmd, ID])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert route.called
    assert json.loads(route.calls.last.request.content) == {"action": action}


@pytest.mark.parametrize("action", ["start", "stop", "reboot"])
def test_vm_actions(runner, mock_api, action):
    route = mock_api.post(f"/v1/vm-instances/{ID}/actions").mock(
        return_value=httpx.Response(200, json={"vm_id": ID, "action": action, "status": "scheduled"}))
    result = runner.invoke(app, ["vm", action, ID])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(route.calls.last.request.content) == {"action": action}


@pytest.mark.parametrize(("groupe", "chemin"), [("ct-scale-set", "container-scale-sets"),
                                                 ("vm-scale-set", "vm-scale-sets")])
def test_scale_par_patch(runner, mock_api, groupe, chemin):
    route = mock_api.patch(f"/v1/{chemin}/{ID}").mock(
        return_value=httpx.Response(200, json={"id": ID, "desired_instances": 4}))
    result = runner.invoke(app, [groupe, "scale", ID, "--replicas", "4"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(route.calls.last.request.content) == {"desired_instances": 4}


def test_liste_affiche_desired_instances(runner, mock_api):
    mock_api.get("/v1/container-scale-sets").mock(return_value=httpx.Response(200, json=[{
        "id": ID, "name": "web", "region": "RNN", "plan": "small",
        "desired_instances": 3, "status": "active"}]))
    result = runner.invoke(app, ["ct-scale-set", "list"], env={"CCP_OUTPUT": "json"})
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)[0]["replicas"] == 3
