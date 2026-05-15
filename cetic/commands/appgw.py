"""cetic appgw — Application Gateways L7 CETIC Cloud.

Gère les Application Gateways managées (ccp-appgw) : aiguillage HTTP/HTTPS par
hostname + path, certificats Let's Encrypt automatiques (SNI multi-domaine),
politiques de routage (rate limit, IP allow/deny, WAF, CORS, basic auth).

Service distinct du Load Balancer L4 (`cetic lb`) :
- LB L4 : forward TCP/UDP brut (Postgres, gRPC, jeux)
- AppGW L7 : routage HTTP avec règles host/path et policies

Architecture interne (transparente côté tenant) :
- Une gateway expose 1 IP publique flottante (Keepalived) partagée par plusieurs
  hostnames via SNI.
- Hostnames sous-domaine auto (`<slug>-<id8>.app.cloud.cetic-group.com`) ou
  custom domain (CNAME vers CETIC + validation DNS-01).
- Mutations propagées en zéro-downtime (reload sans coupure).
"""

from __future__ import annotations

from typing import Any

import typer
from rich import print as rprint

from cetic import client
from cetic._resolve import resolve_id
from cetic.commands._render import render_list, render_one

APPGW_PATH = "/v1/app-gateways"

app = typer.Typer(help="Application Gateways L7 — routage HTTP/HTTPS managé")
listener_app = typer.Typer(help="Listeners (hostnames + certificats SNI)")
tg_app = typer.Typer(help="Target groups (pools de backends + health check)")
tg_member_app = typer.Typer(help="Membres d'un target group (container / VM / IP)")
route_app = typer.Typer(help="Routes (conditions host+path → target group + policies)")

app.add_typer(listener_app, name="listener")
app.add_typer(tg_app, name="tg")
tg_app.add_typer(tg_member_app, name="member")
app.add_typer(route_app, name="route")


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------


def _format_api_error(e: client.APIError) -> str:
    """Localise les erreurs API en messages français lisibles."""
    if e.status_code == 401:
        return "Non authentifié — vérifiez `cetic auth login` ou `CCP_API_KEY`."
    if e.status_code == 403:
        return "Accès refusé — droits insuffisants pour cette opération."
    if e.status_code == 404:
        return "Ressource introuvable."
    if e.status_code == 409:
        detail = (e.detail or "").lower()
        if "quota" in detail or "max_app_gateways" in detail or "limit" in detail:
            return (
                "Quota atteint — limite d'Application Gateways dépassée pour ce tenant. "
                "Demandez une augmentation via `cetic quota request`."
            )
        return f"Conflit : {e.detail}"
    if e.status_code == 422:
        return f"Paramètres invalides : {e.detail}"
    if e.status_code >= 500:
        return f"Erreur serveur ({e.status_code}). Réessayez plus tard."
    return e.detail or f"Erreur HTTP {e.status_code}"


def _bail(e: client.APIError) -> typer.Exit:
    """Affiche le message localisé puis renvoie un Exit(1) à propager."""
    rprint(f"[red]Erreur : {_format_api_error(e)}[/red]")
    return typer.Exit(1)


def _resolve_appgw(id_or_name: str) -> str:
    return resolve_id(APPGW_PATH, id_or_name)


def _status_color(status: str) -> str:
    """Couleur Rich selon l'état d'une gateway / backend."""
    s = (status or "").lower()
    if s in ("active", "running", "up", "issued"):
        return "green"
    if s in ("error", "failed", "down"):
        return "red"
    if s in ("creating", "deleting", "pending", "transient"):
        return "yellow"
    return "white"


def _format_health_status(status: str) -> str:
    """Renvoie un texte coloré Rich (UP vert, DOWN rouge, autres ambre)."""
    color = _status_color(status)
    return f"[{color}]{status or '—'}[/{color}]"


# ---------------------------------------------------------------------------
# Top-level : list / get / create / delete / attach-ip / detach-ip / health
# ---------------------------------------------------------------------------


@app.command(name="list")
def list_gateways(
    region: str | None = typer.Option(
        None, "--region", "-r", help="Filtrer par région (ex: RNN, PAR, ABJ)"
    ),
) -> None:
    """Liste les Application Gateways du tenant.

    Exemples :
      cetic appgw list
      cetic appgw list --region RNN
    """
    try:
        items = client.get(APPGW_PATH, params={"region": region} if region else None)
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": gw["id"][:8],
            "name": gw["name"],
            "region": gw.get("region", "—"),
            "plan": gw.get("plan", "—"),
            "status": gw.get("status", "—"),
            "public_ip": gw.get("public_ip_address") or "—",
            "listeners": str(gw.get("listener_count", "—")),
            "routes": str(gw.get("route_count", "—")),
        }
        for gw in items
    ]
    render_list(
        rows,
        title=f"Application Gateways ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("name", "Nom"),
            ("region", "Région"),
            ("plan", "Plan"),
            ("status", "Statut"),
            ("public_ip", "IP publique"),
            ("listeners", "Listeners"),
            ("routes", "Routes"),
        ],
    )


