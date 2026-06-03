"""Tests config — nouveau chemin ~/.ccp/config + migration auto + priorité env.

Isolation : on patche ``pathlib.Path.home`` (→ tmp_path), ``platformdirs.user_config_dir``
(→ ancien chemin sous tmp_path) et on restaure le vrai ``_load_file`` (le conftest
autouse ``cfg_env`` le mocke pour neutraliser le fichier user — ici on veut le tester).
On purge aussi les env vars CCP_* posées par ``cfg_env`` pour tester réellement le fichier.
"""
from __future__ import annotations

import pytest

import cetic.config as config

# Capture la vraie fonction _load_file à l'import du module (avant tout mock conftest).
_real_load_file = config._load_file


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    """Isole config.py sur un HOME tmp_path, restaure le vrai _load_file,
    et retire les env vars de test du conftest."""
    home = tmp_path
    legacy_root = tmp_path / ".config"

    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(
        config.platformdirs,
        "user_config_dir",
        lambda app: str(legacy_root / app),
    )
    # Le conftest autouse mocke config._load_file → on remet la vraie implémentation.
    monkeypatch.setattr(config, "_load_file", _real_load_file)
    # Purge des env vars CCP_* posées par cfg_env (on teste le fichier).
    for var in ("CCP_API_KEY", "CCP_REGION", "CCP_OUTPUT", "CCP_LANG", "CCP_API_URL"):
        monkeypatch.delenv(var, raising=False)
    return config


def test_config_file_is_under_dot_ccp(cfg, tmp_path):
    assert cfg.config_file() == tmp_path / ".ccp" / "config"


def test_set_then_get_roundtrip(cfg):
    cfg.set_value("region", "PAR")
    assert cfg.get("region") == "PAR"
    # Fichier écrit au bon endroit, format TOML lisible.
    content = cfg.config_file().read_text()
    assert 'region = "PAR"' in content


def test_env_var_takes_priority_over_file(cfg, monkeypatch):
    cfg.set_value("region", "PAR")
    monkeypatch.setenv("CCP_REGION", "ABJ")
    assert cfg.get("region") == "ABJ"


def test_migration_from_legacy_path(cfg, tmp_path):
    # Simule l'ancien fichier ~/.config/cetic/config.toml
    legacy_dir = tmp_path / ".config" / "cetic"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.toml").write_text('api_key = "legacy-token"\nregion = "PAR"\n')
    # Le nouveau fichier n'existe pas encore.
    assert not cfg.config_file().exists()
    # Premier accès → migration auto.
    assert cfg.get("api_key") == "legacy-token"
    assert cfg.config_file().exists()
    assert "legacy-token" in cfg.config_file().read_text()
    # L'ancien fichier est laissé en place.
    assert (legacy_dir / "config.toml").exists()


def test_no_migration_when_new_exists(cfg, tmp_path):
    # Nouveau fichier déjà présent → on ne migre pas par-dessus.
    cfg.set_value("region", "RNN")
    legacy_dir = tmp_path / ".config" / "cetic"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.toml").write_text('region = "PAR"\n')
    assert cfg.get("region") == "RNN"  # le nouveau gagne


def test_config_view_detects_env_source(runner, monkeypatch):
    """`config view` doit afficher 'env' quand une var CCP_* est posée."""
    monkeypatch.setenv("CCP_REGION", "ABJ")
    from cetic.commands.config_cmd import app as config_app
    result = runner.invoke(config_app, ["view"])
    assert result.exit_code == 0, result.output
    # La région vient de l'env → ligne 'env' présente (au moins une fois).
    assert "env" in result.output
