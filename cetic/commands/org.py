"""cetic org — organisations CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Organisations CETIC Cloud (orgs)")


@app.command(name="list")
def list_orgs() -> None:
    """Liste les organisations du tenant courant."""
    try:
        items = client.get("/v1/orgs")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": o["id"], "name": o["name"],
         "default": "✓" if o.get("is_default") else "—",
         "has_payment": "✓" if o.get("has_payment_method") else "—",
         "has_subscription": "✓" if o.get("has_subscription") else "—"}
        for o in items
    ]
    render_list(rows, title=f"Orgs ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("default", "Défaut"),
                         ("has_payment", "Carte"), ("has_subscription", "Abonn.")])


@app.command()
def get(org_id: str = typer.Argument(...)) -> None:
    """Détails d'une org."""
    try:
        o = client.get(f"/v1/orgs/{org_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(o, title=o.get("name", org_id))


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    description: str | None = typer.Option(None, "--description", "-d"),
) -> None:
    """Crée une org. Une carte de paiement doit être attachée au compte."""
    body = {"name": name}
    if description:
        body["description"] = description
    try:
        o = client.post("/v1/orgs", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Org créée : [bold]{o['id']}[/bold]")


@app.command()
def delete(
    org_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime une org (impossible si is_default)."""
    if not yes and not typer.confirm(f"Supprimer l'org {org_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/orgs/{org_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Org supprimée.")


@app.command()
def update(
    org_id: str = typer.Argument(...),
    name: str | None = typer.Option(None, "--name", "-n"),
    description: str | None = typer.Option(None, "--description", "-d"),
) -> None:
    """Met à jour le nom ou la description d'une org."""
    body: dict = {}
    if name:
        body["name"] = name
    if description is not None:
        body["description"] = description
    if not body:
        rprint("[yellow]Aucune modification demandée.[/yellow]")
        raise typer.Exit(0)
    try:
        o = client.patch(f"/v1/orgs/{org_id}", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Org mise à jour : [bold]{o.get('name', org_id)}[/bold]")


@app.command()
def switch(org_id: str = typer.Argument(...)) -> None:
    """Active une org (renouvelle le JWT avec le nouveau active_org_id)."""
    try:
        res = client.post("/v1/auth/switch-org", json={"org_id": org_id})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    # Persiste le nouveau token côté config locale
    if "access_token" in res:
        from cetic import config
        config.set_value("api_key", res["access_token"])
    rprint(f"[green]✓[/green] Org active mise à jour.")
