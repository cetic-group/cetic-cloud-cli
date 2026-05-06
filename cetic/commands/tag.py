"""cetic tag — gestion des tags libres sur les ressources CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_one

app = typer.Typer(help="Tags libres sur les ressources CETIC Cloud")

RESOURCE_TYPES = ["container", "vm_instance", "vpc", "vnet", "volume",
                  "bucket", "load_balancer", "container_scale_set", "vm_scale_set", "db_instance"]


@app.command()
def set(
    resource_type: str = typer.Option(..., "--type", "-t",
                                      help="container | vm_instance | vpc | vnet | volume | bucket | ..."),
    resource_id: str = typer.Option(..., "--id", "-i", help="UUID de la ressource"),
    tags: list[str] = typer.Option(..., "--tag", help="Tag (répéter pour plusieurs tags)"),
) -> None:
    """Remplace tous les tags d'une ressource (idempotent)."""
    try:
        res = client.put("/v1/tags", json={
            "resource_type": resource_type,
            "resource_id": resource_id,
            "tags": tags,
        })
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Tags mis à jour : {', '.join(res.get('tags', []))}")


@app.command()
def clear(
    resource_type: str = typer.Option(..., "--type", "-t"),
    resource_id: str = typer.Option(..., "--id", "-i"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime tous les tags d'une ressource."""
    if not yes and not typer.confirm(f"Supprimer tous les tags de {resource_type}/{resource_id[:8]} ?"):
        raise typer.Abort()
    try:
        client.put("/v1/tags", json={
            "resource_type": resource_type,
            "resource_id": resource_id,
            "tags": [],
        })
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Tags supprimés.")
