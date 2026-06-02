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

_ACME_CHALLENGES = ("http01", "dns01")


def _parse_credentials(entries: list[str]) -> dict[str, str]:
    """["api_token=xxx", ...] → {"api_token": "xxx"} ; ValueError si pas de '='."""
    out: dict[str, str] = {}
    for raw in entries:
        if "=" not in raw:
            raise ValueError(
                f"Credential « {raw} » invalide. Format attendu : KEY=VALUE."
            )
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Credential « {raw} » : clé manquante.")
        out[key] = value
    return out

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
            "id": gw["id"],
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
                    "member_id": (member.get("id") or "—"),
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
    acme_challenge: str | None = typer.Option(
        None, "--acme-challenge",
        help=(
            "Challenge ACME pour émettre un certificat Let's Encrypt : "
            "http01 ou dns01. Sans cette option, aucun certificat n'est émis."
        ),
    ),
    acme_dns_provider: str | None = typer.Option(
        None, "--acme-dns-provider",
        help="Provider DNS pour dns01 (cf. cetic appgw acme-providers).",
    ),
    acme_dns_credential: list[str] = typer.Option(  # noqa: B008 — Typer pattern
        None, "--acme-dns-credential",
        help="Credential DNS, répétable. Format : KEY=VALUE (ex: api_token=xxx).",
    ),
) -> None:
    """Ajoute un listener (hostname + certificat Let's Encrypt optionnel).

    Sans `--acme-challenge`, le listener est créé sans certificat (HTTP seul, ou
    SNI à configurer ultérieurement). Pour émettre un certificat Let's Encrypt,
    précisez le challenge ACME :
      • http01 : validation par fichier HTTP (le hostname doit pointer vers la GW)
      • dns01  : validation par enregistrement DNS (provider + credentials requis)

    Exemples :
      # Listener sans certificat
      cetic appgw listener add web-edge --hostname myapp-xyz.app.cloud.cetic-group.com

      # Certificat Let's Encrypt via HTTP-01
      cetic appgw listener add web-edge --hostname api.example.com \\
        --acme-challenge http01

      # Certificat Let's Encrypt via DNS-01 (provider + credentials requis)
      cetic appgw listener add web-edge --hostname api.example.com \\
        --acme-challenge dns01 \\
        --acme-dns-provider cloudflare --acme-dns-credential api_token=xxx
    """
    acme_dns_credential = acme_dns_credential or []

    body: dict[str, Any] = {"hostname": hostname}

    if acme_challenge is not None:
        if acme_challenge not in _ACME_CHALLENGES:
            rprint(
                f"[red]Erreur : --acme-challenge doit être l'un de "
                f"{', '.join(_ACME_CHALLENGES)}.[/red]"
            )
            raise typer.Exit(1)
        body["acme_challenge"] = acme_challenge
        if acme_challenge == "dns01":
            if not acme_dns_provider:
                rprint(
                    "[red]Erreur : le challenge dns01 requiert "
                    "--acme-dns-provider (cf. cetic appgw acme-providers).[/red]"
                )
                raise typer.Exit(1)
            if not acme_dns_credential:
                rprint(
                    "[red]Erreur : le challenge dns01 requiert au moins "
                    "un --acme-dns-credential KEY=VALUE.[/red]"
                )
                raise typer.Exit(1)
            try:
                creds = _parse_credentials(acme_dns_credential)
            except ValueError as e:
                rprint(f"[red]Erreur : {e}[/red]")
                raise typer.Exit(1)
            body["acme_dns_provider"] = acme_dns_provider
            body["acme_dns_credentials"] = creds

    gid = _resolve_appgw(id_or_name)
    try:
        listener = client.post(f"{APPGW_PATH}/{gid}/listeners", json=body)
    except client.APIError as e:
        raise _bail(e) from e

    name = listener.get("hostname", hostname)
    if acme_challenge is not None:
        rprint(
            f"[green]✓[/green] Listener ajouté : [bold]{name}[/bold] "
            f"(émission certificat en cours, statut: "
            f"{listener.get('acme_status', 'pending')})"
        )
    else:
        rprint(f"[green]✓[/green] Listener ajouté : [bold]{name}[/bold]")
        rprint(
            "[yellow]⚠ aucun certificat ne sera émis (pas de --acme-challenge).[/yellow]"
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
            "id": item["id"],
            "hostname": item.get("hostname", "—"),
            "acme_challenge": item.get("acme_challenge") or "—",
            "acme_status": item.get("acme_status", "—"),
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
            ("acme_challenge", "Challenge"),
            ("acme_status", "Cert"),
            ("last_renewal", "Renouvelé le"),
        ],
    )


