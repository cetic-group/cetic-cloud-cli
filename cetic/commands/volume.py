"""cetic volume — block volumes CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Volumes de stockage bloc CETIC Cloud")


@app.command(name="list")
def list_volumes(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les volumes."""
    try:
        items = client.get("/v1/volumes", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": v["id"], "name": v["name"], "region": v["region"],
         "size_gb": v["size_gb"], "status": v["status"],
         "attached_to": (v.get("attached_to_container_id") or v.get("attached_to_vm_id") or "—")}
        for v in items
    ]
    render_list(rows, title=f"Volumes ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("size_gb", "Taille (GB)"), ("status", "Statut"), ("attached_to", "Attaché à")])


@app.command()
def get(volume_id: str = typer.Argument(...)) -> None:
    """Détails d'un volume."""
    try:
        v = client.get(f"/v1/volumes/{volume_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(v, title=f"Volume {v.get('name', volume_id)}")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
    size_gb: int = typer.Option(..., "--size", "-s", help="Taille en GB"),
) -> None:
    """Crée un volume."""
    try:
        v = client.post("/v1/volumes", json={"name": name, "region": region, "size_gb": size_gb})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Volume créé : [bold]{v['id']}[/bold]")


@app.command()
def delete(
    volume_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un volume."""
    if not yes and not typer.confirm(f"Supprimer le volume {volume_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/volumes/{volume_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Volume supprimé.")


@app.command()
def attach(
    volume_id: str = typer.Argument(...),
    container: str | None = typer.Option(None, "--container", "-c", help="UUID container cible"),
    vm: str | None = typer.Option(None, "--vm", help="UUID VM cible"),
) -> None:
    """Attache un volume à un container ou VM."""
    if not container and not vm:
        rprint("[red]Erreur : préciser --container ou --vm[/red]")
        raise typer.Exit(1)
    body = {}
    if container: body["container_id"] = container
    if vm: body["vm_id"] = vm
    try:
        client.post(f"/v1/volumes/{volume_id}/attach", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Attachement demandé.")


@app.command()
def detach(volume_id: str = typer.Argument(...)) -> None:
    """Détache un volume."""
    try:
        client.post(f"/v1/volumes/{volume_id}/detach")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Détachement demandé.")


@app.command()
def resize(
    volume_id: str = typer.Argument(...),
    size_gb: int = typer.Option(..., "--size", "-s", help="Nouvelle taille en GB (doit être supérieure à l'actuelle)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Redimensionne un volume (agrandissement uniquement)."""
    if not yes and not typer.confirm(f"Redimensionner le volume {volume_id} à {size_gb} GB ?"):
        raise typer.Abort()
    try:
        v = client.post(f"/v1/volumes/{volume_id}/resize", json={"size_gb": size_gb})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Redimensionnement demandé. Nouveau statut : {v.get('status', '—')}")
