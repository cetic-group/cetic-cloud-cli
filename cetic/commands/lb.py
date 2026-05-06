"""cetic lb — load balancers HAProxy CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Load Balancers CETIC Cloud")


@app.command(name="list")
def list_lbs(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les load balancers."""
    try:
        items = client.get("/v1/load-balancers", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": lb["id"][:8], "name": lb["name"], "region": lb["region"],
         "status": lb["status"],
         "vip": lb.get("vip_address") or "—",
         "public_ip": lb.get("public_ip_address") or "—"}
        for lb in items
    ]
    render_list(rows, title=f"Load Balancers ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("status", "Statut"), ("vip", "VIP privée"), ("public_ip", "IP publique")])


@app.command()
def get(lb_id: str = typer.Argument(...)) -> None:
    """Détails d'un load balancer."""
    try:
        lb = client.get(f"/v1/load-balancers/{lb_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(lb, title=f"LB {lb.get('name', lb_id)}")


@app.command()
def health(lb_id: str = typer.Argument(...)) -> None:
    """État UP/DOWN des backends d'un LB (poll HAProxy stats)."""
    try:
        h = client.get(f"/v1/load-balancers/{lb_id}/health")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(h, title="Santé backends")


@app.command()
def delete(
    lb_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un LB."""
    if not yes and not typer.confirm(f"Supprimer le LB {lb_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/load-balancers/{lb_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] LB supprimé.")


@app.command(name="attach-ip")
def attach_ip(
    lb_id: str = typer.Argument(...),
    ip_id: str = typer.Argument(..., help="UUID de l'IP publique à attacher"),
) -> None:
    """Attache une IP publique à un LB (Keepalived flottante)."""
    try:
        client.post(f"/v1/load-balancers/{lb_id}/attach-ip", json={"public_ip_id": ip_id})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Attachement IP demandé.")


@app.command(name="detach-ip")
def detach_ip(lb_id: str = typer.Argument(...)) -> None:
    """Détache l'IP publique du LB."""
    try:
        client.post(f"/v1/load-balancers/{lb_id}/detach-ip")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Détachement IP demandé.")