@listener_app.command(name="get")
def listener_get(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    listener_id: str = typer.Option(..., "--listener-id", help="UUID du listener"),
) -> None:
    """Affiche les détails d'un listener (recherche locale dans la liste).

    Le backend ne propose pas de GET singleton pour les listeners ; cette
    commande liste la gateway puis filtre côté client par UUID (préfixe
    accepté également, si le préfixe est unique).
    """
    gid = _resolve_appgw(id_or_name)
    try:
        items = client.get(f"{APPGW_PATH}/{gid}/listeners")
    except client.APIError as e:
        raise _bail(e) from e
    matches = [
        item for item in items
        if item.get("id") == listener_id
        or (item.get("id") or "").startswith(listener_id)
    ]
    if not matches:
        rprint(
            f"[red]Erreur : aucun listener {listener_id} sur cette gateway.[/red]"
        )
        raise typer.Exit(1)
    if len(matches) > 1:
        rprint(
            f"[red]Erreur : préfixe {listener_id} ambigu "
            f"({len(matches)} listeners correspondent).[/red]"
        )
        raise typer.Exit(1)
    listener = matches[0]
    render_one(
        listener,
        title=f"Listener {listener.get('hostname', listener['id'])}",
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
            "id": tg["id"],
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


_TG_ALGORITHMS = {"roundrobin", "leastconn", "source"}
_TG_HC_PROTOCOLS = {"http", "https", "tcp"}
_TG_HC_METHODS = {"GET", "HEAD", "POST"}


@tg_app.command(name="update")
def tg_update(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    tg_id: str = typer.Option(..., "--tg-id", help="UUID du target group"),
    name: str | None = typer.Option(
        None, "--name", help="Nouveau nom du target group (slug)"
    ),
    algorithm: str | None = typer.Option(
        None, "--algorithm",
        help="Algorithme de répartition : roundrobin / leastconn / source",
    ),
    hc_protocol: str | None = typer.Option(
        None, "--hc-protocol",
        help="Protocole de health check : http / https / tcp",
    ),
    hc_method: str | None = typer.Option(
        None, "--hc-method",
        help="Méthode HTTP de health check : GET / HEAD / POST",
    ),
    hc_path: str | None = typer.Option(
        None, "--hc-path", help="Chemin de health check (ex: /health)"
    ),
    hc_expect_status: int | None = typer.Option(
        None, "--hc-expect-status",
        help="Code HTTP attendu pour considérer le backend UP (100-599)",
    ),
    hc_interval_sec: int | None = typer.Option(
        None, "--hc-interval-sec",
        help="Intervalle entre deux checks, en secondes (1-300)",
    ),
    hc_timeout_sec: int | None = typer.Option(
        None, "--hc-timeout-sec",
        help="Timeout d'un check, en secondes (1-60)",
    ),
    hc_healthy_threshold: int | None = typer.Option(
        None, "--hc-healthy-threshold",
        help="Nombre de checks OK consécutifs avant UP (1-10)",
    ),
    hc_unhealthy_threshold: int | None = typer.Option(
        None, "--hc-unhealthy-threshold",
        help="Nombre de checks KO consécutifs avant DOWN (1-10)",
    ),
) -> None:
    """Met à jour un target group (PATCH partiel, seuls les champs fournis sont modifiés).

    Exemples :
      # Renommer + changer l'algorithme
      cetic appgw tg update web-edge --tg-id <tg-uuid> \\
        --name api-pool-v2 --algorithm leastconn

      # Affiner le health check
      cetic appgw tg update web-edge --tg-id <tg-uuid> \\
        --hc-path /health --hc-interval-sec 10 --hc-healthy-threshold 3
    """
    if algorithm is not None and algorithm not in _TG_ALGORITHMS:
        rprint(
            f"[red]Algorithme invalide '{algorithm}'. "
            f"Valeurs attendues : {', '.join(sorted(_TG_ALGORITHMS))}.[/red]"
        )
        raise typer.Exit(1)
    if hc_protocol is not None and hc_protocol not in _TG_HC_PROTOCOLS:
        rprint(
            f"[red]Protocole de health check invalide '{hc_protocol}'. "
            f"Valeurs attendues : {', '.join(sorted(_TG_HC_PROTOCOLS))}.[/red]"
        )
        raise typer.Exit(1)
    if hc_method is not None and hc_method.upper() not in _TG_HC_METHODS:
        rprint(
            f"[red]Méthode HTTP de health check invalide '{hc_method}'. "
            f"Valeurs attendues : {', '.join(sorted(_TG_HC_METHODS))}.[/red]"
        )
        raise typer.Exit(1)
    if hc_expect_status is not None and (hc_expect_status < 100 or hc_expect_status > 599):
        rprint("[red]Erreur : --hc-expect-status doit être compris entre 100 et 599.[/red]")
        raise typer.Exit(1)
    if hc_interval_sec is not None and (hc_interval_sec < 1 or hc_interval_sec > 300):
        rprint("[red]Erreur : --hc-interval-sec doit être compris entre 1 et 300.[/red]")
        raise typer.Exit(1)
    if hc_timeout_sec is not None and (hc_timeout_sec < 1 or hc_timeout_sec > 60):
        rprint("[red]Erreur : --hc-timeout-sec doit être compris entre 1 et 60.[/red]")
        raise typer.Exit(1)
    if hc_healthy_threshold is not None and (hc_healthy_threshold < 1 or hc_healthy_threshold > 10):
        rprint("[red]Erreur : --hc-healthy-threshold doit être compris entre 1 et 10.[/red]")
        raise typer.Exit(1)
    if hc_unhealthy_threshold is not None and (
        hc_unhealthy_threshold < 1 or hc_unhealthy_threshold > 10
    ):
        rprint("[red]Erreur : --hc-unhealthy-threshold doit être compris entre 1 et 10.[/red]")
        raise typer.Exit(1)

    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if algorithm is not None:
        body["algorithm"] = algorithm
    if hc_protocol is not None:
        body["hc_protocol"] = hc_protocol
    if hc_method is not None:
        body["hc_method"] = hc_method.upper()
    if hc_path is not None:
        body["hc_path"] = hc_path
    if hc_expect_status is not None:
        body["hc_expect_status"] = hc_expect_status
    if hc_interval_sec is not None:
        body["hc_interval_sec"] = hc_interval_sec
    if hc_timeout_sec is not None:
        body["hc_timeout_sec"] = hc_timeout_sec
    if hc_healthy_threshold is not None:
        body["hc_healthy_threshold"] = hc_healthy_threshold
    if hc_unhealthy_threshold is not None:
        body["hc_unhealthy_threshold"] = hc_unhealthy_threshold

    if not body:
        rprint(
            "[red]Erreur : aucun champ à modifier. "
            "Fournissez au moins une option (--name, --algorithm, --hc-*).[/red]"
        )
        raise typer.Exit(1)

    gid = _resolve_appgw(id_or_name)
    try:
        tg = client.patch(f"{APPGW_PATH}/{gid}/target-groups/{tg_id}", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Target group mis à jour : "
        f"[bold]{(tg or {}).get('id', tg_id)}[/bold]"
    )


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


def _parse_basic_auth_users(entries: list[str]) -> list[dict[str, str]]:
    """Parse les `--basic-auth-user user:password` en liste de dicts.

    Le séparateur est le PREMIER `:` (les passwords peuvent contenir des `:`).
    Lève typer.Exit(1) si une entrée est mal formée ou si un nom est dupliqué.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in entries:
        if ":" not in raw:
            rprint(
                f"[red]Erreur : --basic-auth-user '{raw}' doit être au format "
                "user:password.[/red]"
            )
            raise typer.Exit(1)
        user, password = raw.split(":", 1)
        user = user.strip()
        if not user or not password:
            rprint(
                f"[red]Erreur : --basic-auth-user '{raw}' : user et password "
                "sont obligatoires (format user:password).[/red]"
            )
            raise typer.Exit(1)
        if len(user) > 64:
            rprint(
                f"[red]Erreur : nom d'utilisateur '{user}' trop long "
                "(max 64 caractères).[/red]"
            )
            raise typer.Exit(1)
        if len(password) > 128:
            rprint(
                f"[red]Erreur : mot de passe de l'utilisateur '{user}' trop long "
                "(max 128 caractères).[/red]"
            )
            raise typer.Exit(1)
        if user in seen:
            rprint(
                f"[red]Erreur : utilisateur '{user}' dupliqué dans "
                "--basic-auth-user.[/red]"
            )
            raise typer.Exit(1)
        seen.add(user)
        out.append({"user": user, "password": password})
    return out


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
    basic_auth_user: list[str] = typer.Option(
        [], "--basic-auth-user",
        help=(
            "Active basic auth pour cette route. Format user:password (répétable). "
            "Les mots de passe sont hashés bcrypt + chiffrés côté serveur — "
            "ils ne sont jamais retournés en lecture."
        ),
    ),
    strip_prefix: bool = typer.Option(
        False, "--strip-prefix/--no-strip-prefix",
        help=(
            "Si activé et `--path` non vide (mode prefix/exact), strippe le "
            "préfixe avant forward au backend. Ex: `/web-app/foo` devient `/foo`."
        ),
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

      # Route protégée par basic auth (2 utilisateurs)
      cetic appgw route create web-edge \\
        --listener-id <listener-uuid> --target-group-id <admin-tg-uuid> \\
        --path /admin \\
        --basic-auth-user alice:s3cret \\
        --basic-auth-user bob:hunter2
    """
    if waf_preset not in _WAF_PRESETS:
        rprint(
            f"[red]Preset WAF invalide '{waf_preset}'. "
            f"Valeurs attendues : {', '.join(sorted(_WAF_PRESETS))}.[/red]"
        )
        raise typer.Exit(1)
    basic_auth_users = _parse_basic_auth_users(list(basic_auth_user))
    gid = _resolve_appgw(id_or_name)
    body: dict[str, Any] = {
        "listener_id": listener_id,
        "target_group_id": target_group_id,
        "priority": priority,
        "waf_preset": waf_preset,
        "strip_prefix": strip_prefix,
    }
    if path:
        body["path_match"] = path
    if rate_limit is not None:
        body["rate_limit_per_sec"] = rate_limit
    if allow_cidr:
        body["allow_cidrs"] = list(allow_cidr)
    if deny_cidr:
        body["deny_cidrs"] = list(deny_cidr)
    if basic_auth_users:
        body["basic_auth_users"] = basic_auth_users
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
            "id": r["id"],
            "priority": str(r.get("priority", "—")),
            "listener": (r.get("listener_id") or "—"),
            "path": r.get("path_match") or "(tous)",
            "strip_prefix": "oui" if r.get("strip_prefix") else "non",
            "target_group": (r.get("target_group_id") or "—"),
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
            ("strip_prefix", "Strip prefix"),
            ("target_group", "Target group"),
            ("rate_limit", "Rate limit"),
            ("waf", "WAF"),
        ],
    )


