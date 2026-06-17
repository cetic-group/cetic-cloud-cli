"""Tests des options --cloud-init / --bastion-access / --template-source
sur les commandes de création compute (container, vm, vm-scale-set, ct-scale-set).

Issues : cetic-cloud-platform#343, cetic-cloud-cli#19.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cetic.main import app


PW = "supersecret"  # 8+ chars (politique CCP)

CLOUD_INIT = "#cloud-config\npackage_update: true\npackages:\n  - htop\n"


def _capture_post(mock_api, path: str, status: int = 201):
    """Installe un handler POST qui capture le body et renvoie un objet minimal."""
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(status, json={"id": "new-id", "name": "x", "status": "creating"})

    mock_api.post(path).mock(side_effect=_handler)
    return captured


# Matrice : (argv prefix, endpoint, requires vnet flag name, supports template_source)
CREATE_CASES = [
    (["container", "create"], "/v1/containers", "--vnet", True),
    (["vm", "create"], "/v1/vm-instances", "--vnet", True),
    (["vm-scale-set", "create"], "/v1/vm-scale-sets", "--vnet", False),
    (["ct-scale-set", "create"], "/v1/container-scale-sets", "--vnet", False),
]


def _base_args(prefix: list[str], vnet_flag: str) -> list[str]:
    return [
        *prefix,
        "--name", "demo",
        "--region", "PAR",
        vnet_flag, "vnet-123",
        "--root-password", PW,
    ]


@pytest.mark.parametrize("prefix,endpoint,vnet_flag,_ts", CREATE_CASES)
def test_cloud_init_reads_file_into_user_data(
    runner, mock_api, tmp_path, prefix, endpoint, vnet_flag, _ts
):
    ci = tmp_path / "ci.yaml"
    ci.write_text(CLOUD_INIT, encoding="utf-8")
    captured = _capture_post(mock_api, endpoint)

    result = runner.invoke(
        app, [*_base_args(prefix, vnet_flag), "--cloud-init", str(ci)]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["user_data"] == CLOUD_INIT


@pytest.mark.parametrize("prefix,endpoint,vnet_flag,_ts", CREATE_CASES)
def test_bastion_access_flag_sets_field(
    runner, mock_api, prefix, endpoint, vnet_flag, _ts
):
    captured = _capture_post(mock_api, endpoint)
    result = runner.invoke(
        app, [*_base_args(prefix, vnet_flag), "--bastion-access"]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["bastion_access"] is True


@pytest.mark.parametrize("prefix,endpoint,vnet_flag,_ts", CREATE_CASES)
def test_options_absent_by_default(
    runner, mock_api, prefix, endpoint, vnet_flag, _ts
):
    captured = _capture_post(mock_api, endpoint)
    result = runner.invoke(app, _base_args(prefix, vnet_flag))
    assert result.exit_code == 0, result.stdout
    body = captured["body"]
    assert "user_data" not in body
    assert "bastion_access" not in body
    assert "is_template_source" not in body


@pytest.mark.parametrize("prefix,endpoint,vnet_flag,_ts", CREATE_CASES)
def test_cloud_init_missing_file_errors(
    runner, mock_api, prefix, endpoint, vnet_flag, _ts
):
    # Aucun handler POST : la commande doit échouer avant tout appel réseau.
    result = runner.invoke(
        app, [*_base_args(prefix, vnet_flag), "--cloud-init", "/does/not/exist.yaml"]
    )
    assert result.exit_code != 0
    assert not any(call.request.method == "POST" for call in mock_api.calls)


# --template-source : uniquement vm + container (sans objet pour un scale-set).


@pytest.mark.parametrize(
    "prefix,endpoint",
    [(["container", "create"], "/v1/containers"), (["vm", "create"], "/v1/vm-instances")],
)
def test_template_source_flag_sets_field(runner, mock_api, prefix, endpoint):
    captured = _capture_post(mock_api, endpoint)
    result = runner.invoke(
        app, [*_base_args(prefix, "--vnet"), "--template-source"]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["is_template_source"] is True


@pytest.mark.parametrize("prefix", [["vm-scale-set", "create"], ["ct-scale-set", "create"]])
def test_template_source_not_available_on_scale_sets(runner, mock_api, prefix):
    result = runner.invoke(
        app, [*_base_args(prefix, "--vnet"), "--template-source"]
    )
    # Option inconnue → Typer renvoie une erreur d'usage (exit code 2).
    assert result.exit_code != 0


# --windows-license-consent : VM + VM scale set (templates Windows).

WIN_PW = "Str0ng-P@ssw0rd!"  # ≥ 12 chars, 4 catégories

WINDOWS_CASES = [
    (["vm", "create"], "/v1/vm-instances"),
    (["vm-scale-set", "create"], "/v1/vm-scale-sets"),
]


def _win_args(prefix: list[str], password: str = WIN_PW) -> list[str]:
    return [
        *prefix,
        "--name", "win",
        "--region", "PAR",
        "--plan", "medium",
        "--template", "win-2022",
        "--vnet", "vnet-123",
        "--root-password", password,
    ]


@pytest.mark.parametrize("prefix,endpoint", WINDOWS_CASES)
def test_windows_license_consent_flag_sets_field(runner, mock_api, prefix, endpoint):
    captured = _capture_post(mock_api, endpoint)
    result = runner.invoke(
        app, [*_win_args(prefix), "--windows-license-consent"]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"]["windows_license_consent"] is True


@pytest.mark.parametrize("prefix,endpoint", WINDOWS_CASES)
def test_windows_license_consent_absent_by_default(runner, mock_api, prefix, endpoint):
    captured = _capture_post(mock_api, endpoint)
    result = runner.invoke(app, _win_args(prefix))
    assert result.exit_code == 0, result.stdout
    assert "windows_license_consent" not in captured["body"]


@pytest.mark.parametrize("prefix,endpoint", WINDOWS_CASES)
def test_windows_weak_password_rejected_locally(runner, mock_api, prefix, endpoint):
    # Mot de passe ≥ 8 (passe la garde Linux) mais faible pour Windows
    # (< 12 chars / < 3 catégories) → rejet local AVANT tout appel réseau.
    captured = _capture_post(mock_api, endpoint)
    result = runner.invoke(
        app, [*_win_args(prefix, password="weakpass1"), "--windows-license-consent"]
    )
    assert result.exit_code != 0
    assert "body" not in captured
    assert not any(call.request.method == "POST" for call in mock_api.calls)