@app.command()
def get(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
) -> None:
    """Affiche les détails d'une Application Gateway.

    Accepte un UUID ou un nom (résolution automatique).
    """
    gid = _resolve_appgw(id_or_name)
    try:
        gw = client.get(f"{APPGW_PATH}/{gid}")
    except client.APIError as e:
        raise _bail(e) from e
    render_one(gw, title=f"Application Gateway {gw.get('name', gid)}")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n", help="Nom de la gateway (slug)"),
    region: str = typer.Option(..., "--region", "-r", help="Région (RNN/PAR/ABJ)"),
    plan: str = typer.Option(
        "small", "--plan", "-p",
        help="Plan tarifaire : small / medium / large",
    ),
    vpc: str = typer.Option(..., "--vpc", help="UUID ou nom du VPC"),
    vnet: str = typer.Option(..., "--vnet", help="UUID ou nom du VNet"),
    public_ip: str | None = typer.Option(
        None, "--public-ip",
        help="UUID d'une IP publique pré-allouée à attacher (optionnel)",
    ),
) -> None:
    """Crée une nouvelle Application Gateway.

    Exemples :
      cetic appgw create --name web-edge --region RNN --plan small \\
        --vpc prod --vnet web-tier

      cetic appgw create -n api-gw -r PAR -p medium \\
        --vpc <vpc-uuid> --vnet <vnet-uuid> --public-ip <ip-uuid>

    Le provisioning prend ~3-5 minutes. L'état passe `creating` → `active`.
    """
    body: dict[str, Any] = {
        "name": name,
        "region": region,
        "plan": plan,
        "vpc_id": vpc,
        "vnet_id": vnet,
    }
    if public_ip:
        body["public_ip_id"] = public_ip
    try:
        gw = client.post(APPGW_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Application Gateway créée : "
        f"[bold]{gw['id']}[/bold] (provisioning en cours)"
    )
    render_one(gw, title=gw.get("name", gw["id"]))


@app.command()
def delete(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation interactive"),
) -> None:
    """Supprime une Application Gateway et toutes ses ressources associées.

    Action irréversible — les listeners, routes, target groups et members
    sont détruits ; l'IP publique attachée est libérée.
    """
    gid = _resolve_appgw(id_or_name)
    if not yes and not typer.confirm(
        f"Supprimer l'Application Gateway {id_or_name} ? "
        "Cette action est irréversible."
    ):
        raise typer.Abort()
    try:
        client.delete(f"{APPGW_PATH}/{gid}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Application Gateway supprimée (téardown en cours).")


@app.command(name="attach-ip")
def attach_ip(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    public_ip_id: str = typer.Option(
        ..., "--public-ip-id", help="UUID de l'IP publique à attacher"
    ),
) -> None:
    """Attache une IP publique flottante à la gateway."""
    gid = _resolve_appgw(id_or_name)
    try:
        client.post(
            f"{APPGW_PATH}/{gid}/attach-ip",
            json={"public_ip_id": public_ip_id},
        )
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Attachement IP demandé.")


@app.command(name="detach-ip")
def detach_ip(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
) -> None:
    """Détache l'IP publique de la gateway (elle redevient interne uniquement)."""
    gid = _resolve_appgw(id_or_name)
    try:
        client.post(f"{APPGW_PATH}/{gid}/detach-ip")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Détachement IP demandé.")


@app.command()
def health(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
) -> None:
    """Affiche l'état UP/DOWN des backends par target group.

    Indicateurs colorés :
      [green]UP[/green]    backend joignable + health check OK
      [red]DOWN[/red]      backend injoignable ou check KO
      [yellow]TRANSIENT[/yellow] en cours de healthcheck (montée/descente)
    """
    gid = _resolve_appgw(id_or_name)
    try:
        h = client.get(f"{APPGW_PATH}/{gid}/health")
    except client.APIError as e:
        raise _bail(e) from e

    # Si l'API renvoie une structure {target_groups: [...]}, on affiche
    # une table colorée. Sinon fallback render_one pour le format json/yaml.
    target_groups = h.get("target_groups") if isinstance(h, dict) else None
    if not target_groups:
        render_one(h, title="Santé Application Gateway")
        return

    rows: list[dict[str, Any]] = []
    for tg in target_groups:
        tg_name = tg.get("name", "—")
        for member in tg.get("members", []):
            rows.append(
                {
                    "target_group": tg_name,
                    "member_id": (member.get("id") or "—")[:8],
                    "address": f"{member.get('address', '—')}:{member.get('port', '—')}",
                    "status": _format_health_status(member.get("status", "—")),
                    "last_check": (member.get("last_check_at") or "—")[:19],
                }
            )
    render_list(
        rows,
        title=f"Santé backends ({len(rows)})",
        columns=[
            ("target_group", "Target group"),
            ("member_id", "Member"),
            ("address", "Adresse"),
            ("status", "État"),
            ("last_check", "Dernier check"),
        ],
    )


