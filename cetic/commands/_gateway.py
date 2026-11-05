"""Helpers partagés `cetic lb` / `cetic appgw` (#53, alignement cetic-cloud-platform#1579).

Page d'indisponibilité personnalisée, certificat fourni par le client et cibles
« scale set » dynamiques : les deux services exposent les mêmes champs, ce
module évite d'en écrire deux variantes qui divergeraient.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Plafond appliqué par l'API (`validate_custom_error_page`) : 7 Kio en octets
# UTF-8. Vérifié ici pour échouer avant l'appel, avec un message clair.
ERROR_PAGE_MAX_BYTES = 7 * 1024

# En dessous, l'échéance du certificat client est signalée (il ne se renouvelle
# pas tout seul, contrairement à Let's Encrypt).
CERT_WARN_DAYS = 30


def read_error_page(path: Path) -> str:
    """Lit la page HTML d'indisponibilité ; ValueError si elle sera refusée."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ValueError(f"Lecture impossible de « {path} » : {e.strerror}.") from e
    if len(raw) > ERROR_PAGE_MAX_BYTES:
        raise ValueError(
            f"La page fait {len(raw)} octets : le maximum est "
            f"{ERROR_PAGE_MAX_BYTES} octets (7 Kio)."
        )
    if b"\x00" in raw:
        raise ValueError("La page contient un octet nul : fichier HTML attendu.")
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError("La page doit être encodée en UTF-8.") from e
    if not html.strip():
        raise ValueError(
            "La page est vide. Pour revenir à la page par défaut, utilisez "
            "--reset-error-page."
        )
    return html


def read_cert_pair(cert_file: Path | None, key_file: Path | None) -> dict[str, str]:
    """Certificat (+ chaîne) et clé privée PEM → corps attendu par l'API."""
    if cert_file is None or key_file is None:
        raise ValueError("--cert-file et --key-file doivent être fournis ensemble.")
    out: dict[str, str] = {}
    for field, path in (("tls_certificate_pem", cert_file),
                        ("tls_private_key_pem", key_file)):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise ValueError(f"Lecture impossible de « {path} » : {e}.") from e
        if "-----BEGIN" not in text:
            raise ValueError(f"« {path} » n'est pas un fichier PEM.")
        out[field] = text
    if "ENCRYPTED" in out["tls_private_key_pem"]:
        raise ValueError(
            "La clé privée est protégée par une phrase secrète : fournissez-la "
            "non chiffrée (ex. `openssl pkey -in cle.pem -out cle-claire.pem`)."
        )
    return out


def error_page_label(html: str | None) -> str:
    return "personnalisée" if html else "par défaut"


def target_label(item: dict[str, Any]) -> str:
    """Libellé de la cible d'un backend LB / membre AppGW (une seule renseignée)."""
    if item.get("container_scale_set_id"):
        return f"scale set containers {item['container_scale_set_id']} (dynamique)"
    if item.get("vm_scale_set_id"):
        return f"scale set VM {item['vm_scale_set_id']} (dynamique)"
    if item.get("container_id"):
        return f"container {item['container_id']}"
    if item.get("vm_instance_id"):
        return f"VM {item['vm_instance_id']}"
    if item.get("target_ip"):
        label = item.get("target_label")
        return f"{item['target_ip']} ({label})" if label else str(item["target_ip"])
    return "—"


def cert_expiry_label(not_after: str | None, *, now: datetime | None = None) -> str:
    """Date d'expiration du certificat client, avec alerte sous 30 jours."""
    if not not_after:
        return "—"
    try:
        exp = datetime.fromisoformat(not_after)
    except ValueError:
        return not_after
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    days = (exp - (now or datetime.now(UTC))).days
    date = exp.date().isoformat()
    if days < 0:
        return f"[red]{date} (expiré)[/red]"
    if days < CERT_WARN_DAYS:
        return f"[yellow]{date} (⚠ {days} j)[/yellow]"
    return date


def network_label(item: dict[str, Any]) -> str:
    vpc = item.get("vpc_name") or item.get("vpc_id")
    vnet = item.get("vnet_name")
    cidr = item.get("vnet_cidr")
    parts = [p for p in (vpc, vnet) if p]
    if not parts:
        return "—"
    label = " / ".join(parts)
    return f"{label} ({cidr})" if cidr else label


def table_view(item: dict[str, Any]) -> dict[str, Any]:
    """Vue `table` d'un LB / d'une AppGW : la page HTML n'est pas déroulée.

    En JSON/YAML, la ressource est rendue telle que l'API la renvoie (HTML
    compris) : ce dictionnaire ne sert qu'à l'affichage tabulaire.
    """
    view = {k: v for k, v in item.items() if k not in ("listeners",)}
    if "custom_error_page_html" in view:
        view["custom_error_page_html"] = error_page_label(view["custom_error_page_html"])
    return view


def validate_created_replica_count(item: dict[str, Any], requested: int) -> None:
    """Signale une ancienne API qui ignore le mode demandé, en conservant l'ID."""
    actual = item.get("replica_count", 2)
    if actual != requested:
        raise ValueError(
            f"Ressource créée : {item.get('id', 'ID non retourné')}. "
            f"Le serveur n'a pas appliqué replica_count={requested} "
            f"(valeur retournée : {actual}). Mettez la plateforme à jour avant "
            "de recréer la ressource ; vérifiez celle déjà créée."
        )
