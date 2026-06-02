"""cetic ip — IPs publiques CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="IPs publiques CETIC Cloud")


@app.command(name="list")
def list_ips(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les IPs publiques du tenant."""
    try:
        items = client.get("/v1/public-ips", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": p["id"], "address": p.get("ip_address") or "—",
         "label": p.get("label") or "—",
         "region": p["region"], "status": p["status"],
         "attached_to": (p.get("container_id") or p.get("vm_instance_id")
                         or p.get("load_balancer_id") or "—")}
        for p in items
    ]
    render_list(rows, title=f"IPs publiques ({len(rows)})",
                columns=[("id", "ID"), ("address", "Adresse"), ("label", "Nom"),
                         ("region", "Région"), ("status", "Statut"), ("attached_to", "Attachée à")])


@app.command()
def allocate(
    region: str = typer.Option(..., "--region", "-r"),
    pool_id: str | None = typer.Option(None, "--pool", help="UUID du pool, sinon auto"),
    label: str | None = typer.Option(None, "--label", "-l",
                                     help="Nom de l'IP (ex : passerelle-prod, ip-fixe-api)"),
    description: str | None = typer.Option(None, "--description", "-d",
                                           help="À quoi sert cette IP ?"),
    quantity: int = typer.Option(1, "--quantity", "-n", min=1, max=8,
                                 help="Nombre d'IPs à allouer (1-8). Si > 1, le nom est suffixé -1, -2, …"),
) -> None:
    """Alloue une ou plusieurs IPs publiques."""
    body: dict = {"region": region}
    if pool_id:
        body["pool_id"] = pool_id
    if label:
        body["label"] = label
    if description:
        body["description"] = description
    try:
        if quantity > 1:
            body["quantity"] = quantity
            ips = client.post("/v1/public-ips/batch", json=body)
        else:
            ips = [client.post("/v1/public-ips", json=body)]
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    for ip in ips:
        suffix = f" ({ip['label']})" if ip.get("label") else ""
        rprint(f"[green]✓[/green] IP allouée : [bold]{ip.get('ip_address', ip['id'])}[/bold]{suffix}")


@app.command()
def get(ip_id: str = typer.Argument(...)) -> None:
    """Détails d'une IP."""
    try:
        ip = client.get(f"/v1/public-ips/{ip_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(ip, title=f"IP {ip.get('ip_address', ip_id)}")


@app.command()
def update(
    ip_id: str = typer.Argument(...),
    label: str | None = typer.Option(None, "--label", "-l", help="Nouveau nom (chaîne vide pour effacer)"),
    description: str | None = typer.Option(None, "--description", "-d",
                                           help="Nouvelle description (chaîne vide pour effacer)"),
) -> None:
    """Modifie le nom / la description d'une IP publique."""
    body: dict = {}
    if label is not None:
        body["label"] = label or None
    if description is not None:
        body["description"] = description or None
    if not body:
        rprint("[red]Préciser --label et/ou --description[/red]")
        raise typer.Exit(1)
    try:
        ip = client.patch(f"/v1/public-ips/{ip_id}", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] IP mise à jour : [bold]{ip.get('ip_address', ip_id)}[/bold]")


@app.command()
def release(
    ip_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Relâche une IP publique."""
    if not yes and not typer.confirm(f"Relâcher l'IP {ip_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/public-ips/{ip_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] IP relâchée.")


@app.command()
def attach(
    ip_id: str = typer.Argument(...),
    container: str | None = typer.Option(None, "--container"),
    vm: str | None = typer.Option(None, "--vm"),
    lb: str | None = typer.Option(None, "--lb"),
) -> None:
    """Attache l'IP à un container, VM ou LB."""
    body = {}
    if container: body["container_id"] = container
    if vm: body["vm_id"] = vm
    if lb: body["load_balancer_id"] = lb
    if not body:
        rprint("[red]Préciser --container, --vm ou --lb[/red]")
        raise typer.Exit(1)
    try:
        client.post(f"/v1/public-ips/{ip_id}/attach", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Attachement demandé.")


@app.command()
def detach(ip_id: str = typer.Argument(...)) -> None:
    """Détache l'IP."""
    try:
        client.post(f"/v1/public-ips/{ip_id}/detach")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Détachement demandé.")
