"""Tests pour `cetic auth login --sso`.

Le flux complet (navigateur + serveur loopback) n'est pas simulé ici ; on
vérifie le garde-fou de provider et le stockage des tokens via un faux flux SSO.
"""
from __future__ import annotations

import pytest

from cetic.commands import auth as auth_cmd
from cetic.main import app


def test_sso_unknown_provider_rejected(runner):
    result = runner.invoke(app, ["auth", "login", "--sso", "facebook"])
    assert result.exit_code == 1
    assert "inconnu" in (result.stdout + result.stderr)


def test_sso_stores_tokens(runner, monkeypatch: pytest.MonkeyPatch):
    """Simule un callback loopback réussi → tokens stockés via config.set_value."""
    stored: dict[str, str] = {}
    monkeypatch.setattr(auth_cmd.config, "set_value",
                        lambda k, v: stored.__setitem__(k, v))

    def fake_login_sso(provider: str) -> None:
        assert provider == "github"
        auth_cmd._store_tokens("acc-tok", "ref-tok")

    monkeypatch.setattr(auth_cmd, "_login_sso", fake_login_sso)
    result = runner.invoke(app, ["auth", "login", "--sso", "github"])
    assert result.exit_code == 0
    assert stored["api_key"] == "acc-tok"
    assert stored["refresh_token"] == "ref-tok"


def test_store_tokens_skips_empty_refresh(monkeypatch: pytest.MonkeyPatch):
    stored: dict[str, str] = {}
    monkeypatch.setattr(auth_cmd.config, "set_value",
                        lambda k, v: stored.__setitem__(k, v))
    auth_cmd._store_tokens("acc-only", None)
    assert stored == {"api_key": "acc-only"}
