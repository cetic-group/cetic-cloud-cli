"""cetic vpc — gestion des VPCs, VNets et peerings CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client, config
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="VPCs CETIC Cloud")
vnet_app = typer.Typer(help="VNets — sous-ressource d'un VPC")
ip_resv_app = typer.Typer(help="Réservations d'IP privées dans un VNet")
fw_app = typer.Typer(help="Règles de firewall d'un VNet")
app.add_typer(vnet_app, name="vnet")
vnet_app.add_typer(ip_resv_app, name="ip-reservation")
vnet_app.add_typer(fw_app, name="firewall")


def fmt_egress(snat: object) -> str:
    """Mode de sortie d'un VNet, dans les libellés retenus côté console.

    Un réseau sans sortie (`snat=false`) est parfaitement utilisable — un
    cluster Kubernetes y démarre depuis le préchargement des images et le relais
    DNS interne. Ce qu'il n'a pas, c'est Internet : sans l'afficher, on choisit
    un réseau sans savoir lequel des deux on prend.
    """
    return "Sortie internet" if snat else "Réseau isolé"


# ── IP de sortie dédiée du VPC (cetic-group/cetic-cloud-platform#1734) ──────

# Statuts de `egress_ip_status` rendus par l'API, en clair.
_EGRESS_IP_STATUS = {
    "allocated": "réservée, pas encore en service",
    "attaching": "mise en service",
    "attached": "en service",
    "detaching": "retour au pool en cours",
    "error": "en erreur",
}


def fmt_vpc_egress(v: dict) -> str:
    """Sortie internet d'un VPC : partagée (défaut) ou IP dédiée et son statut.

    `dedicated_egress_ip` absent (plateforme antérieure à l'option) se lit
    comme la sortie partagée : c'est le seul mode qui existait alors.
    """
    ip = v.get("egress_ip")
    if not v.get("dedicated_egress_ip"):
        # Désactivée mais IP encore tenue : retour au pool en cours.
        if ip:
            return f"Partagée (IP {ip} : {_fmt_ip_status(v.get('egress_ip_status'))})"
        return "Partagée"
    if not ip:
        return "Dédiée — IP en attente d'attribution"
    return f"Dédiée {ip} ({_fmt_ip_status(v.get('egress_ip_status'))})"


def _fmt_ip_status(status: object) -> str:
    if not status:
        return "statut inconnu"
    return _EGRESS_IP_STATUS.get(str(status), str(status))


def _egress_error(e: client.APIError) -> str:
    """Message lisible des refus de l'IP de sortie dédiée (409 / 422)."""
    detail = e.detail or f"Erreur HTTP {e.status_code}"
    if e.status_code == 409:
        return f"IP de sortie dédiée indisponible pour le moment : {detail}"
    if e.status_code == 422:
        return f"IP de sortie dédiée refusée : {detail}"
    return detail


_EGRESS_UNSUPPORTED = (
    "la plateforme n'a pas pris en compte l'option d'IP de sortie dédiée "
    "(fonctionnalité pas encore disponible sur cette plateforme)."
)


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
         "vnets": len(v.get("vnets", [])),
         # Champs bruts de l'API (exploitables en JSON/YAML) ; `egress` en est
         # le libellé lisible, seul affiché en table.
         "dedicated_egress_ip": bool(v.get("dedicated_egress_ip")),
         "egress_ip": v.get("egress_ip"),
         "egress_ip_type": v.get("egress_ip_type"),
         "egress_ip_status": v.get("egress_ip_status"),
         "egress": fmt_vpc_egress(v)}
        for v in items
    ]
    render_list(rows, title=f"VPCs ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("cidr", "CIDR"), ("status", "Statut"), ("vnets", "VNets"),
                         ("egress", "Sortie internet")])


