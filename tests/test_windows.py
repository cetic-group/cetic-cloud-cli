"""Tests pour `cetic windows` (list/get/create/delete/start/stop/reboot/plans/templates/credentials)."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cetic.main import app


WINDOWS_ID = "ffffffff-1111-2222-3333-444444444444"
VNET_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _windows(
    wid: str = WINDOWS_ID,
    name: str = "win-prod",
    region: str = "PAR",
    status: str = "running",
    ip: str = "10.0.1.42",
) -> dict[str, Any]:
    return {
        "id": wid,
        "name": name,
        "region": region,
        "plan": "standard.2c4g",
        "status": status,
        "ip_address": ip,
        "template_key": "windows-server-2022",
        "created_at": "2026-06-09T10:00:00Z",
    }


# ---------------------------------------------------------------------------
# Enregistrement des sous-apps
# ---------------------------------------------------------------------------


def test_windows_subapp_registered():
    names = [g.name for g in app.registered_groups]
    assert "windows" in names


# ---------------------------------------------------------------------------
# list / get
# ---------------------------------------------------------------------------


def test_list_table(runner, mock_api):
    mock_api.get("/v1/windows-instances").mock(
        return_value=httpx.Response(200, json=[_windows(), _windows(name="win-dev")])
    )
    result = runner.invoke(app, ["windows", "list"])
    assert result.exit_code == 0, result.stdout
    assert "win-prod" in result.stdout
    assert "PAR" in result.stdout


def test_list_with_region_filter(runner, mock_api):
    mock_api.get("/v1/windows-instances", params={"region": "RNN"}).mock(
        return_value=httpx.Response(200, json=[_windows(region="RNN")])
    )
    result = runner.invoke(app, ["windows", "list", "--region", "RNN"])
    assert result.exit_code == 0, result.stdout
    assert "RNN" in result.stdout


def test_get(runner, mock_api):
    mock_api.get(f"/v1/windows-instances/{WINDOWS_ID}").mock(
        return_value=httpx.Response(200, json=_windows())
    )
    result = runner.invoke(app, ["windows", "get", WINDOWS_ID])
    assert result.exit_code == 0, result.stdout
    assert "win-prod" in result.stdout


def test_get_404(runner, mock_api):
    mock_api.get(f"/v1/windows-instances/{WINDOWS_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(app, ["windows", "get", WINDOWS_ID])
    assert result.exit_code == 1
    assert "Erreur" in result.stdout


# ---------------------------------------------------------------------------
# create — license gate
# ---------------------------------------------------------------------------


def test_create_without_accept_license_exits_error(runner, mock_api):
    """Sans --accept-license, la commande doit refuser et afficher l'avertissement."""
    result = runner.invoke(
        app,
        [
            "windows", "create",
            "--name", "win-test",
            "--region", "PAR",
            "--plan", "standard.2c4g",
            "--template", "windows-server-2022",
        ],
        input="password123456\npassword123456\n",  # Simule les prompts du mot de passe
    )
    assert result.exit_code == 1
    assert "Licence Windows non incluse" in result.stdout or "CETIC Cloud Platform" in result.stdout
    # Vérifier qu'aucun POST n'a été effectué
    assert not any(call.request.method == "POST" for call in mock_api.calls)


def test_create_sends_correct_body(runner, mock_api):
    """Vérifie que le body contient license_consent et template_key."""
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_windows())

    mock_api.post("/v1/windows-instances").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "windows", "create",
            "--name", "win-test",
            "--region", "PAR",
            "--plan", "standard.2c4g",
            "--template", "windows-server-2022",
            "--vnet", VNET_ID,
            "--accept-license",
        ],
        input="password123456\npassword123456\n",
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["license_consent"] is True
    assert captured["body"]["template_key"] == "windows-server-2022"
    assert captured["body"]["administrator_password"] == "password123456"
    assert captured["body"]["vnet_id"] == VNET_ID


def test_create_with_data_volumes(runner, mock_api):
    """Vérifie que data_volume_ids est bien envoyé."""
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_windows())

    mock_api.post("/v1/windows-instances").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "windows", "create",
            "--name", "win-test",
            "--region", "PAR",
            "--plan", "standard.2c4g",
            "--template", "windows-server-2022",
            "--data-volume", "vol1",
            "--data-volume", "vol2",
            "--accept-license",
        ],
        input="password123456\npassword123456\n",
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["data_volume_ids"] == ["vol1", "vol2"]


