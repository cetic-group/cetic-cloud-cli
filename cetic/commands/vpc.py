"""cetic vpc — gestion des VPCs, VNets et peerings CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="VPCs CETIC Cloud")
vnet_app = typer.Typer(help="VNets — sous-ressource d'un VPC")
ip_resv_app = typer.Typer(help="Réservations d'IP privées dans un VNet")
fw_app = typer.Typer(help="Règles de firewall d'un VNet")
app.add_typer(vnet_app, name="vnet")
vnet_app.add_typer(ip_resv_app, name="ip-reservation")
vnet_app.add_typer(fw_app, name="firewall")


@app.command(name="list")
def list_vpcs() -> None:
    """Liste les VPCs."""
    try:
        items = client.get("/v1/vpcs")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": v["id"], "name": v["name"], "region": v["region"],
         "cidr": v.get("cidr") or "—",
         "status": v.get("status", "—"),
         "vnets": len(v.get("vnets", []))}
        for v in items
    ]
    render_list(rows, title=f"VPCs ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("cidr", "CIDR"), ("status", "Statut"), ("vnets", "VNets")])


@app.command()
def get(vpc_id: str = typer.Argument(...)) -> None:
    """Détails d'un VPC."""
    try:
        v = client.get(f"/v1/vpcs/{vpc_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(v, title=f"VPC {v.get('name', vpc_id)}")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
    cidr: str | None = typer.Option(
        None, "--cidr",
        help="Bloc d'adressage privé du VPC (RFC1918, /16-/24). Auto-alloué si omis."),
) -> None:
    """Crée un VPC."""
    json: dict = {"name": name, "region": region}
    if cidr:
        json["cidr"] = cidr
    try:
        v = client.post("/v1/vpcs", json=json)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] VPC créé : [bold]{v['id']}[/bold]")


@app.command()
def delete(
    vpc_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un VPC."""
    if not yes and not typer.confirm(f"Supprimer le VPC {vpc_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/vpcs/{vpc_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] VPC supprimé.")


# ── VNets (sous-ressources du VPC) ────────────────────────────────────────


@vnet_app.command(name="list")
def list_vnets(vpc_id: str = typer.Argument(..., help="UUID du VPC parent")) -> None:
    """Liste les VNets d'un VPC."""
    try:
        items = client.get(f"/v1/vpcs/{vpc_id}/vnets")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": v["id"], "name": v["name"], "cidr": v["cidr"],
         "snat": "✓" if v.get("snat") else "—"}
        for v in items
    ]
    render_list(rows, title=f"VNets du VPC {vpc_id[:8]} ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("cidr", "CIDR"), ("snat", "SNAT")])


@vnet_app.command()
def create(
    vpc_id: str = typer.Argument(...),
    name: str = typer.Option(..., "--name", "-n"),
    cidr: str | None = typer.Option(
        None, "--cidr", help="ex: 10.0.0.0/24 — auto-attribué si omis"),
    snat: bool = typer.Option(
        False, "--snat/--no-snat",
        help="Activer l'accès internet sortant (désactivé par défaut)"),
) -> None:
    """Crée un VNet dans un VPC (réseau isolé par défaut ; CIDR auto-attribué si omis)."""
    body: dict = {"name": name, "snat": snat}
    if cidr:
        body["cidr"] = cidr
    try:
        v = client.post(f"/v1/vpcs/{vpc_id}/vnets", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] VNet créé : [bold]{v['id']}[/bold]")


@vnet_app.command()
def delete(
    vpc_id: str = typer.Argument(...),
    vnet_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un VNet."""
    if not yes and not typer.confirm(f"Supprimer le VNet {vnet_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/vpcs/{vpc_id}/vnets/{vnet_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] VNet supprimé.")


@vnet_app.command()
def isolate(
    vnet_id: str = typer.Argument(..., help="UUID du VNet"),
    enabled: bool = typer.Option(..., "--enable/--disable", help="Activer ou désactiver l'isolation"),
) -> None:
    """Active ou désactive l'isolation (firewall) d'un VNet."""
    try:
        r = client.put(f"/v1/vnets/{vnet_id}/firewall/isolation", json={"isolated": enabled})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    state = "activée" if r.get("isolated") else "désactivée"
    rprint(f"[green]✓[/green] Isolation {state} sur le VNet {vnet_id[:8]}.")


# ── IP Reservations (sous-ressources du VNet) ────────────────────────────


@ip_resv_app.command(name="list")
def list_ip_reservations(vnet_id: str = typer.Argument(..., help="UUID du VNet")) -> None:
    """Liste les réservations IP d'un VNet."""
    try:
        items = client.get(f"/v1/vnets/{vnet_id}/ip-reservations")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "id": r["id"],
            "name": r["name"],
            "ip": r["ip"],
            "range_end": r.get("range_end") or "—",
            "kind": r.get("kind", "—"),
            "count": r.get("count", "—"),
        }
        for r in items
    ]
    render_list(rows, title=f"Réservations IP du VNet {vnet_id[:8]} ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("ip", "IP"),
                         ("range_end", "Fin plage"), ("kind", "Type"), ("count", "Nb IPs")])