@route_app.command(name="get")
def route_get(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    route_id: str = typer.Option(..., "--route-id", help="UUID de la route"),
) -> None:
    """Affiche les détails d'une route (recherche locale dans la liste).

    Pour la sortie table, le statut basic auth est résumé en
    « configuré (N utilisateurs) » à partir de `basic_auth_secret_ref` ;
    les credentials individuels ne sont JAMAIS retournés par le backend.
    """
    gid = _resolve_appgw(id_or_name)
    try:
        items = client.get(f"{APPGW_PATH}/{gid}/routes")
    except client.APIError as e:
        raise _bail(e) from e
    matches = [
        r for r in items
        if r.get("id") == route_id
        or (r.get("id") or "").startswith(route_id)
    ]
    if not matches:
        rprint(f"[red]Erreur : aucune route {route_id} sur cette gateway.[/red]")
        raise typer.Exit(1)
    if len(matches) > 1:
        rprint(
            f"[red]Erreur : préfixe {route_id} ambigu "
            f"({len(matches)} routes correspondent).[/red]"
        )
        raise typer.Exit(1)
    route = matches[0]

    # Masquer la présence/absence d'un secret basic auth derrière un libellé
    # lisible. On ne touche au champ qu'en sortie table — json/yaml gardent la
    # forme brute (basic_auth_secret_ref string ou null, comme le backend).
    display = dict(route)
    secret_ref = route.get("basic_auth_secret_ref")
    if secret_ref:
        display["basic_auth"] = "configuré (credentials masqués côté serveur)"
    else:
        display["basic_auth"] = "désactivé"
    display.pop("basic_auth_secret_ref", None)

    render_one(display, title=f"Route {route.get('id', route_id)}")


