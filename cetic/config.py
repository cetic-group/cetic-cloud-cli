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
import re
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
        # Lire AVANT de créer : un ancien fichier illisible (encodage, droits)
        # ne doit pas laisser un fichier vide, qui empêcherait toute nouvelle
        # tentative. Écriture par temporaire 0600 puis `os.link`
        # (échoue si la cible existe, comme `O_EXCL`), donc jamais partielle.
        contenu = old.read_text(encoding="utf-8")
        dossier = config_dir()
        dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
        _restreindre(dossier, 0o700)
        _ecrire_atomique(new, contenu, remplacer=False)
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
    les droits de l'umask. Le reste du fichier est conservé tel quel
    (`_nouveau_contenu`)."""
    _migrate_legacy_if_needed()
    contenu = _nouveau_contenu(key, value)
    if contenu is None:
        # SystemExit : message sur stderr et code 1, sans trace Python.
        raise SystemExit(
            f"Erreur : {config_file()} ne peut pas être réécrit sans perdre des "
            f"valeurs (clé `{key}` sur plusieurs lignes ou entre guillemets ?). "
            "Rien n'a été modifié : corrigez le fichier à la main.")
    dossier = config_dir()
    dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
    _restreindre(dossier, 0o700)
    _ecrire_atomique(config_file(), contenu, remplacer=True)


def reecrivable(*keys: str) -> bool:
    """Vrai si `set_value` peut écrire ces clés. À vérifier AVANT un effet
    côté serveur qu'on ne peut pas rejouer : le refresh token tourne à
    chaque rafraîchissement, et un nouveau jeton non enregistré ferait
    rejouer l'ancien, ce que la plateforme traite comme un vol (toutes les
    sessions du compte révoquées)."""
    try:
        return all(_nouveau_contenu(k, "x") is not None for k in keys)
    except Exception:
        return False


def _nouveau_contenu(key: str, value: str) -> str | None:
    """Contenu du fichier avec `key = value`, ou None s'il ne peut pas être
    produit sans perdre de valeur.

    Seule la ligne `key = …` du niveau racine est remplacée (ou ajoutée) : les
    tables, dates et commentaires ajoutés à la main restent tels quels.
    `json.dumps` produit une chaîne TOML valide (un `"` ou un `\\` rendait le
    fichier illisible). Un fichier déjà illisible est remplacé, comme avant."""
    path = config_file()
    try:
        texte = path.read_text(encoding="utf-8") if path.exists() else ""
        avant = tomllib.loads(texte)
    except Exception:
        texte, avant = "", {}
    lignes = texte.splitlines()
    fin_racine = next(
        (i for i, ligne in enumerate(lignes) if ligne.lstrip().startswith("[")), len(lignes))
    motif = re.compile(rf"^\s*{re.escape(key)}\s*=")
    ligne = f"{key} = {json.dumps(value, ensure_ascii=False)}"
    idx = next((i for i in range(fin_racine) if motif.match(lignes[i])), None)
    if idx is None:
        while fin_racine > 0 and not lignes[fin_racine - 1].strip():
            fin_racine -= 1  # avant les lignes vides qui précèdent une table
        lignes.insert(fin_racine, ligne)
    else:
        lignes[idx] = ligne
    contenu = "\n".join(lignes) + "\n"
    # Contrôle : le résultat se relit à l'identique, la clé en plus. Sinon
    # (valeur sur plusieurs lignes, clé entre guillemets…), ne rien écrire.
    try:
        apres = tomllib.loads(contenu)
    except Exception:
        return None
    avant[key] = value
    return contenu if apres == avant else None


def _ecrire_atomique(chemin: Path, contenu: str, *, remplacer: bool) -> None:
    """Fichier temporaire 0600 puis `os.replace` (ou `os.link` sans
    remplacement). Une interruption ne laisse jamais de fichier partiel, et un
    lien symbolique posé sur le fichier n'est pas suivi."""
    tmp = chemin.with_name(f".{chemin.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contenu)
        if remplacer:
            os.replace(tmp, chemin)
        else:
            os.link(tmp, chemin)
            tmp.unlink()
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