@ip_resv_app.command()
def create(
    vnet_id: str = typer.Argument(..., help="UUID du VNet"),
    name: str = typer.Option(..., "--name", "-n", help="Nom de la réservation"),
    ip: str = typer.Option(..., "--ip", help="IP de début (ou IP unique)"),
    range_end: str | None = typer.Option(None, "--range-end", help="IP de fin pour une plage"),
    description: str | None = typer.Option(None, "--desc"),
) -> None:
    """Réserve une IP ou une plage d'IPs privées dans un VNet."""
    body: dict = {"name": name, "ip": ip}
    if range_end:
        body["range_end"] = range_end
    if description:
        body["description"] = description
    try:
        r = client.post(f"/v1/vnets/{vnet_id}/ip-reservations", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Réservation créée : [bold]{r['id']}[/bold]")
    render_one(r, title=r.get("name", r["id"]))


@ip_resv_app.command()
def delete(
    vnet_id: str = typer.Argument(..., help="UUID du VNet"),
    reservation_id: str = typer.Argument(..., help="UUID de la réservation"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime une réservation IP."""
    if not yes and not typer.confirm(f"Supprimer la réservation {reservation_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/vnets/{vnet_id}/ip-reservations/{reservation_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Réservation supprimée.")


# ── Firewall rules (sous-ressources du VNet) ─────────────────────────────


@fw_app.command(name="list")
def list_fw_rules(vnet_id: str = typer.Argument(..., help="UUID du VNet")) -> None:
    """Liste les règles de firewall d'un VNet."""
    try:
        items = client.get(f"/v1/vnets/{vnet_id}/firewall/rules")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "id": r["id"],
            "dir": r.get("direction", "—"),
            "action": r.get("action", "—"),
            "proto": r.get("proto") or "any",
            "src": r.get("source_cidr") or "any",
            "dst": r.get("dest_cidr") or "any",
            "dport": r.get("dport") or "any",
            "pos": r.get("position", "—"),
            "enabled": "✓" if r.get("enabled") else "✗",
        }
        for r in items
    ]
    render_list(rows, title=f"Règles firewall du VNet {vnet_id[:8]} ({len(rows)})",
                columns=[("id", "ID"), ("dir", "Dir"), ("action", "Action"),
                         ("proto", "Proto"), ("src", "Source"), ("dst", "Dest"),
                         ("dport", "Port"), ("pos", "Pos"), ("enabled", "Actif")])


@fw_app.command()
def create(
    vnet_id: str = typer.Argument(..., help="UUID du VNet"),
    direction: str = typer.Option(..., "--dir", "-d", help="in | out"),
    action: str = typer.Option(..., "--action", "-a", help="ACCEPT | DROP"),
    proto: str | None = typer.Option(None, "--proto", help="tcp | udp | icmp"),
    source_cidr: str | None = typer.Option(None, "--src", help="CIDR source, ex: 10.0.0.0/24"),
    dest_cidr: str | None = typer.Option(None, "--dst", help="CIDR destination"),
    dport: str | None = typer.Option(None, "--dport", help="Port destination, ex: 443 ou 8000-9000"),
    comment: str | None = typer.Option(None, "--comment"),
    position: int | None = typer.Option(None, "--pos", help="Position (ordre d'évaluation)"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
) -> None:
    """Crée une règle de firewall sur un VNet (isolation doit être activée)."""
    body: dict = {"direction": direction, "action": action, "enabled": enabled}
    if proto:
        body["proto"] = proto
    if source_cidr:
        body["source_cidr"] = source_cidr
    if dest_cidr:
        body["dest_cidr"] = dest_cidr
    if dport:
        body["dport"] = dport
    if comment:
        body["comment"] = comment
    if position is not None:
        body["position"] = position
    try:
        r = client.post(f"/v1/vnets/{vnet_id}/firewall/rules", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Règle créée : [bold]{r['id']}[/bold]")
    render_one(r, title=f"Règle {r['id']}")



@fw_app.command()
def delete(
    vnet_id: str = typer.Argument(..., help="UUID du VNet"),
    rule_id: str = typer.Argument(..., help="UUID de la règle"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime une règle de firewall."""
    if not yes and not typer.confirm(f"Supprimer la règle {rule_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/vnets/{vnet_id}/firewall/rules/{rule_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Règle supprimée.")




@vnet_app.command(name="update")
def update_vnet(
    vpc_id: str = typer.Argument(..., help="UUID du VPC"),
    vnet_id: str = typer.Argument(..., help="UUID du VNet"),
    name: str | None = typer.Option(None, "--name", "-n", help="Nouveau nom"),
    snat: bool | None = typer.Option(
        None, "--snat/--no-snat",
        help="Activer/désactiver l'accès internet sortant"),
) -> None:
    """Modifie le nom / l'accès internet d'un VNet."""
    body: dict = {}
    if name is not None:
        body["name"] = name
    if snat is not None:
        body["snat"] = snat
    if not body:
        rprint("[yellow]Rien à modifier (--name et/ou --snat).[/yellow]")
        raise typer.Exit(0)
    try:
        client.patch(f"/v1/vpcs/{vpc_id}/vnets/{vnet_id}", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] VNet mis à jour.")


@app.command()
def update(
    vpc_id: str = typer.Argument(..., help="UUID du VPC"),
    name: str | None = typer.Option(None, "--name", "-n", help="Nouveau nom"),
    tags: list[str] | None = typer.Option(
        None, "--tag", help="Tag (répétable ; remplace l'ensemble des tags)"),
) -> None:
    """Modifie les paramètres à chaud d'un VPC (nom, tags)."""
    body: dict = {}
    if name is not None:
        body["name"] = name
    if tags is not None:
        body["tags"] = tags
    if not body:
        rprint("[yellow]Rien à modifier (--name et/ou --tag).[/yellow]")
        raise typer.Exit(0)
    try:
        v = client.patch(f"/v1/vpcs/{vpc_id}", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] VPC mis à jour : [bold]{v.get('name', vpc_id)}[/bold]")
