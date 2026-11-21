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


def test_login_sans_sso_explique_et_n_appelle_pas_l_api(runner, monkeypatch: pytest.MonkeyPatch):
    """cetic-cloud-platform#2004 : l'API refuse toujours en production un login
    par mot de passe sans jeton anti-robot (403). La CLI ne le tente plus : elle
    oriente vers le SSO ou une clé d'API, sans demander de mot de passe."""
    appels: list[str] = []
    monkeypatch.setattr(auth_cmd.client, "post", lambda path, **kw: appels.append(path))
    stored: dict[str, str] = {}
    monkeypatch.setattr(auth_cmd.config, "set_value",
                        lambda k, v: stored.__setitem__(k, v))

    result = runner.invoke(app, ["auth", "login"], input="a@b.c\nsecret\n")

    assert result.exit_code == 1
    sortie = result.stdout + result.stderr
    assert "--sso" in sortie
    assert "CCP_API_KEY" in sortie
    assert "Mot de passe" not in sortie
    assert appels == []
    assert stored == {}


# ── Revue de la PR #85 ───────────────────────────────────────────────────────


def test_login_sans_option_ecrit_sur_stderr(runner):
    result = runner.invoke(app, ["auth", "login"])
    assert result.exit_code == 1
    assert result.stdout.strip() == ""
    assert "--api-key" in result.stderr


def test_login_api_key_enregistre_la_cle_et_efface_le_refresh(runner, monkeypatch: pytest.MonkeyPatch):
    stored: dict[str, str] = {}
    monkeypatch.setattr(auth_cmd.config, "set_value", lambda k, v: stored.__setitem__(k, v))
    monkeypatch.delenv("CCP_API_KEY", raising=False)
    result = runner.invoke(app, ["auth", "login", "--api-key"], input="ccp_live_abc\n")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert stored == {"api_key": "ccp_live_abc", "refresh_token": ""}
    assert "CCP_API_KEY" not in result.stderr


def test_login_signale_que_ccp_api_key_prime(runner, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth_cmd.config, "set_value", lambda k, v: None)
    monkeypatch.setenv("CCP_API_KEY", "env-key")
    result = runner.invoke(app, ["auth", "login", "--api-key"], input="ccp_live_abc\n")
    assert result.exit_code == 0
    assert "CCP_API_KEY est définie" in result.stderr


def test_whoami_et_401_ne_renvoient_plus_vers_le_login_nu(runner, monkeypatch: pytest.MonkeyPatch):
    from cetic._auth_hint import NON_AUTHENTIFIE

    assert "--sso" in NON_AUTHENTIFIE and "--api-key" in NON_AUTHENTIFIE
    monkeypatch.setattr(auth_cmd.config, "get", lambda k: None)
    result = runner.invoke(app, ["auth", "whoami"])
    assert result.exit_code == 1
    assert "--sso" in result.stdout