@app.command()
def get(vpc_id: str = typer.Argument(...)) -> None:
    """Détails d'un VPC."""
    try:
        v = client.get(f"/v1/vpcs/{vpc_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    if config.get_output() == "table":
        # Libellé lisible en table seulement : JSON/YAML gardent la réponse brute.
        v = {**v, "sortie_internet": fmt_vpc_egress(v)}
    render_one(v, title=f"VPC {v.get('name', vpc_id)}")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
    cidr: str | None = typer.Option(
        None, "--cidr",
        help="Bloc d'adressage privé du VPC (RFC1918, /16-/24). Auto-alloué si omis."),
    dedicated_egress_ip: bool = typer.Option(
        False, "--dedicated-egress-ip",
        help="IP publique de sortie propre au VPC, conservée tant que l'option reste "
             "active (facturée, hors quota d'IP publiques). Sans l'option : sortie "
             "internet partagée de la plateforme, TCP 25 bloqué."),
) -> None:
    """Crée un VPC."""
    json: dict = {"name": name, "region": region}
    if cidr:
        json["cidr"] = cidr
    if dedicated_egress_ip:
        json["dedicated_egress_ip"] = True
    try:
        v = client.post("/v1/vpcs", json=json)
    except client.APIError as e:
        rprint(f"[red]Erreur : {_egress_error(e) if dedicated_egress_ip else e.detail}[/red]")
        raise typer.Exit(1)
    if dedicated_egress_ip and v.get("dedicated_egress_ip") is not True:
        # Une plateforme qui ne connaît pas l'option l'ignore en silence : le
        # VPC existe, mais sort par la sortie partagée. On ne le supprime pas
        # (il ne coûte rien de plus), on le dit et on échoue.
        rprint(
            f"[red]Erreur : {_EGRESS_UNSUPPORTED} Le VPC {v['id']} a été créé avec la "
            "sortie internet partagée.[/red]"
        )
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] VPC créé : [bold]{v['id']}[/bold]")
    if dedicated_egress_ip:
        rprint(f"  Sortie internet : {fmt_vpc_egress(v)}")


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
        # `snat` reste le booléen brut (exploitable en JSON/YAML) ; `egress` en
        # est le libellé lisible, seul affiché en table.
        {"id": v["id"], "name": v["name"], "cidr": v["cidr"],
         "snat": bool(v.get("snat")), "egress": fmt_egress(v.get("snat"))}
        for v in items
    ]
    render_list(rows, title=f"VNets du VPC {vpc_id[:8]} ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("cidr", "CIDR"),
                         ("egress", "Sortie")])


@vnet_app.command()
def create(
    vpc_id: str = typer.Argument(...),
    name: str = typer.Option(..., "--name", "-n"),
    cidr: str | None = typer.Option(
        None, "--cidr", help="ex: 10.0.0.0/24 — auto-attribué si omis"),
    snat: bool = typer.Option(
        False, "--snat/--no-snat",
        help="--snat = sortie internet ; --no-snat (défaut) = réseau isolé"),
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
    dedicated_egress_ip: bool | None = typer.Option(
        None, "--dedicated-egress-ip/--no-dedicated-egress-ip",
        help="Activer / désactiver l'IP publique de sortie dédiée du VPC. "
             "Désactiver rend l'IP au pool AUSSITÔT : elle est perdue."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Ne pas demander de confirmation (désactivation)"),
) -> None:
    """Modifie les paramètres à chaud d'un VPC (nom, tags, IP de sortie dédiée)."""
    body: dict = {}
    if name is not None:
        body["name"] = name
    if tags is not None:
        body["tags"] = tags
    if dedicated_egress_ip is not None:
        body["dedicated_egress_ip"] = dedicated_egress_ip
    if not body:
        rprint("[yellow]Rien à modifier (--name, --tag et/ou "
               "--dedicated-egress-ip/--no-dedicated-egress-ip).[/yellow]")
        raise typer.Exit(0)
    if dedicated_egress_ip is False and not yes:
        _confirm_egress_release(vpc_id)
    try:
        v = client.patch(f"/v1/vpcs/{vpc_id}", json=body)
    except client.APIError as e:
        msg = _egress_error(e) if dedicated_egress_ip is not None else e.detail
        rprint(f"[red]Erreur : {msg}[/red]")
        raise typer.Exit(1)
    if dedicated_egress_ip is not None and (
        not isinstance(v, dict) or v.get("dedicated_egress_ip") is not dedicated_egress_ip
    ):
        rprint(f"[red]Erreur : {_EGRESS_UNSUPPORTED}[/red]")
        raise typer.Exit(1)
    label = v.get("name", vpc_id) if isinstance(v, dict) else vpc_id
    rprint(f"[green]✓[/green] VPC mis à jour : [bold]{label}[/bold]")
    if dedicated_egress_ip is not None:
        rprint(f"  Sortie internet : {fmt_vpc_egress(v)}")


def _confirm_egress_release(vpc_id: str) -> None:
    """Confirmation de la désactivation : l'IP de sortie est perdue sans retour."""
    ip = None
    try:
        current = client.get(f"/v1/vpcs/{vpc_id}")
    except client.APIError:
        current = None  # le PATCH dira ce qu'il en est (404, droits…)
    if isinstance(current, dict):
        ip = current.get("egress_ip")
        if not current.get("dedicated_egress_ip") and not ip:
            return  # option déjà inactive et aucune IP tenue : rien à perdre
    which = f"L'IP {ip}" if ip else "L'IP de sortie dédiée"
    rprint(
        f"[yellow]{which} du VPC {vpc_id} retournera au pool AUSSITÔT, sans délai de "
        "grâce : elle est perdue et ne pourra pas être récupérée (une réactivation "
        "attribue une autre IP). Le VPC repasse sur la sortie internet partagée "
        "(TCP 25 bloqué) : pensez aux partenaires qui filtrent sur cette IP.[/yellow]"
    )
    if not typer.confirm("Désactiver l'IP de sortie dédiée ?"):
        raise typer.Abort()
