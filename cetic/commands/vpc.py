"""cetic vpc — gestion des VPCs, VNets et peerings CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="VPCs CETIC Cloud")
vnet_app = typer.Typer(help="VNets — sous-ressource d'un VPC")
ip_resv_app = typer.Typer(help="Réservations d'IP privées dans un VNet")
fw_app = typer.Typer(help="Règles de firewall d'un VNet")
peering_app = typer.Typer(help="Peerings inter-VPC")
app.add_typer(vnet_app, name="vnet")
app.add_typer(peering_app, name="peering")
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
         "status": v.get("status", "—"),
         "vnets": len(v.get("vnets", []))}
        for v in items
    ]
    render_list(rows, title=f"VPCs ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("status", "Statut"), ("vnets", "VNets")])


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
) -> None:
    """Crée un VPC."""
    try:
        v = client.post("/v1/vpcs", json={"name": name, "region": region})
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
    cidr: str = typer.Option(..., "--cidr", help="ex: 10.0.0.0/24"),
    snat: bool = typer.Option(True, "--snat/--no-snat", help="Activer le SNAT outbound"),
) -> None:
    """Crée un VNet dans un VPC."""
    try:
        v = client.post(f"/v1/vpcs/{vpc_id}/vnets", json={"name": name, "cidr": cidr, "snat": snat})
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


# ── Peerings inter-VPC ────────────────────────────────────────────────────


@peering_app.command(name="list")
def list_peerings(vpc_id: str = typer.Argument(..., help="UUID du VPC")) -> None:
    """Liste les peerings d'un VPC."""
    try:
        items = client.get(f"/v1/vpcs/{vpc_id}/peerings")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "id": p["id"],
            "requester": p.get("requester_vpc_id", ""),
            "accepter": p.get("accepter_vpc_id", ""),
            "status": p.get("status", "—"),
            "created_at": p.get("created_at", "")[:10],
        }
        for p in items
    ]
    render_list(rows, title=f"Peerings du VPC {vpc_id[:8]} ({len(rows)})",
                columns=[("id", "ID"), ("requester", "Requester VPC"), ("accepter", "Accepter VPC"),
                         ("status", "Statut"), ("created_at", "Créé le")])


@peering_app.command()
def create(
    vpc_id: str = typer.Argument(..., help="UUID du VPC requester"),
    accepter_vpc_id: str = typer.Option(..., "--accepter", "-a", help="UUID du VPC accepter"),
) -> None:
    """Crée un peering entre deux VPCs (intra-tenant : auto-accepté)."""
    try:
        p = client.post(f"/v1/vpcs/{vpc_id}/peerings", json={"accepter_vpc_id": accepter_vpc_id})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Peering créé : [bold]{p['id']}[/bold] (statut : {p.get('status', '—')})")


@peering_app.command()
def delete(
    vpc_id: str = typer.Argument(...),
    peering_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un peering VPC."""
    if not yes and not typer.confirm(f"Supprimer le peering {peering_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/vpcs/{vpc_id}/peerings/{peering_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Peering supprimé.")
