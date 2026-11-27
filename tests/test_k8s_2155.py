"""Tests `cetic k8s update --os` — OS par défaut des nouveaux pools, et retrait
de Rocky Linux 9 (cetic-cloud-platform#2155, #2194)."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from cetic.main import app

CID = "11111111-2222-3333-4444-555555555555"
BASE = f"/v1/k8s/clusters/{CID}"


def _cluster(**over: Any) -> dict[str, Any]:
    c = {"id": CID, "name": "ccks", "region": "RNN", "k8s_version": "1.34.8",
         "status": "active", "plan": "standard", "tier": "prod", "os_image": "ubuntu",
         "proxy_vip_vnet": "10.0.0.9",
         "node_pools": [
             {"id": "p1", "name": "default", "os_image": None},
             {"id": "p2", "name": "gpu", "os_image": "flatcar"},
             {"id": "p3", "name": "batch", "os_image": None},
         ]}
    c.update(over)
    return c


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(text: str) -> str:
    return " ".join(_ANSI.sub("", text).split())


def _capture_patch(mock_api, status: int = 200, payload: Any = None) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _side(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(status, json=payload if payload is not None else _cluster(
            os_image=captured["body"]["os_image"]))

    mock_api.patch(BASE).mock(side_effect=_side)
    return captured


def _no_patch(mock_api) -> bool:
    return not any(call.request.method == "PATCH" for call in mock_api.calls)


def test_update_os_sends_patch_and_lists_pinned_pools(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    cap = _capture_patch(mock_api)
    result = runner.invoke(app, ["k8s", "update", CID, "--os", "Flatcar", "-y"])
    assert result.exit_code == 0, result.output
    assert cap["body"] == {"os_image": "flatcar"}
    err = _flat(result.stderr)
    assert "Ubuntu → Flatcar" in err
    # Seuls les pools qui héritaient sont fixés sur l'ancien OS.
    assert "Pools fixés sur Ubuntu (ils en héritaient) : default, batch." in err
    assert "gpu" not in err
    assert "OS par défaut des nouveaux pools : Flatcar" in _flat(result.stdout)


def test_update_os_no_inheriting_pool(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster(
        node_pools=[{"id": "p2", "name": "gpu", "os_image": "flatcar"}])))
    _capture_patch(mock_api)
    result = runner.invoke(app, ["k8s", "update", CID, "--os", "rocky10", "-y"])
    assert result.exit_code == 0, result.output
    assert "Les pools existants gardent leur OS" in _flat(result.stderr)


def test_update_os_same_is_noop(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    result = runner.invoke(app, ["k8s", "update", CID, "--os", "ubuntu", "-y"])
    assert result.exit_code == 0, result.output
    assert "rien à changer" in result.stderr
    assert _no_patch(mock_api)


def test_update_os_asks_confirmation(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    result = runner.invoke(app, ["k8s", "update", CID, "--os", "flatcar"], input="n\n")
    assert result.exit_code != 0
    assert _no_patch(mock_api)


def test_update_without_option_does_nothing(runner, mock_api):
    result = runner.invoke(app, ["k8s", "update", CID])
    assert result.exit_code == 0
    assert "Aucun paramètre" in result.stderr
    assert not mock_api.calls


@pytest.mark.parametrize(("valeur", "needle"), [
    ("rocky9", "Rocky Linux 9 est retiré de la plateforme"),
    ("ROCKY9", "Rocky Linux 9 est retiré de la plateforme"),
    ("windows", "--os invalide"),
])
def test_update_os_refused_locally(runner, mock_api, valeur, needle):
    result = runner.invoke(app, ["k8s", "update", CID, "--os", valeur, "-y"])
    assert result.exit_code == 1
    err = _flat(result.stderr)
    assert needle in err
    assert "flatcar, ubuntu, rocky10" in err
    assert not mock_api.calls


def test_update_os_api_refusal(runner, mock_api):
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    mock_api.patch(BASE).mock(return_value=httpx.Response(422, json={
        "detail": "Aucun template Flatcar 1.34.8 utilisable dans la région RNN."}))
    result = runner.invoke(app, ["k8s", "update", CID, "--os", "flatcar", "-y"])
    assert result.exit_code == 1
    assert "Aucun template Flatcar" in result.stderr


def test_update_os_ignored_by_older_api_is_an_error(runner, mock_api):
    """Avant #2155, l'API ignore `os_image` dans le PATCH : pas de faux succès."""
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    _capture_patch(mock_api, payload=_cluster())
    result = runner.invoke(app, ["k8s", "update", CID, "--os", "flatcar", "-y"])
    assert result.exit_code == 1, result.output
    err = _flat(result.stderr)
    assert "n'a pas appliqué" in err and "#2155" in err
    assert "✓" not in result.output


def test_update_os_json_output(runner, mock_api, monkeypatch):
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster()))
    _capture_patch(mock_api)
    result = runner.invoke(app, ["k8s", "update", CID, "--os", "flatcar", "-y"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["os_image"] == "flatcar"
    assert "proxy_vip_vnet" not in data


def test_get_reads_rocky9_without_error(runner, mock_api):
    """Une valeur `rocky9` lue (retirée en écriture) s'affiche telle quelle."""
    mock_api.get(BASE).mock(return_value=httpx.Response(200, json=_cluster(os_image="rocky9")))
    mock_api.get("/v1/k8s/clusters/plans").mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(app, ["k8s", "get", CID])
    assert result.exit_code == 0, result.output
    assert "Rocky Linux 9" in result.stdout


def test_help_mentions_default_os_and_no_rocky9(runner):
    for args in (["k8s", "update", "--help"], ["k8s", "create", "--help"]):
        result = runner.invoke(app, args, env={"COLUMNS": "200", "NO_COLOR": "1"})
        assert result.exit_code == 0
        out = _flat(_ANSI.sub("", result.stdout))
        assert "nouveaux pools" in out
        assert "rocky9" not in out
