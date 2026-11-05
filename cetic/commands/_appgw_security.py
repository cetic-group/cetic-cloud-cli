"""Transport des politiques d'accès AppGW (#1828), sans interpolation de code."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich import print as rprint

from cetic import client

LIMIT = 65536
FIELDS = frozenset({"l4_policy", "l7_policy", "client_ip_policy"})


def _fail(message: str) -> NoReturn:
    rprint(f"[red]Erreur : {message}[/red]")
    raise typer.Exit(1)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def read_policy(path: Path) -> dict[str, Any]:
    """Lecture bornée, JSON strict ; aucun contenu de fichier dans les erreurs."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("oversized")
        def constant(_value):
            raise ValueError("non-finite number")
        def finite_float(raw_value):
            value = float(raw_value)
            if not math.isfinite(value):
                raise ValueError("non-finite number")
            return value
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=constant, parse_float=finite_float)
        if not isinstance(value, dict):
            raise TypeError("object required")
        # json.loads accepts isolated UTF-16 surrogate escapes. HTTPX encodes
        # JSON as UTF-8, which would otherwise raise an uncontrolled exception
        # later, with the policy body available in traceback locals.
        json.dumps(value, ensure_ascii=False).encode("utf-8")
        return value
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        _fail("Politique illisible : objet JSON UTF-8 strict de 64 Kio maximum requis, sans clé dupliquée.")


def policy_payload(**options) -> dict[str, dict]:
    """Un champ omis n'est jamais transformé en remise à zéro."""
    result = {}
    for field, (file, clear) in options.items():
        if file is not None and clear:
            _fail("Le fichier de politique et sa suppression explicite sont incompatibles.")
        if file is not None:
            result[field] = read_policy(file)
        elif clear:
            result[field] = {}
    return result


def prepare_policy_write(body: dict, gateway_id: str | None = None, *, updating: bool = False) -> None:
    if not FIELDS.intersection(body):
        return
    capability = client.get("/v1/platform/capabilities")
    versions = capability.get("appgw_security_policy_versions", []) if isinstance(capability, dict) else []
    if not isinstance(versions, list) or not any(type(version) is int and version == 1 for version in versions):
        _fail("Cette installation ne prend pas en charge la version 1 des politiques d’accès.")
    if capability.get("appgw_security_policies_enabled") is False and any(body[field] for field in FIELDS.intersection(body)):
        _fail("Les nouvelles politiques sont désactivées sur cette installation ; leur suppression reste disponible.")
    if updating:
        gateway = client.get(f"/v1/app-gateways/{gateway_id}")
        revision = gateway.get("access_policy_generation") if isinstance(gateway, dict) else None
        if type(revision) is not int or revision < 0:
            _fail("La révision de sécurité n’est pas disponible ; aucune modification envoyée.")
        body["security_revision"] = revision


def _normalized_policy(value: Any, field: str = "") -> Any:
    """Compare the full replacement with server defaults/canonical values removed."""
    import ipaddress
    from urllib.parse import quote_from_bytes, unquote_to_bytes

    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "name" and isinstance(item, str):
                item = item.lower()
            if key == "value" and value.get("op") in {"exact", "segment_prefix"} and isinstance(item, str):
                item = quote_from_bytes(unquote_to_bytes(item), safe="/-._~")
            item = _normalized_policy(item, key)
            if item is None or item is False or item == [] or item == {}:
                continue
            if key == "mode" and item == "peer":
                continue
            result[key] = item
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            if field in {"allow_cidrs", "deny_cidrs", "source_cidrs", "trusted_proxy_cidrs"}:
                item = str(ipaddress.ip_network(item, strict=False))
            item = _normalized_policy(item)
            if item not in result:
                result.append(item)
        return result
    return value


def verify_policy_response(body: dict, response: object) -> None:
    """Détecte notamment une API ancienne qui ignore silencieusement le champ."""
    fields = FIELDS.intersection(body)
    if not fields:
        return
    if (not isinstance(response, dict) or type(response.get("security_revision")) is not int
            or any(not isinstance(response.get(field), dict) for field in fields)):
        _fail("La réponse ne confirme pas les politiques enregistrées. Relisez la passerelle avant de réessayer.")
    for field in fields:
        try:
            requested = json.dumps(_normalized_policy(body[field]), sort_keys=True)
            received = json.dumps(_normalized_policy(response[field]), sort_keys=True)
        except (TypeError, ValueError):
            _fail("La réponse contient une politique invalide ; relisez la passerelle.")
        if requested != received:
            _fail("La réponse ne confirme pas la politique demandée. Relisez la passerelle avant de réessayer.")
    rprint("Politique enregistrée ; son application est suivie dans l’état de sécurité de la passerelle.")


def policy_bail(error: client.APIError) -> typer.Exit:
    """Policy failures never echo server details that may contain header values."""
    messages = {
        401: "Non authentifié — vérifiez `cetic auth login` ou `CCP_API_KEY`.",
        403: "Accès refusé — droits insuffisants pour cette opération.",
        404: "Ressource introuvable.",
        409: "La configuration de sécurité a changé ou n’est pas disponible. Relisez la passerelle et vérifiez vos modifications avant de réessayer manuellement.",
        422: "Politique refusée : vérifiez ses critères, ses limites et sa compatibilité avec les certificats et le cache.",
    }
    message = messages.get(error.status_code, "La demande de politique a échoué. Relisez la passerelle avant de réessayer.")
    rprint(f"[red]Erreur : {message}[/red]")
    return typer.Exit(1)
