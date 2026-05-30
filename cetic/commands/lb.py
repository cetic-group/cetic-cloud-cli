"""cetic lb — load balancers CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Load Balancers CETIC Cloud")

_LB_PLANS = ("small", "medium", "large")


@app.command(name="list")
def list_lbs(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les load balancers."""
    try:
        items = client.get("/v1/load-balancers", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": lb["id"], "name": lb["name"], "region": lb["region"],
         "plan": lb.get("plan") or "—",
         "status": lb["status"],
         "vip": lb.get("vip_address") or "—",
         "public_ip": lb.get("public_ip_address") or "—"}
        for lb in items
    ]
    render_list(rows, title=f"Load Balancers ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("plan", "Plan"), ("status", "Statut"),
                         ("vip", "VIP privée"), ("public_ip", "IP publique")])


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
def create(
    name: str = typer.Option(..., "--name", "-n", help="Nom du load balancer (1-100 chars)."),
    region: str = typer.Option(..., "--region", "-r", help="Région : RNN, PAR ou ABJ."),
    vnet_id: str = typer.Option(..., "--vnet", help="UUID du VNet hébergeant la VIP."),
    plan: str = typer.Option(
        "small", "--plan", "-p",
        help=(
            "Plan de capacité du LB. Choix : small (1 vCPU / 512 Mo, 4,99 €/mois, défaut), "
            "medium (2 vCPU / 1 Go, 11,99 €/mois), large (4 vCPU / 2 Go, 27,99 €/mois). "
            "Plan immuable : changer de plan plus tard implique de recréer le LB."
        ),
    ),
    public_ip_id: str | None = typer.Option(
        None, "--public-ip",
        help="UUID d'une IP publique à attacher (même région). Omettre pour un LB purement interne.",
    ),
    tag: list[str] = typer.Option(  # noqa: B008 — Typer pattern
        None, "--tag",
        help="Tag libre, répétable (`--tag web --tag env:prod`).",
    ),
) -> None:
    """Crée un load balancer (sans listeners — à ajouter ensuite via l'API ou Terraform).

    Le plan détermine la taille de la paire d'instances LB (HA active/passive).
    Les listeners et backends ne sont pas exposés dans cette commande ; pour
    une création complète scriptée, utiliser Terraform (`ccp_load_balancer`).
    """
    if plan not in _LB_PLANS:
        rprint(f"[red]Erreur : --plan doit être l'un de {', '.join(_LB_PLANS)}.[/red]")
        raise typer.Exit(1)
    body: dict[str, object] = {
        "name": name,
        "region": region,
        "plan": plan,
        "vnet_id": vnet_id,
    }
    if public_ip_id:
        body["public_ip_id"] = public_ip_id
    if tag:
        body["tags"] = list(tag)
    try:
        lb = client.post("/v1/load-balancers", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(
        f"[green]✓[/green] LB créé : [bold]{lb['id']}[/bold] "
        f"(plan: {lb.get('plan', plan)}, statut: {lb.get('status', 'provisioning')})"
    )


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
