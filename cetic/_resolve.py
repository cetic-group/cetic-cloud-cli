"""Helper `resolve_id(resource, id_or_name)` réutilisable.

La spec CLI CCR demande que toutes les commandes acceptent indifféremment un
UUID OU un `name` comme premier argument. Cette fonction tente d'abord un GET
direct (UUID), puis fallback sur une recherche par nom.

Extension IAM v1 : `resolve_principal(type, id_or_name)` résout un principal
(api_key par préfixe, service_account par nom, org_member par email, ccks_workload
par UUID uniquement) en UUID.
"""

from __future__ import annotations

import re

import typer
from rich import print as rprint

from cetic import client

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def looks_like_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


def resolve_id(collection_path: str, id_or_name: str) -> str:
    """Renvoie l'UUID de la ressource désignée par `id_or_name`.

    - `collection_path` : chemin de listing, ex `/v1/registries`
    - Si la valeur ressemble à un UUID, on la retourne telle quelle.
    - Sinon on liste la collection et on cherche par `name`.
    Erreur typer.Exit(1) si aucune ou plusieurs correspondances.
    """
    if looks_like_uuid(id_or_name):
        return id_or_name
    try:
        items = client.get(collection_path)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1) from e
    matches = [it for it in items if it.get("name") == id_or_name]
    if not matches:
        rprint(f"[red]Aucune ressource nommée '{id_or_name}' trouvée.[/red]")
        raise typer.Exit(1)
    if len(matches) > 1:
        rprint(
            f"[red]Plusieurs ressources nommées '{id_or_name}' "
            f"({len(matches)}). Utilisez l'UUID.[/red]"
        )
        raise typer.Exit(1)
    return matches[0]["id"]


# Endpoint collection par type de principal (IAM v1).
_PRINCIPAL_COLLECTIONS: dict[str, tuple[str, str]] = {
    # type → (collection_path, lookup_field_for_non_uuid)
    "api_key": ("/v1/api-keys", "name"),
    "service_account": ("/v1/service-accounts", "name"),
    "org_member": ("/v1/members", "email"),
    # ccks_workload : aucun listing exposé (UUID cluster_id uniquement).
    "ccks_workload": ("", ""),
}


def resolve_principal(principal_type: str, id_or_name: str) -> str:
    """Résout un principal_id en UUID selon son type.

    - `api_key` : UUID OU nom (champ `name`).
    - `service_account` : UUID OU nom.
    - `org_member` : UUID OU email.
    - `ccks_workload` : UUID uniquement (pas de listing).

    Lève `typer.Exit(1)` avec message UX si introuvable / ambigu.
    """
    if principal_type not in _PRINCIPAL_COLLECTIONS:
        rprint(
            f"[red]Type de principal inconnu : {principal_type!r}. "
            f"Attendu un de {sorted(_PRINCIPAL_COLLECTIONS)}.[/red]"
        )
        raise typer.Exit(1)
    if looks_like_uuid(id_or_name):
        return id_or_name
    coll_path, field = _PRINCIPAL_COLLECTIONS[principal_type]
    if not coll_path:
        rprint(
            f"[red]Pour `{principal_type}`, seul un UUID est accepté "
            f"(pas de listing de principals de ce type).[/red]"
        )
        raise typer.Exit(1)
    try:
        items = client.get(coll_path)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1) from e
    matches = [it for it in items if it.get(field) == id_or_name]
    if not matches:
        rprint(
            f"[red]Aucun principal `{principal_type}` avec {field}={id_or_name!r}.[/red]"
        )
        raise typer.Exit(1)
    if len(matches) > 1:
        rprint(
            f"[red]Plusieurs principals `{principal_type}` avec {field}={id_or_name!r} "
            f"({len(matches)}). Utilisez l'UUID.[/red]"
        )
        raise typer.Exit(1)
    return matches[0]["id"]
