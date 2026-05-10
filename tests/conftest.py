"""Fixtures partagées pour tests CLI cetic.

Comprend :
    - `runner` : Typer CliRunner (mix_stderr=False)
    - `cfg_env` : env CCP_API_URL/CCP_API_KEY isolés
    - `respx_mock` : intercepte httpx (renommé `mock_api` pour clarté)
    - `mock_keyring` : trousseau in-memory (monkeypatch keyring globalement)
    - `mock_subprocess` : capture les `subprocess.run` (docker login)
"""

from __future__ import annotations

from typing import Any

import pytest
import respx
from typer.testing import CliRunner


API_URL = "https://api.test.cetic-group.com"


@pytest.fixture
def runner() -> CliRunner:
    # typer >= 0.13 a retiré `mix_stderr`. On garde le fallback pour les vieilles versions.
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


@pytest.fixture(autouse=True)
def cfg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolation : pas de fichier config, env contrôlé."""
    monkeypatch.setenv("CCP_API_URL", API_URL)
    monkeypatch.setenv("CCP_API_KEY", "test-token")
    monkeypatch.setenv("CCP_OUTPUT", "table")
    # Empêche la lecture du fichier user.
    monkeypatch.setattr(
        "cetic.config._load_file", lambda: {}, raising=True
    )


@pytest.fixture
def mock_api() -> Any:
    """Mock httpx via respx — yield le router pour ajouter des routes."""
    with respx.mock(base_url=API_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def mock_keyring(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Stub keyring en mémoire — survie au scope du test."""
    store: dict[str, str] = {}

    def _key(service: str, user: str) -> str:
        return f"{service}::{user}"

    def set_password(service: str, user: str, password: str) -> None:
        store[_key(service, user)] = password

    def get_password(service: str, user: str) -> str | None:
        return store.get(_key(service, user))

    def delete_password(service: str, user: str) -> None:
        store.pop(_key(service, user), None)

    # Patch le module keyring importé paresseusement dans _secrets.
    import keyring

    monkeypatch.setattr(keyring, "set_password", set_password)
    monkeypatch.setattr(keyring, "get_password", get_password)
    monkeypatch.setattr(keyring, "delete_password", delete_password)
    return store


@pytest.fixture
def mock_subprocess(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture subprocess.run — last_call dict accessible via la fixture."""
    last_call: dict[str, Any] = {}

    class _CompletedProcess:
        def __init__(self, returncode: int = 0, stdout: str = "Login Succeeded\n", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(*args: Any, **kwargs: Any) -> _CompletedProcess:
        last_call["args"] = args[0] if args else kwargs.get("args", [])
        last_call["input"] = kwargs.get("input")
        last_call["kwargs"] = kwargs
        rc = last_call.get("returncode_override", 0)
        return _CompletedProcess(returncode=rc, stdout="Login Succeeded\n", stderr="")

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("cetic.commands.registry.subprocess.run", fake_run)
    # Toujours présent : `which("docker")` doit renvoyer un chemin.
    monkeypatch.setattr("cetic.commands.registry.shutil.which", lambda _: "/usr/bin/docker")
    return last_call