@route_app.command(name="update")
def route_update(
    id_or_name: str = typer.Argument(..., metavar="GW_ID|GW_NAME"),
    route_id: str = typer.Option(..., "--route-id", help="UUID de la route"),
    priority: int | None = typer.Option(
        None, "--priority",
        help="Ordre d'évaluation (entier ; plus bas = évalué en premier)",
    ),
    path: str | None = typer.Option(
        None, "--path",
        help="Nouveau pattern de path (ex: /api ou /api/*)",
    ),
    target_group_id: str | None = typer.Option(
        None, "--target-group-id",
        help="Nouveau target group de destination (UUID)",
    ),
    rate_limit: int | None = typer.Option(
        None, "--rate-limit",
        help="Nouvelle limite requêtes/seconde/IP pour cette route (1-100000)",
    ),
    allow_cidr: list[str] = typer.Option(
        [], "--allow-cidr",
        help="Remplace l'allow list par ces CIDR (répétable). Omis = inchangé.",
    ),
    deny_cidr: list[str] = typer.Option(
        [], "--deny-cidr",
        help="Remplace la deny list par ces CIDR (répétable). Omis = inchangé.",
    ),
    waf_preset: str | None = typer.Option(
        None, "--waf-preset",
        help="Nouveau preset WAF : off / permissive / strict",
    ),
    basic_auth_user: list[str] = typer.Option(
        [], "--basic-auth-user",
        help=(
            "Active basic auth en remplaçant les utilisateurs existants. "
            "Format user:password (répétable). Incompatible avec --no-basic-auth."
        ),
    ),
    no_basic_auth: bool = typer.Option(
        False, "--no-basic-auth",
        help="Désactive basic auth sur cette route. Incompatible avec --basic-auth-user.",
    ),
    strip_prefix: bool | None = typer.Option(
        None, "--strip-prefix/--no-strip-prefix",
        help=(
            "Active/désactive le strip du préfixe `path_match` avant forward "
            "au backend (ex: `/web-app/foo` → `/foo`). Omis = inchangé."
        ),
    ),
) -> None:
    """Met à jour une route (PATCH partiel — seuls les champs fournis sont modifiés).

    Gestion basic auth (3 cas) :
      • `--basic-auth-user user:pwd ...` (1 ou plus) → active/remplace la liste
      • `--no-basic-auth`                            → désactive
      • aucun des deux                                → préserve l'état actuel

    Exemples :
      # Augmenter la priorité (=> évaluée plus tard)
      cetic appgw route update web-edge --route-id <uuid> --priority 200

      # Changer le rate limit et le preset WAF
      cetic appgw route update web-edge --route-id <uuid> \\
        --rate-limit 50 --waf-preset strict

      # Remplacer entièrement les utilisateurs basic auth
      cetic appgw route update web-edge --route-id <uuid> \\
        --basic-auth-user alice:newpwd --basic-auth-user bob:newpwd

      # Retirer la protection basic auth
      cetic appgw route update web-edge --route-id <uuid> --no-basic-auth

      # Remplacer la deny list par une liste vide (= aucune restriction)
      cetic appgw route update web-edge --route-id <uuid> --deny-cidr ""
    """
    if basic_auth_user and no_basic_auth:
        rprint(
            "[red]Erreur : --basic-auth-user et --no-basic-auth sont "
            "incompatibles.[/red]"
        )
        raise typer.Exit(1)
    if waf_preset is not None and waf_preset not in _WAF_PRESETS:
        rprint(
            f"[red]Preset WAF invalide '{waf_preset}'. "
            f"Valeurs attendues : {', '.join(sorted(_WAF_PRESETS))}.[/red]"
        )
        raise typer.Exit(1)
    if rate_limit is not None and (rate_limit < 1 or rate_limit > 100_000):
        rprint(
            "[red]Erreur : --rate-limit doit être compris entre 1 et 100000.[/red]"
        )
        raise typer.Exit(1)

    body: dict[str, Any] = {}
    if priority is not None:
        body["priority"] = priority
    if path is not None:
        body["path_match"] = path
    if target_group_id is not None:
        body["target_group_id"] = target_group_id
    if rate_limit is not None:
        body["rate_limit_per_sec"] = rate_limit
    if allow_cidr:
        body["allow_cidrs"] = list(allow_cidr)
    if deny_cidr:
        body["deny_cidrs"] = list(deny_cidr)
    if waf_preset is not None:
        body["waf_preset"] = waf_preset
    if strip_prefix is not None:
        body["strip_prefix"] = strip_prefix

    if basic_auth_user:
        body["basic_auth_users"] = _parse_basic_auth_users(list(basic_auth_user))
    elif no_basic_auth:
        # Convention v1 : le backend accepte basic_auth_secret_ref=null pour
        # désactiver (le payload AppgwRouteUpdate l'expose explicitement).
        body["basic_auth_secret_ref"] = None

    if not body:
        rprint(
            "[red]Erreur : aucun champ à modifier. Fournissez au moins une "
            "option (--priority, --path, --rate-limit, --waf-preset, "
            "--allow-cidr, --deny-cidr, --target-group-id, --basic-auth-user, "
            "--no-basic-auth ou --strip-prefix/--no-strip-prefix).[/red]"
        )
        raise typer.Exit(1)

    gid = _resolve_appgw(id_or_name)
    try:
        route = client.patch(
            f"{APPGW_PATH}/{gid}/routes/{route_id}", json=body
        )
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Route mise à jour : "
        f"[bold]{(route or {}).get('id', route_id)}[/bold]"
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


