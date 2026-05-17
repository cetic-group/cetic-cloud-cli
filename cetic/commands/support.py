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


# ─── cetic support plan {list,show,current,subscribe,unsubscribe} ────────────
# Sous-commande introduite par la vague C6.

plan_app = typer.Typer(help="Plans de support CETIC Cloud (vague C6)")
app.add_typer(plan_app, name="plan")


def _format_eur(cents: int) -> str:
    return f"{cents / 100:.2f} €"


@plan_app.command(name="list")
def list_plans() -> None:
    """Liste les plans de support disponibles."""
    try:
        items = client.get("/v1/support/plans")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "key": p["key"],
            "name": p["display_name"],
            "price": _format_eur(p["price_eur_month_cents"]) + "/mois",
            "1st_reply": f"{p['sla_first_response_hours']}h",
            "resolution": (
                f"{p['sla_resolution_hours']}h"
                if p.get("sla_resolution_hours") else "best-effort"
            ),
            "priority": p["max_priority"],
            "channels": ",".join(p.get("channels", [])),
        }
        for p in items
    ]
    render_list(
        rows, title=f"Plans de support ({len(rows)})",
        columns=[
            ("key", "Clé"), ("name", "Nom"), ("price", "Tarif"),
            ("1st_reply", "1ère réponse"), ("resolution", "Résolution"),
            ("priority", "Priorité max"), ("channels", "Canaux"),
        ],
    )


@plan_app.command()
def show(key: str = typer.Argument(...)) -> None:
    """Détail d'un plan."""
    try:
        p = client.get(f"/v1/support/plans/{key}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(p, title=f"Plan {p['display_name']}")


@plan_app.command()
def current() -> None:
    """Affiche le plan auquel votre tenant est abonné."""
    try:
        c = client.get("/v1/support/subscription")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    plan = c.get("plan")
    sub = c.get("subscription")
    if not plan or not sub:
        rprint("[dim]Aucun plan de support souscrit. Utilisez `cetic support plan subscribe <key>`.[/dim]")
        return
    rprint(f"[bold]{plan['display_name']}[/bold] · {_format_eur(plan['price_eur_month_cents'])}/mois")
    rprint(f"[dim]Actif depuis {(sub.get('started_at') or '')[:10]}[/dim]")
    rprint(f"SLA 1ère réponse : {plan['sla_first_response_hours']}h")
    if plan.get("sla_resolution_hours"):
        rprint(f"SLA résolution   : {plan['sla_resolution_hours']}h")
    rprint(f"Canaux           : {', '.join(plan.get('channels', []))}")


@plan_app.command()
def subscribe(
    key: str = typer.Argument(..., help="Clé du plan (base/standard/premium)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Souscrit à un plan de support."""
    if not yes and not typer.confirm(f"Souscrire au plan « {key} » ?"):
        raise typer.Abort()
    try:
        sub = client.post("/v1/support/subscribe", json={"plan_key": key})
    except client.APIError as e:
        if getattr(e, "status", None) == 402:
            rprint("[yellow]Ajoutez un moyen de paiement d'abord :[/yellow] `cetic billing add-card`")
            raise typer.Exit(2)
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Abonné au plan [bold]{sub['plan_key']}[/bold].")


@plan_app.command()
def change(
    key: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Alias de `subscribe` — switch vers un autre plan."""
    subscribe(key, yes=yes)


@plan_app.command()
def unsubscribe(
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Retombe sur le plan de base (gratuit)."""
    if not yes and not typer.confirm("Revenir au plan de base ?"):
        raise typer.Abort()
    try:
        sub = client.post("/v1/support/unsubscribe")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Vous êtes sur le plan [bold]{sub['plan_key']}[/bold].")
