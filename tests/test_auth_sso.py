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


CLE = "ccp_live_" + "A1b2_C3d4-" * 4


def _cle_valide(mock_api, cle: str = CLE):  # noqa: ANN001, ANN202
    import httpx

    def _repondre(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {cle}":
            return httpx.Response(401, json={"detail": "Token invalide"})
        return httpx.Response(200, json={"email": "alice@exemple.fr"})

    return mock_api.get("/v1/tenants/me").mock(side_effect=_repondre)


def test_login_api_key_enregistre_la_cle_et_efface_le_refresh(
        runner, mock_api, monkeypatch: pytest.MonkeyPatch):
    stored: dict[str, str] = {}
    monkeypatch.setattr(auth_cmd.config, "set_value", lambda k, v: stored.__setitem__(k, v))
    monkeypatch.delenv("CCP_API_KEY", raising=False)
    route = _cle_valide(mock_api)
    result = runner.invoke(app, ["auth", "login", "--api-key"], input=f"{CLE}\n")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert route.called  # vérifiée AVANT d'être écrite
    assert stored == {"api_key": CLE, "refresh_token": ""}
    assert "alice@exemple.fr" in result.stdout
    assert "CCP_API_KEY" not in result.stderr


def test_login_signale_que_ccp_api_key_prime(runner, mock_api, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth_cmd.config, "set_value", lambda k, v: None)
    monkeypatch.setenv("CCP_API_KEY", "env-key")
    _cle_valide(mock_api)
    result = runner.invoke(app, ["auth", "login", "--api-key"], input=f"{CLE}\n")
    assert result.exit_code == 0
    assert "CCP_API_KEY est définie" in result.stderr


def test_login_api_key_refusee_n_ecrit_rien(runner, mock_api, monkeypatch: pytest.MonkeyPatch):
    """Clé tronquée, révoquée ou mal tapée : refusée par l'API, rien n'est
    écrit (avant, l'erreur n'apparaissait qu'à la commande suivante)."""
    stored: dict[str, str] = {}
    monkeypatch.setattr(auth_cmd.config, "set_value", lambda k, v: stored.__setitem__(k, v))
    _cle_valide(mock_api, cle="une-autre-cle-valide-xyz")
    result = runner.invoke(app, ["auth", "login", "--api-key"], input=f"{CLE}\n")
    assert result.exit_code == 1
    assert "refusée" in result.stderr
    assert stored == {}


def test_login_api_key_forme_invalide_sans_appel(runner, mock_api, monkeypatch: pytest.MonkeyPatch):
    """Un `"` collé avec la clé aurait cassé le fichier de configuration."""
    stored: dict[str, str] = {}
    monkeypatch.setattr(auth_cmd.config, "set_value", lambda k, v: stored.__setitem__(k, v))
    route = _cle_valide(mock_api)
    result = runner.invoke(app, ["auth", "login", "--api-key"], input=f'{CLE}"\n')
    assert result.exit_code == 1
    assert not route.called
    assert stored == {}


def test_login_api_key_vide(runner, monkeypatch: pytest.MonkeyPatch):
    stored: dict[str, str] = {}
    monkeypatch.setattr(auth_cmd.config, "set_value", lambda k, v: stored.__setitem__(k, v))
    result = runner.invoke(app, ["auth", "login", "--api-key"], input="\n")
    assert result.exit_code == 1
    assert "Aucune clé" in result.stderr
    assert stored == {}


def test_sso_et_api_key_exclusifs(runner, monkeypatch: pytest.MonkeyPatch):
    appels: list[str] = []
    monkeypatch.setattr(auth_cmd, "_login_sso", lambda p: appels.append(p))
    monkeypatch.setattr(auth_cmd, "_login_api_key", lambda: appels.append("cle"))
    result = runner.invoke(app, ["auth", "login", "--sso", "github", "--api-key"])
    assert result.exit_code == 2
    assert "exclusifs" in result.stderr
    assert appels == []


def test_whoami_et_401_ne_renvoient_plus_vers_le_login_nu(runner, monkeypatch: pytest.MonkeyPatch):
    from cetic._auth_hint import NON_AUTHENTIFIE

    assert "--sso" in NON_AUTHENTIFIE and "--api-key" in NON_AUTHENTIFIE
    monkeypatch.setattr(auth_cmd.config, "get", lambda k: None)
    result = runner.invoke(app, ["auth", "whoami"])
    assert result.exit_code == 1
    assert "--sso" in result.stderr
    assert result.stdout.strip() == ""


# ── Fichier de configuration (revue de la PR #85) ────────────────────────────


def test_fichier_de_config_prive_et_valeurs_echappees(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Le fichier porte une clé qui n'expire pas : 0600 (dossier 0700), même
    s'il existait en 0644. Une valeur avec `"` ou `\\` reste lisible, et les
    autres réglages survivent."""
    import os
    import stat
    import tomllib

    from cetic import config

    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / ".ccp")
    monkeypatch.setattr(config, "config_file", lambda: tmp_path / ".ccp" / "config")
    monkeypatch.setattr(config, "_load_file", lambda: (
        tomllib.loads((tmp_path / ".ccp" / "config").read_text())
        if (tmp_path / ".ccp" / "config").exists() else {}))
    (tmp_path / ".ccp").mkdir(mode=0o755)
    (tmp_path / ".ccp" / "config").write_text('region = "PAR"\n')
    os.chmod(tmp_path / ".ccp" / "config", 0o644)

    config.set_value("api_key", 'ab"c\\d')

    assert stat.S_IMODE((tmp_path / ".ccp" / "config").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / ".ccp").stat().st_mode) == 0o700
    data = tomllib.loads((tmp_path / ".ccp" / "config").read_text())
    assert data == {"region": "PAR", "api_key": 'ab"c\\d'}


def test_pas_de_rafraichissement_quand_la_cle_vient_de_l_environnement(monkeypatch: pytest.MonkeyPatch):
    """Un 401 sur la clé de `CCP_API_KEY` ne déclenche pas de rafraîchissement,
    qui réécrirait un fichier masqué."""
    from cetic import client

    monkeypatch.setattr(client.config, "get", lambda k: "rt" if k == "refresh_token" else None)
    monkeypatch.setattr(client.config, "set_value", lambda k, v: pytest.fail("fichier réécrit"))
    monkeypatch.setenv("CCP_API_KEY", "env-key")
    assert client._try_refresh() is False