# ---------------------------------------------------------------------------
# acme-providers : catalogue providers DNS-01 supportés
# ---------------------------------------------------------------------------


@app.command(name="acme-providers")
def acme_providers() -> None:
    """Liste les providers DNS-01 supportés pour les certificats ACME.

    Utile pour choisir la valeur à passer au backend lors de la configuration
    d'un certificat custom domain (lookup via le catalogue serveur, pas figé
    côté CLI).
    """
    try:
        data = client.get(f"{APPGW_PATH}/acme/dns-providers")
    except client.APIError as e:
        raise _bail(e) from e

    # Tolérant à 2 formes : liste plate de strings/dicts, ou dict {"providers": [...]}.
    items: list[Any]
    if isinstance(data, dict):
        items = data.get("providers") or data.get("items") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    rows: list[dict[str, Any]] = []
    for entry in items:
        if isinstance(entry, str):
            rows.append({"name": entry, "label": "—", "credentials": "—"})
        elif isinstance(entry, dict):
            creds = entry.get("required_credentials") or entry.get("credentials") or []
            if isinstance(creds, list):
                creds_str = ", ".join(str(c) for c in creds) or "—"
            else:
                creds_str = str(creds)
            rows.append(
                {
                    "name": entry.get("name") or entry.get("id") or "—",
                    "label": entry.get("label") or entry.get("display_name") or "—",
                    "credentials": creds_str,
                }
            )

    render_list(
        rows,
        title=f"Providers DNS-01 disponibles ({len(rows)})",
        columns=[
            ("name", "Identifiant"),
            ("label", "Libellé"),
            ("credentials", "Credentials requis"),
        ],
    )
