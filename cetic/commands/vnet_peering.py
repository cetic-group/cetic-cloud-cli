"""cetic vnet-peering — peerings VNet↔VNet CETIC Cloud (relie 2 sous-réseaux de VPC différents)."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Peerings VNet↔VNet CETIC Cloud")

_COLUMNS = [
    ("id", "ID"),
    ("name", "Nom"),
    ("vnet_a", "VNet A"),
    ("vnet_b", "VNet B"),
    ("status", "Statut"),
    ("created_at", "Créé le"),
]


def _endpoint(p: dict, side: str) -> str:
    vpc = p.get(f"vpc_{side}_name")
    vnet = p.get(f"vnet_{side}_name") or "—"
    cidr = p.get(f"vnet_{side}_cidr")
    label = f"{vpc} · {vnet}" if vpc else vnet
    return f"{label} ({cidr})" if cidr else label


@app.command(name="list")
def list_peerings() -> None:
    """Liste les peerings VNet↔VNet du tenant."""
    try:
        items = client.get("/v1/vnet-peerings")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "id": p["id"],
            "name": p.get("name", "—"),
            "vnet_a": _endpoint(p, "a"),
            "vnet_b": _endpoint(p, "b"),
            "status": p.get("status", "—"),
            "created_at": (p.get("created_at") or "")[:10] or "—",
        }
        for p in items
    ]
    render_list(rows, title=f"Peerings VNet↔VNet ({len(rows)})", columns=_COLUMNS)


@app.command()
def get(peering_id: str = typer.Argument(..., help="UUID du peering VNet")) -> None:
    """Détails d'un peering VNet↔VNet."""
    try:
        p = client.get(f"/v1/vnet-peerings/{peering_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(p, title=f"Peering VNet {p.get('name', peering_id)}")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n", help="Nom du peering"),
    vnet_a: str = typer.Option(..., "--vnet-a", help="UUID du premier VNet"),
    vnet_b: str = typer.Option(..., "--vnet-b", help="UUID du second VNet"),
) -> None:
    """Crée un peering entre deux VNets de VPCs différents (même région)."""
    body: dict = {"name": name, "vnet_a_id": vnet_a, "vnet_b_id": vnet_b}
    try:
        p = client.post("/v1/vnet-peerings", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(
        f"[green]✓[/green] Peering VNet créé : [bold]{p['id']}[/bold]"
        f" (statut : {p.get('status', '—')})"
    )


@app.command()
def delete(
    peering_id: str = typer.Argument(..., help="UUID du peering VNet"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un peering VNet↔VNet."""
    if not yes and not typer.confirm(f"Supprimer le peering {peering_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/vnet-peerings/{peering_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Peering VNet supprimé.")