# ---------------------------------------------------------------------------
# Sub-app : listener
# ---------------------------------------------------------------------------


@listener_app.command(name="add")
def listener_add(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    hostname: str = typer.Option(
        ..., "--hostname",
        help="Hostname à servir (ex: api.example.com)",
    ),
    custom_domain: bool = typer.Option(
        False, "--custom-domain",
        help=(
            "Le hostname est un domaine custom du client (CNAME requis vers CETIC). "
            "Active la validation DNS-01 pour Let's Encrypt."
        ),
    ),
) -> None:
    """Ajoute un listener (hostname + certificat Let's Encrypt automatique).

    Exemples :
      # Sous-domaine ccp auto
      cetic appgw listener add web-edge --hostname myapp-xyz.app.cloud.cetic-group.com

      # Custom domain (CNAME tenant → CETIC requis)
      cetic appgw listener add web-edge --hostname api.example.com --custom-domain
    """
    gid = _resolve_appgw(id_or_name)
    body: dict[str, Any] = {"hostname": hostname, "custom_domain": custom_domain}
    try:
        listener = client.post(f"{APPGW_PATH}/{gid}/listeners", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Listener ajouté : [bold]{listener.get('hostname', hostname)}[/bold] "
        f"(émission certificat en cours, statut: {listener.get('acme_status', 'pending')})"
    )


@listener_app.command(name="list")
def listener_list(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
) -> None:
    """Liste les listeners d'une gateway."""
    gid = _resolve_appgw(id_or_name)
    try:
        items = client.get(f"{APPGW_PATH}/{gid}/listeners")
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": item["id"][:8],
            "hostname": item.get("hostname", "—"),
            "acme_status": item.get("acme_status", "—"),
            "custom_domain": "oui" if item.get("custom_domain") else "non",
            "last_renewal": (item.get("acme_last_renewal_at") or "—")[:10],
        }
        for item in items
    ]
    render_list(
        rows,
        title=f"Listeners ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("hostname", "Hostname"),
            ("acme_status", "Cert"),
            ("custom_domain", "Custom"),
            ("last_renewal", "Renouvelé le"),
        ],
    )


@listener_app.command(name="delete")
def listener_delete(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    listener_id: str = typer.Option(..., "--listener-id", help="UUID du listener"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un listener (le hostname ne sera plus servi)."""
    gid = _resolve_appgw(id_or_name)
    if not yes and not typer.confirm(f"Supprimer le listener {listener_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"{APPGW_PATH}/{gid}/listeners/{listener_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Listener supprimé.")


@listener_app.command(name="renew-cert")
def listener_renew_cert(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    listener_id: str = typer.Option(..., "--listener-id", help="UUID du listener"),
) -> None:
    """Force le renouvellement du certificat Let's Encrypt.

    Normalement le renouvellement se fait automatiquement à J-14 avant
    expiration. Cette commande est utile en cas de problème ACME ou pour
    déclencher un renouvellement anticipé.
    """
    gid = _resolve_appgw(id_or_name)
    try:
        client.post(f"{APPGW_PATH}/{gid}/listeners/{listener_id}/renew-cert")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Renouvellement de certificat déclenché.")


# ---------------------------------------------------------------------------
# Sub-app : tg (target groups)
# ---------------------------------------------------------------------------


@tg_app.command(name="create")
def tg_create(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    name: str = typer.Option(..., "--name", help="Nom du target group (slug)"),
    algorithm: str = typer.Option(
        "roundrobin", "--algorithm",
        help="Algorithme de répartition : roundrobin / leastconn / source",
    ),
) -> None:
    """Crée un target group (pool de backends + health check).

    Exemples :
      cetic appgw tg create web-edge --name api-pool
      cetic appgw tg create web-edge --name web-pool --algorithm leastconn
    """
    gid = _resolve_appgw(id_or_name)
    body: dict[str, Any] = {"name": name, "algorithm": algorithm}
    try:
        tg = client.post(f"{APPGW_PATH}/{gid}/target-groups", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Target group créé : [bold]{tg['id']}[/bold] "
        f"({tg.get('name', name)})"
    )


@tg_app.command(name="list")
def tg_list(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
) -> None:
    """Liste les target groups d'une gateway."""
    gid = _resolve_appgw(id_or_name)
    try:
        items = client.get(f"{APPGW_PATH}/{gid}/target-groups")
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": tg["id"][:8],
            "name": tg.get("name", "—"),
            "algorithm": tg.get("algorithm", "—"),
            "hc_path": tg.get("hc_path", "—"),
            "members": str(tg.get("member_count", "—")),
        }
        for tg in items
    ]
    render_list(
        rows,
        title=f"Target groups ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("name", "Nom"),
            ("algorithm", "Algorithme"),
            ("hc_path", "Health path"),
            ("members", "Membres"),
        ],
    )


