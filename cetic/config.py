"""Gestion de la configuration CLI CETIC Cloud Platform.

Stockage : fichier TOML dans ``~/.ccp/config`` (même chemin sur tous les OS).

Migration auto : au premier accès, si ``~/.ccp/config`` n'existe pas mais que
l'ancien fichier ``~/.config/cetic/config.toml`` (ou équivalent platformdirs)
existe, son contenu est copié vers le nouveau chemin. L'ancien fichier est
laissé en place (plus jamais lu).

Variables d'environnement (prioritaires sur le fichier) :
  CCP_API_KEY    — clé API CETIC Cloud
  CCP_REGION     — région active (RNN | PAR | ABJ)
  CCP_OUTPUT     — format de sortie (table | json | yaml)
  CCP_LANG       — langue (fr | en)
  CCP_API_URL    — surcharge URL API (dev uniquement)
"""

import json
import os
import stat
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
    return Path.home() / ".ccp"


def config_file() -> Path:
    return config_dir() / "config"


def _legacy_config_file() -> Path:
    """Ancien emplacement (platformdirs) — lu une seule fois pour migration."""
    return Path(platformdirs.user_config_dir(_APP_NAME)) / "config.toml"


def _migrate_legacy_if_needed() -> None:
    """Copie l'ancien fichier vers ~/.ccp/config au premier accès (best-effort)."""
    new = config_file()
    if new.exists():
        return
    old = _legacy_config_file()
    if not old.exists():
        return
    try:
        # Le fichier porte jeton d'accès et refresh token : 0700 / 0600 dès la
        # copie, pas à la prochaine écriture.
        dossier = config_dir()
        dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
        _restreindre(dossier, 0o700)
        fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(old.read_text(encoding="utf-8"))
        _restreindre(new, 0o600)
    except Exception:
        pass  # best-effort — un échec de migration ne bloque pas la CLI


def _load_file() -> dict[str, Any]:
    _migrate_legacy_if_needed()
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
    """Persiste une clé dans le fichier de config TOML.

    Le fichier peut porter une clé d'API qui n'expire pas (`cetic auth login
    --api-key`) : dossier en 0700, fichier en 0600, même s'ils existaient avec
    les droits de l'umask. Les valeurs sont échappées
    (`json.dumps` produit une chaîne TOML valide) : un `"` ou un `\\` rendait
    le fichier illisible, et le poste perdait TOUTE sa configuration."""
    dossier = config_dir()
    dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
    _restreindre(dossier, 0o700)
    data = _load_file()
    data[key] = value
    lines = [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in data.items()]
    chemin = config_file()
    # Écriture atomique : fichier temporaire 0600 puis `os.replace`. Une
    # interruption après `O_TRUNC` perdait toute la configuration, et un lien
    # symbolique posé sur le fichier était suivi.
    tmp = chemin.with_name(f".{chemin.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, chemin)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _restreindre(chemin, 0o600)


def _restreindre(chemin: Path, mode: int) -> None:
    """Droits du propriétaire seul (best-effort : sans effet sous Windows)."""
    try:
        if stat.S_IMODE(chemin.stat().st_mode) != mode:
            chemin.chmod(mode)
    except OSError:
        pass


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
