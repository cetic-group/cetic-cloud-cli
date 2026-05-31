"""cetic billing — crédits, usage, pricing, free-tier, budget, engagement, promo, estimateur."""

from typing import Optional

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Crédits, consommation, tarification, budgets, engagements, promos")


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
            "description": (t.get("description") or "—"),
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
def pricing(
    v2: bool = typer.Option(False, "--v2", help="Affiche la grille étendue (free, dimension, stopped disk)"),
) -> None:
    """Liste la grille tarifaire publique."""
    try:
        endpoint = "/v1/billing/pricing-v2" if v2 else "/v1/billing/pricing"
        items = client.get(endpoint)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    if v2:
        rows = [
            {
                "type": p["resource_type"],
                "plan": p.get("plan") or "—",
                "horaire": "Gratuit" if p.get("is_free") else f"{p['hourly_price_cents']} cts/h",
                "mensuel": "—" if p.get("is_free") else f"{p['monthly_price_eur']:.2f} €",
                "annuel": "—" if p.get("is_free") else f"{p['yearly_price_eur']:.2f} €",
                "dimension": p.get("billing_dimension", "flat_hourly"),
                "stopped_disk": str(p.get("stopped_disk_price_cents_per_gb_hour") or "—"),
            }
            for p in items
        ]
        render_list(rows, title=f"Tarification v2 ({len(rows)} entrées)",
                    columns=[("type", "Type"), ("plan", "Plan"), ("dimension", "Dim"),
                             ("horaire", "Horaire"), ("mensuel", "≈ Mois"),
                             ("annuel", "≈ An"), ("stopped_disk", "Stopped/GB/h")])
    else:
        rows = [
            {
                "type": p["resource_type"],
                "plan": p.get("plan") or "—",
                "horaire": f"{p['hourly_price_cents']} cts/h",
                "mensuel": f"{p['monthly_price_eur']:.2f} €",
                "description": (p.get("description") or "—"),
            }
            for p in items
        ]
        render_list(rows, title=f"Tarification CETIC Cloud ({len(rows)} entrées)",
                    columns=[("type", "Type"), ("plan", "Plan"), ("horaire", "Horaire"),
                             ("mensuel", "≈ Mois"), ("description", "Description")])


# ─── Free tier ──────────────────────────────────────────────────────────────


@app.command("free-tier")
def free_tier() -> None:
    """Affiche l'état du always-free tier ce mois."""
    try:
        items = client.get("/v1/billing/free-tier")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "ressource": r["resource_type"] + (f" · {r['plan']}" if r.get("plan") else ""),
            "inclus": str(r["included_quantity"]),
            "consomme": f"{r['consumed_quantity']:.2f}",
            "restant": f"{r['remaining_quantity']:.2f}",
            "description": (r.get("description") or "—"),
        }
        for r in items
    ]
    render_list(rows, title=f"Always-Free tier ({len(rows)} ressources)",
                columns=[("ressource", "Ressource"), ("inclus", "Inclus/mois"),
                         ("consomme", "Conso"), ("restant", "Restant"),
                         ("description", "Description")])


# ─── Budgets ────────────────────────────────────────────────────────────────

budget_app = typer.Typer(help="Budget mensuel + alertes 50/80/100%")
app.add_typer(budget_app, name="budget")


@budget_app.command("list")
def budget_list() -> None:
    """Liste les budgets configurés."""
    try:
        items = client.get("/v1/billing/budgets")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "id": b["id"],
            "plafond": _eur(b["monthly_budget_cents"]),
            "seuils": ", ".join(str(t) + "%" for t in b["alert_thresholds_pct"]),
            "hard_stop": "✓" if b.get("hard_stop_at_100") else "—",
            "dernier_alert": str(b.get("last_alert_threshold_pct") or "—"),
            "actif": "✓" if b.get("active") else "—",
        }
        for b in items
    ]
    render_list(rows, title=f"Budgets ({len(rows)})",
                columns=[("id", "ID"), ("plafond", "Plafond/mois"),
                         ("seuils", "Seuils"), ("hard_stop", "Hard stop"),
                         ("dernier_alert", "Dern. alert"), ("actif", "Actif")])


@budget_app.command("create")
def budget_create(
    amount: float = typer.Option(..., "--amount", "-a", help="Plafond mensuel en euros"),
    thresholds: str = typer.Option("50,80,100", "--thresholds", "-t",
                                   help="Seuils alerte % séparés par virgule"),
    emails: str = typer.Option("", "--emails", "-e", help="Emails destinataires (vide = email compte)"),
    hard_stop: bool = typer.Option(False, "--hard-stop",
                                   help="Bloquer création de ressources à 100% du budget"),
) -> None:
    """Crée un nouveau budget mensuel."""
    body = {
        "monthly_budget_cents": int(amount * 100),
        "alert_thresholds_pct": [int(s.strip()) for s in thresholds.split(",") if s.strip()],
        "notify_emails": [e.strip() for e in emails.split(",") if e.strip()] if emails else [],
        "hard_stop_at_100": hard_stop,
    }
    try:
        b = client.post("/v1/billing/budgets", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[bold green]Budget créé : {_eur(b['monthly_budget_cents'])}/mois[/bold green]")
    rprint(f"  ID : {b['id']}")
    rprint(f"  Seuils : {b['alert_thresholds_pct']}")


@budget_app.command("delete")
def budget_delete(budget_id: str = typer.Argument(...)) -> None:
    """Supprime un budget."""
    try:
        client.delete(f"/v1/billing/budgets/{budget_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]Budget {budget_id} supprimé[/green]")


# ─── Engagement (commits) ───────────────────────────────────────────────────

commit_app = typer.Typer(help="Engagement -10% mensuel ou -20% annuel")
app.add_typer(commit_app, name="commit")


@commit_app.command("list")
def commit_list() -> None:
    """Liste les engagements (actif + historique)."""
    try:
        items = client.get("/v1/billing/commits")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "id": c["id"],
            "type": "Annuel" if c["commit_type"] == "yearly" else "Mensuel",
            "remise": f"-{c['discount_pct']}%",
            "debut": c["start_at"][:10],
            "fin": c["end_at"][:10],
            "etat": "Annulé" if c.get("canceled_at") else "Actif",
        }
        for c in items
    ]
    render_list(rows, title="Engagements",
                columns=[("id", "ID"), ("type", "Type"), ("remise", "Remise"),
                         ("debut", "Début"), ("fin", "Fin"), ("etat", "État")])


