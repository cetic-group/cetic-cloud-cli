"""Helper `resolve_id(resource, id_or_name)` réutilisable.

La spec CLI CCR demande que toutes les commandes acceptent indifféremment un
UUID OU un `name` comme premier argument. Cette fonction tente d'abord un GET
direct (UUID), puis fallback sur une recherche par nom.
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