def test_create_rejects_too_many_data_volumes(runner, mock_api):
    """Rejette plus de 5 disques data."""
    result = runner.invoke(
        app,
        [
            "windows", "create",
            "--name", "win-test",
            "--region", "PAR",
            "--plan", "standard.2c4g",
            "--template", "windows-server-2022",
            "--data-volume", "vol1",
            "--data-volume", "vol2",
            "--data-volume", "vol3",
            "--data-volume", "vol4",
            "--data-volume", "vol5",
            "--data-volume", "vol6",
            "--accept-license",
        ],
        input="password123456\npassword123456\n",
    )
    # BadParameter raises exit code 2
    assert result.exit_code == 2, f"Expected exit 2, got {result.exit_code}: {result.stdout}"
    # No API call should have been made (error caught before POST)
    assert not any(call.request.method == "POST" for call in mock_api.calls)


def test_create_with_tags(runner, mock_api):
    """Vérifie que les tags sont envoyés."""
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_windows())

    mock_api.post("/v1/windows-instances").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        [
            "windows", "create",
            "--name", "win-test",
            "--region", "PAR",
            "--plan", "standard.2c4g",
            "--template", "windows-server-2022",
            "--tag", "env=prod",
            "--tag", "team=infra",
            "--accept-license",
        ],
        input="password123456\npassword123456\n",
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["tags"] == ["env=prod", "team=infra"]


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_success(runner, mock_api):
    mock_api.delete(f"/v1/windows-instances/{WINDOWS_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["windows", "delete", WINDOWS_ID, "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "supprimée" in result.stdout


def test_delete_aborted_without_yes(runner, mock_api):
    result = runner.invoke(app, ["windows", "delete", WINDOWS_ID], input="n\n")
    assert result.exit_code != 0
    assert not any(call.request.method == "DELETE" for call in mock_api.calls)


# ---------------------------------------------------------------------------
# start / stop / reboot
# ---------------------------------------------------------------------------


def test_start(runner, mock_api):
    mock_api.post(f"/v1/windows-instances/{WINDOWS_ID}/start").mock(
        return_value=httpx.Response(202)
    )
    result = runner.invoke(app, ["windows", "start", WINDOWS_ID])
    assert result.exit_code == 0, result.stdout
    assert "Démarrage" in result.stdout


def test_stop(runner, mock_api):
    mock_api.post(f"/v1/windows-instances/{WINDOWS_ID}/stop").mock(
        return_value=httpx.Response(202)
    )
    result = runner.invoke(app, ["windows", "stop", WINDOWS_ID])
    assert result.exit_code == 0, result.stdout
    assert "Arrêt" in result.stdout


def test_reboot(runner, mock_api):
    mock_api.post(f"/v1/windows-instances/{WINDOWS_ID}/reboot").mock(
        return_value=httpx.Response(202)
    )
    result = runner.invoke(app, ["windows", "reboot", WINDOWS_ID])
    assert result.exit_code == 0, result.stdout
    assert "Redémarrage" in result.stdout


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------


def test_plans(runner, mock_api):
    mock_api.get("/v1/compute/plans", params={"kind": "windows"}).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "standard.2c4g",
                    "name": "Standard 2C/4GB",
                    "family": "standard",
                    "cores": 2,
                    "memory_mb": 4096,
                    "disk_gb": 100,
                    "price_eur_month": 50.0,
                    "is_default": True,
                },
                {
                    "key": "standard.4c8g",
                    "name": "Standard 4C/8GB",
                    "family": "standard",
                    "cores": 4,
                    "memory_mb": 8192,
                    "disk_gb": 100,
                    "price_eur_month": 100.0,
                    "is_default": False,
                },
            ],
        )
    )
    result = runner.invoke(app, ["windows", "plans"])
    assert result.exit_code == 0, result.stdout
    assert "standard.2c4g" in result.stdout
    assert "Standard 2C/4GB" in result.stdout


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------


def test_templates(runner, mock_api):
    mock_api.get("/v1/windows-instances/templates").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "windows-server-2022",
                    "display_name": "Windows Server 2022",
                    "dockur_version": "v8.1.0",
                },
                {
                    "key": "windows-10",
                    "display_name": "Windows 10",
                    "dockur_version": "v8.1.0",
                },
            ],
        )
    )
    result = runner.invoke(app, ["windows", "templates"])
    assert result.exit_code == 0, result.stdout
    assert "windows-server-2022" in result.stdout
    assert "Windows Server 2022" in result.stdout
    assert "v8.1.0" in result.stdout


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


def test_credentials(runner, mock_api):
    mock_api.get(f"/v1/windows-instances/{WINDOWS_ID}/credentials").mock(
        return_value=httpx.Response(
            200,
            json={"username": "Administrator", "password": "SecureP@ssw0rd!"},
        )
    )
    result = runner.invoke(app, ["windows", "credentials", WINDOWS_ID])
    assert result.exit_code == 0, result.stdout
    assert "Administrator" in result.stdout
    assert "SecureP@ssw0rd!" in result.stdout
