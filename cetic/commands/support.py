"""cetic support — tickets de support CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Support tickets CETIC Cloud")


@app.command(name="list")
def list_tickets(
    status: str | None = typer.Option(None, "--status", "-s",
                                       help="open | pending_customer | pending_admin | resolved | closed"),
) -> None:
    """Liste mes tickets de support."""
    try:
        items = client.get(
            "/v1/support/tickets",
            params={"status": status} if status else None,
        )
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": t["id"][:8], "subject": t["subject"][:60],
         "category": t.get("category", "—"), "priority": t.get("priority", "—"),
         "status": t["status"], "updated": (t.get("updated_at") or "")[:10]}
        for t in items
    ]
    render_list(rows, title=f"Tickets ({len(rows)})",
                columns=[("id", "ID"), ("subject", "Sujet"), ("category", "Catégorie"),
                         ("priority", "Priorité"), ("status", "Statut"), ("updated", "MAJ")])


@app.command()
def get(ticket_id: str = typer.Argument(...)) -> None:
    """Détails d'un ticket avec messages."""
    try:
        t = client.get(f"/v1/support/tickets/{ticket_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[bold]{t['subject']}[/bold] · {t['status']} · {t.get('priority', 'normal')}")
    rprint(f"[dim]Catégorie : {t.get('category')} · Créé le {t.get('created_at', '')[:10]}[/dim]\n")
    for msg in t.get("messages", []):
        author = "[bold cyan]Vous[/bold cyan]" if msg["author_type"] == "tenant" else "[bold yellow]Support CETIC[/bold yellow]"
        rprint(f"{author} · {msg.get('created_at', '')[:16]}")
        rprint(f"  {msg.get('body', '')}\n")


@app.command()
def create(
    subject: str = typer.Option(..., "--subject", "-s"),
    body: str = typer.Option(..., "--body", "-b"),
    category: str = typer.Option("question", "--category", "-c",
                                  help="bug | feature | billing | network | infra | question"),
    priority: str = typer.Option("normal", "--priority", "-p",
                                  help="low | normal | high | urgent"),
) -> None:
    """Crée un ticket."""
    try:
        t = client.post("/v1/support/tickets", json={
            "subject": subject, "body": body,
            "category": category, "priority": priority,
        })
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Ticket créé : [bold]{t['id']}[/bold]")


@app.command()
def reply(
    ticket_id: str = typer.Argument(...),
    body: str = typer.Option(..., "--body", "-b"),
) -> None:
    """Ajoute un message à un ticket."""
    try:
        client.post(f"/v1/support/tickets/{ticket_id}/messages", json={"body": body})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Réponse envoyée.")


@app.command()
def close(
    ticket_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Ferme un ticket."""
    if not yes and not typer.confirm(f"Fermer le ticket {ticket_id} ?"):
        raise typer.Abort()
    try:
        client.post(f"/v1/support/tickets/{ticket_id}/close")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Ticket fermé.")
