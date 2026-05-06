"""Gestion de la configuration CLI CETIC Cloud Platform.

Stockage : fichier TOML dans le répertoire de config utilisateur.
  Linux   : ~/.config/cetic/config.toml
  macOS   : ~/Library/Application Support/cetic/config.toml
  Windows : %APPDATA%/cetic/config.toml

Variables d'environnement (prioritaires sur le fichier) :
  CCP_API_KEY    — clé API CETIC Cloud
  CCP_REGION     — région active (RNN | PAR | ABJ)
  CCP_OUTPUT     — format de sortie (table | json | yaml)
  CCP_LANG       — langue (fr | en)
  CCP_API_URL    — surcharge URL API (dev uniquement)
"""

import os
from pathlib import Path
from typing import Any

import platformdirs

try:
    import tomllib
except ImportError:
    import tomllib  # type: ignore[no-redef]

_APP_NAME = "cetic"
_DEFAULT_API_URL = "https://api.cloud.cetic-group.com"

VALID_REGIONS = ("RNN", "PAR", "ABJ")
VALID_OUTPUTS = ("table", "json", "yaml")
VALID_LANGS = ("fr", "en")


def config_dir() -> Path:
    return Path(platformdirs.user_config_dir(_APP_NAME))


def config_file() -> Path:
    return config_dir() / "config.toml"


def _load_file() -> dict[str, Any]:
    path = config_file()
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get(key: str) -> str | None:
    """Retourne la valeur d'une clé (env var prioritaire sur fichier)."""
    env_map = {
        "api_key": "CCP_API_KEY",
        "region": "CCP_REGION",
        "output": "CCP_OUTPUT",
        "lang": "CCP_LANG",
        "api_url": "CCP_API_URL",
    }
    env_val = os.environ.get(env_map.get(key, ""))
    if env_val:
        return env_val
    return _load_file().get(key)


def set_value(key: str, value: str) -> None:
    """Persiste une clé dans le fichier de config TOML."""
    config_dir().mkdir(parents=True, exist_ok=True)
    data = _load_file()
    data[key] = value
    lines = [f'{k} = "{v}"' for k, v in data.items()]
    config_file().write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_api_url() -> str:
    return get("api_url") or _DEFAULT_API_URL


def get_region() -> str:
    return get("region") or "RNN"


def get_output() -> str:
    return get("output") or "table"


def get_lang() -> str:
    return get("lang") or "fr"


def view_all() -> dict[str, str | None]:
    return {
        "api_url": get_api_url(),
        "region": get_region(),
        "output": get_output(),
        "lang": get_lang(),
        "api_key": "***" if get("api_key") else None,
    }