@commit_app.command("create")
def commit_create(
    commit_type: str = typer.Argument(..., help="monthly (-10%) ou yearly (-20%)"),
    no_auto_renew: bool = typer.Option(False, "--no-auto-renew", help="Désactive le renouvellement auto"),
) -> None:
    """Souscrit à un engagement mensuel ou annuel."""
    if commit_type not in ("monthly", "yearly"):
        rprint("[red]Type d'engagement invalide : utiliser 'monthly' ou 'yearly'[/red]")
        raise typer.Exit(1)
    try:
        c = client.post("/v1/billing/commits", json={
            "commit_type": commit_type,
            "auto_renew": not no_auto_renew,
        })
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    label = "Annuel (-20%)" if commit_type == "yearly" else "Mensuel (-10%)"
    rprint(f"[bold green]Engagement {label} activé[/bold green]")
    rprint(f"  Valide jusqu'au {c['end_at'][:10]}")


@commit_app.command("cancel")
def commit_cancel(commit_id: str = typer.Argument(...)) -> None:
    """Annule un engagement. La remise reste valide jusqu'à l'échéance puis stoppe."""
    try:
        client.post(f"/v1/billing/commits/{commit_id}/cancel")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]Engagement {commit_id} annulé[/green]")


# ─── Codes promo ────────────────────────────────────────────────────────────

promo_app = typer.Typer(help="Codes promo")
app.add_typer(promo_app, name="promo")


@promo_app.command("available")
def promo_available() -> None:
    """Liste les codes promo publics actuellement actifs."""
    try:
        items = client.get("/v1/billing/promo-codes/available")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "code": p["code"],
            "remise": f"-{p['discount_pct']}%",
            "duree": f"{p['duration_months']} mois",
            "description": (p.get("description") or "—"),
        }
        for p in items
    ]
    render_list(rows, title=f"Codes promo disponibles ({len(rows)})",
                columns=[("code", "Code"), ("remise", "Remise"),
                         ("duree", "Durée"), ("description", "Description")])


@promo_app.command("apply")
def promo_apply(code: str = typer.Argument(..., help="Code promo (ex: LAUNCH2026)")) -> None:
    """Applique un code promo à votre compte (unique par code)."""
    try:
        p = client.post("/v1/billing/promo-codes/apply", json={"code": code.upper()})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[bold green]Code {p['code']} appliqué ![/bold green]")
    rprint(f"  Remise : -{p['discount_pct']}% pendant {p['duration_months']} mois")


# ─── Estimateur prix ────────────────────────────────────────────────────────


@app.command()
def estimate(
    resource_type: str = typer.Argument(..., help="ex: container, vm, k8s_node, db_instance"),
    plan: Optional[str] = typer.Option(None, "--plan", "-p",
                                       help="Plan (ex: small, dev:nano)"),
) -> None:
    """Estime le prix d'une ressource avec discount commit/promo + free-tier restant."""
    q = f"?resource_type={resource_type}"
    if plan:
        q += f"&plan={plan}"
    try:
        e = client.get(f"/v1/billing/estimate{q}")
    except client.APIError as exc:
        rprint(f"[red]Erreur : {exc.detail}[/red]")
        raise typer.Exit(1)

    rprint(f"[bold]{e['resource_type']}[/bold]" + (f" · {e['plan']}" if e.get("plan") else ""))
    if e["is_free"]:
        rprint("[green]Cette ressource est gratuite.[/green]")
        return
    rprint(f"  Horaire : {e['hourly_price_cents']} cts/h")
    rprint(f"  Mensuel : {e['monthly_price_eur']:.2f} €")
    rprint(f"  Annuel  : {e['yearly_price_eur']:.2f} €")
    if e["commit_discount_pct"] > 0:
        rprint(f"[dim]  Engagement actif : [/dim][green]-{e['commit_discount_pct']}%[/green]")
    if e["promo_discount_pct"] > 0:
        rprint(f"[dim]  Code promo actif : [/dim][magenta]-{e['promo_discount_pct']}%[/magenta]")
    if e["effective_monthly_price_eur"] != e["monthly_price_eur"]:
        rprint(f"[bold yellow]  Effectif : {e['effective_monthly_price_eur']:.2f} €/mois[/bold yellow]")
    if e["free_tier_units_remaining"] > 0:
        rprint(f"[green]  Free tier : {e['free_tier_units_remaining']} unité(s) restante(s) ce mois[/green]")
