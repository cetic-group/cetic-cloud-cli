"""cetic vpc-peering — peerings inter-VPC CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Peerings inter-VPC CETIC Cloud")

_COLUMNS = [
    ("id", "ID"),
    ("name", "Nom"),
    ("vpc_a_id", "VPC A"),
    ("vpc_b_id", "VPC B"),
    ("status", "Statut"),
    ("created_at", "Créé le"),
]


@app.command(name="list")
def list_peerings() -> None:
    """Liste les peerings inter-VPC du tenant."""
    try:
        items = client.get("/v1/vpc-peerings")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "id": p["id"],
            "name": p.get("name", "—"),
            "vpc_a_id": p.get("vpc_a_id", "—"),
            "vpc_b_id": p.get("vpc_b_id", "—"),
            "status": p.get("status", "—"),
            "created_at": (p.get("created_at") or "")[:10] or "—",
        }
        for p in items
    ]
    render_list(rows, title=f"Peerings inter-VPC ({len(rows)})", columns=_COLUMNS)


@app.command()
def get(peering_id: str = typer.Argument(..., help="UUID du peering inter-VPC")) -> None:
    """Détails d'un peering inter-VPC."""
    try:
        p = client.get(f"/v1/vpc-peerings/{peering_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(p, title=f"Peering inter-VPC {p.get('name', peering_id)}")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n", help="Nom du peering"),
    vpc_a: str = typer.Option(..., "--vpc-a", help="UUID du premier VPC"),
    vpc_b: str = typer.Option(..., "--vpc-b", help="UUID du second VPC"),
) -> None:
    """Crée un peering entre deux VPCs."""
    body: dict = {"name": name, "vpc_a_id": vpc_a, "vpc_b_id": vpc_b}
    try:
        p = client.post("/v1/vpc-peerings", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(
        f"[green]✓[/green] Peering inter-VPC créé : [bold]{p['id']}[/bold]"
        f" (statut : {p.get('status', '—')})"
    )


@app.command()
def delete(
    peering_id: str = typer.Argument(..., help="UUID du peering inter-VPC"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un peering inter-VPC."""
    if not yes and not typer.confirm(f"Supprimer le peering {peering_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/vpc-peerings/{peering_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Peering inter-VPC supprimé.")
