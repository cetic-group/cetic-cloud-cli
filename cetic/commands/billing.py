"""cetic billing — crédits, usage, pricing."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Crédit gratuit, consommation et tarification")


def _eur(cents: int) -> str:
    return f"{cents / 100:.2f} €"


@app.command()
def credits() -> None:
    """Affiche le solde de crédit + transactions."""
    try:
        c = client.get("/v1/billing/credits")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[bold green]Solde : {_eur(c['balance_cents'])}[/bold green]")
    if c.get("next_expiry"):
        rprint(f"[dim]Expire le {c['next_expiry'][:10]}[/dim]")
    rows = [
        {
            "type": t["credit_type"],
            "montant": _eur(t["amount_cents"]),
            "description": (t.get("description") or "—")[:50],
            "date": t["created_at"][:10],
        }
        for t in c.get("transactions", [])[:20]
    ]
    if rows:
        render_list(rows, title="Dernières transactions",
                    columns=[("date", "Date"), ("type", "Type"), ("montant", "Montant"), ("description", "Description")])


@app.command()
def usage(
    period: str = typer.Option("current_month", "--period", "-p",
                               help="current_month | last_month | last_7d | last_30d"),
) -> None:
    """Affiche la consommation agrégée du tenant."""
    try:
        u = client.get(f"/v1/billing/usage?period={period}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[bold]Période :[/bold] {u['period_start'][:10]} → {u['period_end'][:10]}")
    rprint(f"[bold]Total :[/bold] {_eur(u['total_cents'])}")
    rprint(f"  • Crédit utilisé : {_eur(u['credit_applied_cents'])}")
    rprint(f"  • Facturé Stripe : {_eur(u['stripe_billed_cents'])}")
    rows = [
        {
            "categorie": r["resource_type"] + (f" · {r['plan']}" if r["plan"] else ""),
            "heures": f"{r['hours_total']:.1f}",
            "montant": _eur(r["total_cents"]),
        }
        for r in u.get("by_category", [])
    ]
    if rows:
        render_list(rows, title="Consommation par catégorie",
                    columns=[("categorie", "Catégorie"), ("heures", "Heures"), ("montant", "Montant")])


@app.command()
def pricing() -> None:
    """Liste la grille tarifaire publique."""
    try:
        items = client.get("/v1/billing/pricing")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "type": p["resource_type"],
            "plan": p.get("plan") or "—",
            "horaire": f"{p['hourly_price_cents']} cts/h",
            "mensuel": f"{p['monthly_price_eur']:.2f} €",
            "description": (p.get("description") or "—")[:40],
        }
        for p in items
    ]
    render_list(rows, title=f"Tarification CETIC Cloud ({len(rows)} entrées)",
                columns=[("type", "Type"), ("plan", "Plan"), ("horaire", "Horaire"),
                         ("mensuel", "≈ Mois"), ("description", "Description")])
