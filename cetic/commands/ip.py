"""cetic ip — IPs publiques CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="IPs publiques CETIC Cloud")

# Types d'IP publique (#1612). La plateforme décide lesquels elle propose :
# `cetic ip types --region X` les liste, l'API refuse un type non proposé.
VALID_TYPES = ("edge", "firewall", "nat")
# `firewall` ne sert que le cloud privé CCP (#1630) : l'adresse vient du réseau
# privé de l'interface firewall câblée sur le bridge, et c'est le firewall du
# client qui décide de l'exposer. Jamais proposé sur le cloud public.
TYPE_LABELS = {
    "edge": "Routée par edge",
    "firewall": "Via firewall",
    "nat": "NAT 1:1",
}


# Une plateforme antérieure aux types ignore le champ `type` : sans ce
# contrôle, `--type firewall` allouait (et facturait) une IP d'un autre type,
# sans erreur. Les pools d'une plateforme qui connaît les types portent tous
# la clé `type`.
_TYPES_NOT_SUPPORTED = ("La plateforme ne propose pas encore le choix du type "
                        "d'IP publique : relancez sans --type.")


def _platform_knows_types(pools: list[dict]) -> bool:
    return not pools or any("type" in p for p in pools)


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
         "type": p.get("type") or "—",
         "region": p["region"], "status": p["status"],
         "attached_to": (p.get("container_id") or p.get("vm_instance_id")
                         or p.get("load_balancer_id") or "—")}
        for p in items
    ]
    render_list(rows, title=f"IPs publiques ({len(rows)})",
                columns=[("id", "ID"), ("address", "Adresse"), ("label", "Nom"), ("type", "Type"),
                         ("region", "Région"), ("status", "Statut"), ("attached_to", "Attachée à")])


@app.command()
def types(region: str = typer.Option(..., "--region", "-r")) -> None:
    """Types d'IP publique proposés dans une région, avec les IPs libres."""
    try:
        pools = client.get("/v1/public-ips/pools", params={"region": region})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    if not _platform_knows_types(pools):
        rprint(f"[yellow]{_TYPES_NOT_SUPPORTED}[/yellow]")
        return
    # Les pools arrivent dans l'ordre de préférence de la plateforme : sans
    # `--type`, `cetic ip allocate` prend le premier qui a assez d'IPs libres.
    available: dict[str, int] = {}
    for p in pools:
        t = p.get("type") or "—"
        available[t] = available.get(t, 0) + int(p.get("available_ips") or 0)
    rows = [
        {"type": t, "label": TYPE_LABELS.get(t, t), "available": n,
         "default": "oui" if i == 0 else ""}
        for i, (t, n) in enumerate(available.items())
    ]
    render_list(rows, title=f"Types d'IP publique — {region} ({len(rows)})",
                columns=[("type", "Type"), ("label", "Libellé"),
                         ("available", "IPs libres"), ("default", "Par défaut")])


@app.command()
def allocate(
    region: str = typer.Option(..., "--region", "-r"),
    ip_type: str | None = typer.Option(
        None, "--type", "-t",
        help=("Type d'IP publique : edge (routée par edge), firewall (adresse du "
              "réseau privé du firewall, cloud privé uniquement ; toutes les "
              "ressources), nat (NAT 1:1). Sans --type : le "
              "premier type proposé qui a encore des IPs libres dans la région "
              "(cf. `cetic ip types`)."),
        case_sensitive=False,
    ),
    label: str | None = typer.Option(None, "--label", "-l",
                                     help="Nom de l'IP (ex : passerelle-prod, ip-fixe-api)"),
    description: str | None = typer.Option(None, "--description", "-d",
                                           help="À quoi sert cette IP ?"),
    quantity: int = typer.Option(1, "--quantity", "-n", min=1, max=8,
                                 help="Nombre d'IPs à allouer (1-8). Si > 1, le nom est suffixé -1, -2, …"),
) -> None:
    """Alloue une ou plusieurs IPs publiques."""
    body: dict = {"region": region}
    if ip_type:
        type_norm = ip_type.lower()
        if type_norm not in VALID_TYPES:
            rprint(
                f"[red]--type invalide : '{ip_type}'. "
                f"Valeurs autorisées : {', '.join(VALID_TYPES)}.[/red]"
            )
            raise typer.Exit(1)
        try:
            pools = client.get("/v1/public-ips/pools", params={"region": region})
        except client.APIError as e:
            rprint(f"[red]Erreur : {e.detail}[/red]")
            raise typer.Exit(1)
        if not _platform_knows_types(pools):
            rprint(f"[red]{_TYPES_NOT_SUPPORTED}[/red]")
            raise typer.Exit(1)
        body["type"] = type_norm
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
        kind = f" — {TYPE_LABELS.get(ip['type'], ip['type'])}" if ip.get("type") else ""
        rprint(f"[green]✓[/green] IP allouée : [bold]{ip.get('ip_address', ip['id'])}[/bold]{suffix}{kind}")


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
