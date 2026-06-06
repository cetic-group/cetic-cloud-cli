"""cetic scale-set / vm-scale-set — auto-scaling groups CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._catalog import (
    render_compute_plans,
    render_lxc_templates,
    render_qemu_templates,
)
from cetic.commands._render import render_list, render_one

container_app = typer.Typer(help="Container Scale Sets CETIC Cloud")
vm_app = typer.Typer(help="VM Scale Sets CETIC Cloud")


def _list(endpoint: str, kind: str) -> None:
    try:
        items = client.get(endpoint)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": s["id"], "name": s["name"], "region": s["region"],
         "plan": s["plan"], "replicas": s.get("desired_replicas") or s.get("replicas", 0),
         "status": s["status"]}
        for s in items
    ]
    render_list(rows, title=f"{kind} ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("plan", "Plan"), ("replicas", "Replicas"), ("status", "Statut")])


def _scale(endpoint: str, replicas: int) -> None:
    try:
        client.post(f"{endpoint}/scale", json={"desired_replicas": replicas})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Scale → {replicas} replicas demandé.")


@container_app.command(name="list")
def list_css(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les container scale sets."""
    suffix = f"?region={region}" if region else ""
    _list(f"/v1/container-scale-sets{suffix}", "Container Scale Sets")


@container_app.command()
def get(set_id: str = typer.Argument(...)) -> None:
    """Détails d'un scale set."""
    try:
        s = client.get(f"/v1/container-scale-sets/{set_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(s, title=s.get("name", set_id))


@container_app.command()
def scale(
    set_id: str = typer.Argument(...),
    replicas: int = typer.Option(..., "--replicas", "-n"),
) -> None:
    """Change le nombre de replicas."""
    _scale(f"/v1/container-scale-sets/{set_id}", replicas)


@container_app.command()
def delete(
    set_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un scale set (cascade replicas)."""
    if not yes and not typer.confirm(f"Supprimer le scale set {set_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/container-scale-sets/{set_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Scale set supprimé.")


@container_app.command()
def plans() -> None:
    """Liste les plans compute disponibles (partagés VM/container)."""
    render_compute_plans(kind="container", title="Plans container scale set")


@container_app.command()
def templates() -> None:
    """Liste les templates container (CT) disponibles."""
    render_lxc_templates()


@vm_app.command(name="list")
def list_vmss(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les VM scale sets."""
    suffix = f"?region={region}" if region else ""
    _list(f"/v1/vm-scale-sets{suffix}", "VM Scale Sets")


@vm_app.command()
def get(set_id: str = typer.Argument(...)) -> None:
    """Détails d'un VM scale set."""
    try:
        s = client.get(f"/v1/vm-scale-sets/{set_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(s, title=s.get("name", set_id))


@vm_app.command()
def scale(
    set_id: str = typer.Argument(...),
    replicas: int = typer.Option(..., "--replicas", "-n"),
) -> None:
    """Change le nombre de replicas."""
    _scale(f"/v1/vm-scale-sets/{set_id}", replicas)


@vm_app.command()
def delete(
    set_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un VM scale set."""
    if not yes and not typer.confirm(f"Supprimer le VM scale set {set_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/vm-scale-sets/{set_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] VM scale set supprimé.")


@vm_app.command()
def plans() -> None:
    """Liste les plans compute disponibles (partagés VM/container)."""
    render_compute_plans(kind="vm", title="Plans VM scale set")


@vm_app.command()
def templates() -> None:
    """Liste les templates VM (QEMU) disponibles."""
    render_qemu_templates()