@tg_app.command(name="delete")
def tg_delete(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    tg_id: str = typer.Option(..., "--tg-id", help="UUID du target group"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un target group (les routes qui l'utilisent doivent être détachées d'abord)."""
    gid = _resolve_appgw(id_or_name)
    if not yes and not typer.confirm(f"Supprimer le target group {tg_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"{APPGW_PATH}/{gid}/target-groups/{tg_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Target group supprimé.")


# ---------------------------------------------------------------------------
# Sub-app : tg member (members d'un target group)
# ---------------------------------------------------------------------------


@tg_member_app.command(name="add")
def tg_member_add(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    tg_id: str = typer.Option(..., "--tg-id", help="UUID du target group"),
    container: str | None = typer.Option(
        None, "--container", help="UUID d'un container backend"
    ),
    vm: str | None = typer.Option(
        None, "--vm", help="UUID d'une VM backend"
    ),
    target_ip: str | None = typer.Option(
        None, "--target-ip",
        help="IP brute dans le VNet (backend non-managé)",
    ),
    port: int = typer.Option(..., "--port", help="Port d'écoute du backend (1-65535)"),
    weight: int = typer.Option(
        100, "--weight", help="Poids relatif (0-1000, défaut 100)"
    ),
) -> None:
    """Ajoute un membre (container, VM, ou IP brute) à un target group.

    Exactement un de --container / --vm / --target-ip doit être fourni.

    Exemples :
      # Container backend
      cetic appgw tg member add web-edge --tg-id <tg-uuid> --container <ct-uuid> --port 8080

      # VM backend
      cetic appgw tg member add web-edge --tg-id <tg-uuid> --vm <vm-uuid> --port 3000

      # IP brute (legacy / non-managé)
      cetic appgw tg member add web-edge --tg-id <tg-uuid> --target-ip 10.0.0.5 --port 8080
    """
    provided = sum(1 for v in (container, vm, target_ip) if v)
    if provided != 1:
        rprint(
            "[red]Erreur : fournissez exactement un de "
            "--container, --vm, ou --target-ip.[/red]"
        )
        raise typer.Exit(1)
    if port < 1 or port > 65535:
        rprint("[red]Erreur : le port doit être compris entre 1 et 65535.[/red]")
        raise typer.Exit(1)
    if weight < 0 or weight > 1000:
        rprint("[red]Erreur : le poids doit être compris entre 0 et 1000.[/red]")
        raise typer.Exit(1)
    gid = _resolve_appgw(id_or_name)
    body: dict[str, Any] = {"port": port, "weight": weight}
    if container:
        body["container_id"] = container
    elif vm:
        body["vm_instance_id"] = vm
    elif target_ip:
        body["target_ip"] = target_ip
    try:
        member = client.post(
            f"{APPGW_PATH}/{gid}/target-groups/{tg_id}/members",
            json=body,
        )
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Membre ajouté au target group : "
        f"[bold]{member.get('id', '—')}[/bold]"
    )


@tg_member_app.command(name="remove")
def tg_member_remove(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    tg_id: str = typer.Option(..., "--tg-id", help="UUID du target group"),
    member_id: str = typer.Option(..., "--member-id", help="UUID du membre"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Retire un membre d'un target group."""
    gid = _resolve_appgw(id_or_name)
    if not yes and not typer.confirm(f"Retirer le membre {member_id} ?"):
        raise typer.Abort()
    try:
        client.delete(
            f"{APPGW_PATH}/{gid}/target-groups/{tg_id}/members/{member_id}"
        )
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Membre retiré du target group.")


# ---------------------------------------------------------------------------
# Sub-app : route
# ---------------------------------------------------------------------------


_WAF_PRESETS = {"off", "permissive", "strict"}


@route_app.command(name="create")
def route_create(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    listener_id: str = typer.Option(..., "--listener-id", help="UUID du listener cible"),
    target_group_id: str = typer.Option(
        ..., "--target-group-id", help="UUID du target group de destination"
    ),
    path: str | None = typer.Option(
        None, "--path",
        help="Pattern de path (prefix par défaut, ex: /api ou /api/*)",
    ),
    priority: int = typer.Option(
        100, "--priority",
        help="Ordre d'évaluation (entier ; plus bas = évalué en premier)",
    ),
    rate_limit: int | None = typer.Option(
        None, "--rate-limit",
        help="Limite requêtes/seconde/IP pour cette route (NULL = hérite de la gateway)",
    ),
    allow_cidr: list[str] = typer.Option(
        [], "--allow-cidr",
        help="CIDR autorisé (répétable). Si vide : pas de restriction.",
    ),
    deny_cidr: list[str] = typer.Option(
        [], "--deny-cidr",
        help="CIDR bloqué (répétable). Évalué avant allow.",
    ),
    waf_preset: str = typer.Option(
        "off", "--waf-preset",
        help="Preset WAF : off / permissive / strict",
    ),
) -> None:
    """Crée une route (condition host+path → target group + policies L7).

    Exemples :
      # Route simple : tout vers un target group
      cetic appgw route create web-edge \\
        --listener-id <listener-uuid> --target-group-id <tg-uuid>

      # Route path-based avec rate limit + WAF strict
      cetic appgw route create web-edge \\
        --listener-id <listener-uuid> --target-group-id <api-tg-uuid> \\
        --path /api --priority 50 --rate-limit 100 --waf-preset strict

      # Route avec IP allowlist
      cetic appgw route create web-edge \\
        --listener-id <listener-uuid> --target-group-id <admin-tg-uuid> \\
        --path /admin --allow-cidr 10.0.0.0/8 --allow-cidr 192.168.1.0/24
    """
    if waf_preset not in _WAF_PRESETS:
        rprint(
            f"[red]Preset WAF invalide '{waf_preset}'. "
            f"Valeurs attendues : {', '.join(sorted(_WAF_PRESETS))}.[/red]"
        )
        raise typer.Exit(1)
    gid = _resolve_appgw(id_or_name)
    body: dict[str, Any] = {
        "listener_id": listener_id,
        "target_group_id": target_group_id,
        "priority": priority,
        "waf_preset": waf_preset,
    }
    if path:
        body["path_match"] = path
    if rate_limit is not None:
        body["rate_limit_per_sec"] = rate_limit
    if allow_cidr:
        body["allow_cidrs"] = list(allow_cidr)
    if deny_cidr:
        body["deny_cidrs"] = list(deny_cidr)
    try:
        route = client.post(f"{APPGW_PATH}/{gid}/routes", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Route créée : [bold]{route.get('id', '—')}[/bold] "
        f"(priorité {route.get('priority', priority)})"
    )


@route_app.command(name="list")
def route_list(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
) -> None:
    """Liste les routes d'une gateway (triées par priorité asc)."""
    gid = _resolve_appgw(id_or_name)
    try:
        items = client.get(f"{APPGW_PATH}/{gid}/routes")
    except client.APIError as e:
        raise _bail(e) from e
    # Tri par priority asc côté CLI au cas où l'API ne le fait pas.
    items = sorted(items, key=lambda r: r.get("priority", 100))
    rows = [
        {
            "id": r["id"][:8],
            "priority": str(r.get("priority", "—")),
            "listener": (r.get("listener_id") or "—")[:8],
            "path": r.get("path_match") or "(tous)",
            "target_group": (r.get("target_group_id") or "—")[:8],
            "rate_limit": str(r.get("rate_limit_per_sec") or "—"),
            "waf": r.get("waf_preset", "off"),
        }
        for r in items
    ]
    render_list(
        rows,
        title=f"Routes ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("priority", "Priorité"),
            ("listener", "Listener"),
            ("path", "Path"),
            ("target_group", "Target group"),
            ("rate_limit", "Rate limit"),
            ("waf", "WAF"),
        ],
    )


@route_app.command(name="delete")
def route_delete(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    route_id: str = typer.Option(..., "--route-id", help="UUID de la route"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime une route."""
    gid = _resolve_appgw(id_or_name)
    if not yes and not typer.confirm(f"Supprimer la route {route_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"{APPGW_PATH}/{gid}/routes/{route_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Route supprimée.")
