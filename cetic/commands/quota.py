"""cetic quota — quotas et demandes d'augmentation CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_one, render_list

app = typer.Typer(help="Quotas et demandes d'augmentation de quota")


@app.command()
def show() -> None:
    """Affiche les quotas et l'usage courant."""
    try:
        q = client.get("/v1/quotas")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(q, title="Quotas")


@app.command(name="requests")
def list_requests(
    status: str | None = typer.Option(None, "--status", "-s",
                                      help="pending | approved | rejected"),
) -> None:
    """Liste mes demandes d'augmentation de quota."""
    try:
        items = client.get("/v1/quotas/requests",
                           params={"status": status} if status else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": r["id"], "field": r["field"], "requested": r["requested_value"],
         "current": r.get("current_value", "—"), "status": r["status"],
         "created": r.get("created_at", "")[:10]}
        for r in items
    ]
    render_list(rows, title=f"Demandes quota ({len(rows)})",
                columns=[("id", "ID"), ("field", "Champ"), ("requested", "Demandé"),
                         ("current", "Actuel"), ("status", "Statut"), ("created", "Créé le")])


@app.command(name="request")
def create_request(
    field: str = typer.Option(..., "--field", "-f",
                              help="max_containers | max_cores | max_memory_mb | ..."),
    value: int = typer.Option(..., "--value", "-v", help="Valeur souhaitée"),
    reason: str = typer.Option(..., "--reason", "-r", help="Justification de la demande"),
) -> None:
    """Soumet une demande d'augmentation de quota."""
    try:
        r = client.post("/v1/quotas/requests", json={
            "field": field,
            "requested_value": value,
            "reason": reason,
        })
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Demande créée : [bold]{r['id']}[/bold] (statut : {r.get('status', '—')})")
