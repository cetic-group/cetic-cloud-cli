"""cetic lb — load balancers CETIC Cloud."""

from pathlib import Path

import typer
from rich import print as rprint

from cetic import client, config
from cetic.commands._catalog import render_compute_plans
from cetic.commands._gateway import (
    cert_expiry_label,
    network_label,
    read_cert_pair,
    read_error_page,
    table_view,
    target_label,
    validate_created_replica_count,
)
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Load Balancers CETIC Cloud")
backend_app = typer.Typer(help="Backends d'un listener LB")
listener_app = typer.Typer(help="Listeners d'un LB (ajout, rotation de certificat)")
app.add_typer(backend_app, name="backend")
app.add_typer(listener_app, name="listener")


@app.command()
def plans() -> None:
    """Liste les plans Load Balancer disponibles (lb-small/medium/large)."""
    render_compute_plans(kind="lb", title="Plans Load Balancer")

_LB_PLANS = ("small", "medium", "large")
_LB_PROTOCOLS = ("tcp", "http", "https")
_LB_ALGORITHMS = ("roundrobin", "leastconn", "source", "random")
# `custom` = certificat fourni par le client (#1579) : il ne se renouvelle
# pas tout seul, on le remplace avec `cetic lb listener rotate-cert`.
_ACME_CHALLENGES = ("http01", "dns01", "custom")

# Type de cible d'un backend → champ de l'API. Un scale set est UNE cible
# dynamique : toutes ses instances en service reçoivent le trafic, y compris
# celles ajoutées plus tard. Il ne faut surtout pas le développer en N backends.
_BACKEND_TARGETS = {
    "container": "container_id",
    "vm": "vm_instance_id",
    "ctss": "container_scale_set_id",
    "vmss": "vm_scale_set_id",
}
_BACKEND_FORMAT = (
    "container:UUID:PORT[:WEIGHT], vm:UUID:PORT[:WEIGHT], "
    "ctss:UUID:PORT[:WEIGHT] (scale set de containers) ou "
    "vmss:UUID:PORT[:WEIGHT] (scale set de VM)"
)


def _parse_backend_spec(spec: str) -> dict:
    """container|vm|ctss|vmss:UUID:PORT[:WEIGHT] → dict backend API.

    Lève ValueError avec un message clair en français si le format est invalide.
    """
    parts = spec.split(":")
    if len(parts) not in (3, 4):
        raise ValueError(
            f"Backend invalide « {spec} ». Format attendu : {_BACKEND_FORMAT}."
        )
    kind, uuid_, port_str = parts[0], parts[1], parts[2]
    if kind not in _BACKEND_TARGETS:
        raise ValueError(
            f"Backend « {spec} » : le type doit être « container », « vm », "
            "« ctss » ou « vmss »."
        )
    if not uuid_:
        raise ValueError(f"Backend « {spec} » : UUID manquant.")
    try:
        port = int(port_str)
    except ValueError:
        raise ValueError(f"Backend « {spec} » : port « {port_str} » invalide.")
    if not (1 <= port <= 65535):
        raise ValueError(f"Backend « {spec} » : port doit être entre 1 et 65535.")
    out: dict = {"port": port, "weight": 1}
    if len(parts) == 4:
        try:
            weight = int(parts[3])
        except ValueError:
            raise ValueError(f"Backend « {spec} » : poids « {parts[3]} » invalide.")
        if not (0 <= weight <= 256):
            raise ValueError(f"Backend « {spec} » : poids doit être entre 0 et 256.")
        out["weight"] = weight
    out[_BACKEND_TARGETS[kind]] = uuid_
    return out


def _build_listener(
    *,
    protocol: str,
    port: int,
    algorithm: str,
    backends: list[str],
    domain: str | None,
    acme_challenge: str | None,
    acme_dns_provider: str | None,
    acme_dns_credentials: list[str],
    cert_file: Path | None,
    key_file: Path | None,
) -> dict[str, object]:
    """Corps d'un listener (création du LB ou ajout) ; ValueError si incohérent."""
    if protocol not in _LB_PROTOCOLS:
        raise ValueError(f"--listener-protocol doit être l'un de {', '.join(_LB_PROTOCOLS)}.")
    if algorithm not in _LB_ALGORITHMS:
        raise ValueError(f"--algorithm doit être l'un de {', '.join(_LB_ALGORITHMS)}.")
    listener: dict[str, object] = {
        "protocol": protocol,
        "listen_port": port,
        "algorithm": algorithm,
    }
    parsed = [_parse_backend_spec(b) for b in backends]
    if parsed:
        listener["backends"] = parsed

    if (cert_file or key_file) and acme_challenge != "custom":
        raise ValueError("--cert-file/--key-file requièrent --acme-challenge custom.")
    if acme_challenge is None:
        if domain:
            listener["domain"] = domain
        return listener

    if acme_challenge not in _ACME_CHALLENGES:
        raise ValueError(f"--acme-challenge doit être l'un de {', '.join(_ACME_CHALLENGES)}.")
    if protocol != "https":
        raise ValueError("--acme-challenge requiert un listener https.")
    if not domain:
        # Pour un certificat client, l'API accepterait l'absence de domaine,
        # mais n'en vérifierait alors pas les noms : on l'exige.
        raise ValueError("--acme-challenge requiert --domain.")
    listener["domain"] = domain
    listener["acme_challenge"] = acme_challenge
    if acme_challenge == "custom":
        listener.update(read_cert_pair(cert_file, key_file))
    elif acme_challenge == "dns01":
        if not acme_dns_provider:
            raise ValueError(
                "le challenge dns01 requiert --acme-dns-provider "
                "(cf. cetic lb acme-providers)."
            )
        if not acme_dns_credentials:
            raise ValueError(
                "le challenge dns01 requiert au moins un --acme-dns-credential KEY=VALUE."
            )
        listener["acme_dns_provider"] = acme_dns_provider
        listener["acme_dns_credentials"] = _parse_credentials(acme_dns_credentials)
    return listener


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
         "replica_count": lb.get("replica_count", 2),
         "status": lb["status"],
         "vip": lb.get("vip_address") or "—",
         "public_ip": lb.get("public_ip_address") or "—"}
        for lb in items
    ]
    render_list(rows, title=f"Load Balancers ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("plan", "Plan"), ("replica_count", "Instances"), ("status", "Statut"),
                         ("vip", "VIP privée"), ("public_ip", "IP publique")])


@app.command()
def get(lb_id: str = typer.Argument(...)) -> None:
    """Détails d'un load balancer."""
    try:
        lb = client.get(f"/v1/load-balancers/{lb_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    if config.get_output() != "table":
        # JSON/YAML : la ressource telle que l'API la renvoie (page HTML comprise).
        render_one(lb, title=f"LB {lb.get('name', lb_id)}")
        return
    view = table_view(lb)
    view["network"] = network_label(lb)
    render_one(view, title=f"LB {lb.get('name', lb_id)}")
    listeners = lb.get("listeners") or []
    if listeners:
        rows = [
            {"id": ls.get("id"), "protocol": ls.get("protocol"),
             "listen_port": ls.get("listen_port"), "algorithm": ls.get("algorithm"),
             "domain": ls.get("domain") or "—",
             "cert": ("votre certificat" if ls.get("acme_challenge") == "custom"
                      else ls.get("acme_status") or "—"),
             "expires": cert_expiry_label(ls.get("tls_cert_not_after")),
             "backends": str(len(ls.get("backends") or []))}
            for ls in listeners
        ]
        render_list(rows, title=f"Listeners ({len(rows)})",
                    columns=[("id", "ID"), ("protocol", "Proto"),
                             ("listen_port", "Port"), ("algorithm", "Algo"),
                             ("domain", "Domaine"), ("cert", "Cert"),
                             ("expires", "Expire le"), ("backends", "Backends")])
        backends = [
            {"listener": f"{ls.get('protocol')}:{ls.get('listen_port')}",
             "id": b.get("id"), "target": target_label(b),
             "port": b.get("port"), "weight": b.get("weight")}
            for ls in listeners for b in (ls.get("backends") or [])
        ]
        if backends:
            render_list(backends, title=f"Backends ({len(backends)})",
                        columns=[("listener", "Listener"), ("id", "ID"),
                                 ("target", "Cible"), ("port", "Port"),
                                 ("weight", "Poids")])


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n", help="Nom du load balancer (1-100 chars)."),
    region: str = typer.Option(..., "--region", "-r", help="Région : RNN, PAR ou ABJ."),
    vnet_id: str = typer.Option(..., "--vnet", help="UUID du VNet hébergeant la VIP."),
    replica_count: int = typer.Option(
        2, "--replica-count", min=1, max=2,
        help="1 instance sans redondance, ou 2 en haute disponibilité (défaut). Immuable après création.",
    ),
    plan: str = typer.Option(
        "small", "--plan", "-p",
        help=(
            "Plan de capacité du LB : small (défaut), medium ou large. "
            "Consulter cetic lb plans pour les capacités et tarifs du catalogue. "
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
    # — Listener (optionnel, un seul définissable via flags) —
    listener_protocol: str | None = typer.Option(None, "--listener-protocol",
        help="Protocole du listener : tcp, http ou https."),
    listener_port: int | None = typer.Option(None, "--listener-port", min=1, max=65535,
        help="Port d'écoute du listener."),
    algorithm: str = typer.Option("roundrobin", "--algorithm",
        help="Algorithme : roundrobin, leastconn, source ou random."),
    backend: list[str] = typer.Option(None, "--backend",  # noqa: B008
        help=f"Backend, répétable. Format : {_BACKEND_FORMAT}. Un scale set est une "
             "cible dynamique (ses nouvelles instances reçoivent le trafic)."),
    # — Certificat Let's Encrypt (listener https uniquement) —
    domain: str | None = typer.Option(None, "--domain",
        help="Domaine du certificat Let's Encrypt (ex: www.example.com)."),
    acme_challenge: str | None = typer.Option(None, "--acme-challenge",
        help="Certificat : http01 ou dns01 (Let's Encrypt), ou custom (votre "
             "certificat, avec --cert-file et --key-file)."),
    acme_dns_provider: str | None = typer.Option(None, "--acme-dns-provider",
        help="Provider DNS pour dns01 (cf. cetic lb acme-providers ; ex: cloudflare, "
             "ionos (champs prefix + secret))."),
    acme_dns_credential: list[str] = typer.Option(None, "--acme-dns-credential",  # noqa: B008
        help="Credential DNS, répétable. Format : KEY=VALUE (ex: api_token=xxx)."),
    cert_file: Path | None = typer.Option(None, "--cert-file", exists=True, dir_okay=False,  # noqa: B008
        help="Certificat PEM (+ chaîne) pour --acme-challenge custom."),
    key_file: Path | None = typer.Option(None, "--key-file", exists=True, dir_okay=False,  # noqa: B008
        help="Clé privée PEM NON chiffrée pour --acme-challenge custom."),
) -> None:
    """Crée un load balancer, avec optionnellement un listener HTTPS + Let's Encrypt.

    Le plan détermine la capacité de chaque instance. Le nombre d'instances
    se choisit à la création : 1 sans redondance ou 2 en haute disponibilité.

    Un listener unique peut être défini directement à la création via
    `--listener-protocol` + `--listener-port` (les deux sont requis ensemble),
    avec ses backends (`--backend`, répétable) et, pour un listener `https`, un
    certificat Let's Encrypt (`--domain` + `--acme-challenge`).

    Exemples :
      # LB nu (listeners à ajouter via Terraform pour les topologies multi-listener)
      cetic lb create -n web -r RNN --vnet <vnet-uuid>

      # LB avec un listener HTTPS + cert Let's Encrypt (validation HTTP-01)
      cetic lb create -n web -r RNN --vnet <vnet-uuid> \\
        --listener-protocol https --listener-port 443 \\
        --domain www.example.com --acme-challenge http01 \\
        --backend container:<ct-uuid>:8080

      # Validation DNS-01 (provider + credentials requis)
      cetic lb create -n web -r RNN --vnet <vnet-uuid> \\
        --listener-protocol https --listener-port 443 \\
        --domain www.example.com --acme-challenge dns01 \\
        --acme-dns-provider cloudflare --acme-dns-credential api_token=xxx

      # Votre propre certificat, devant un scale set (cible dynamique)
      cetic lb create -n web -r RNN --vnet <vnet-uuid> \\
        --listener-protocol https --listener-port 443 --domain www.example.com \\
        --acme-challenge custom --cert-file fullchain.pem --key-file privkey.pem \\
        --backend vmss:<vmss-uuid>:8080

    Pour des topologies à plusieurs listeners, utiliser Terraform
    (`ccp_load_balancer`) — le backend n'accepte les listeners qu'à la création.
    """
    if plan not in _LB_PLANS:
        rprint(f"[red]Erreur : --plan doit être l'un de {', '.join(_LB_PLANS)}.[/red]")
        raise typer.Exit(1)

    backend = backend or []
    acme_dns_credential = acme_dns_credential or []

    # Listener : protocole et port vont par paire.
    if bool(listener_protocol) != bool(listener_port):
        rprint("[red]Erreur : --listener-protocol et --listener-port doivent être "
               "fournis ensemble.[/red]")
        raise typer.Exit(1)

    listener_dict: dict[str, object] | None = None
    if listener_protocol and listener_port:
        try:
            listener_dict = _build_listener(
                protocol=listener_protocol, port=listener_port, algorithm=algorithm,
                backends=backend, domain=domain, acme_challenge=acme_challenge,
                acme_dns_provider=acme_dns_provider,
                acme_dns_credentials=acme_dns_credential,
                cert_file=cert_file, key_file=key_file,
            )
        except ValueError as e:
            rprint(f"[red]Erreur : {e}[/red]")
            raise typer.Exit(1)

    body: dict[str, object] = {
        "name": name,
        "region": region,
        "plan": plan,
        "replica_count": replica_count,
        "vnet_id": vnet_id,
    }
    if public_ip_id:
        body["public_ip_id"] = public_ip_id
    if tag:
        body["tags"] = list(tag)
    if listener_dict is not None:
        body["listeners"] = [listener_dict]
    try:
        lb = client.post("/v1/load-balancers", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    try:
        validate_created_replica_count(lb, replica_count)
    except ValueError as exc:
        rprint(f"[red]Erreur : {exc}[/red]")
        raise typer.Exit(1) from exc
    rprint(
        f"[green]✓[/green] LB créé : [bold]{lb['id']}[/bold] "
        f"(plan: {lb.get('plan', plan)}, statut: {lb.get('status', 'provisioning')})"
    )


@app.command()
def health(lb_id: str = typer.Argument(...)) -> None:
    """État UP/DOWN des backends d'un LB (état temps réel).

    Pour une cible scale set, la ligne compte ses instances en service
    (« 2/3 » : 2 instances en service sur 3).
    """
    try:
        h = client.get(f"/v1/load-balancers/{lb_id}/health")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    if config.get_output() != "table" or not isinstance(h, dict):
        render_one(h if isinstance(h, dict) else {"health": h}, title="Santé backends")
        return
    rows = [
        {"backend": backend_id, "status": (info or {}).get("status") or "—",
         "check": (info or {}).get("check_status") or "—",
         "instances": (f"{info['instances_up']}/{info['instances_total']}"
                       if info and "instances_total" in info else "—")}
        for backend_id, info in h.items()
    ]
    render_list(rows, title=f"Santé backends ({len(rows)})",
                columns=[("backend", "Backend"), ("status", "État"),
                         ("check", "Check"), ("instances", "Instances en service")])


@app.command()
def update(
    lb_id: str = typer.Argument(...),
    name: str | None = typer.Option(None, "--name", "-n", help="Nouveau nom."),
    error_page_file: Path | None = typer.Option(  # noqa: B008
        None, "--error-page-file", exists=True, dir_okay=False,
        help="Page HTML d'indisponibilité, servie quand aucun backend ne répond "
             "(7 Kio max, UTF-8).",
    ),
    reset_error_page: bool = typer.Option(
        False, "--reset-error-page", help="Rétablit la page d'indisponibilité par défaut.",
    ),
) -> None:
    """Modifie un load balancer (nom, page d'indisponibilité).

    Exemples :
      cetic lb update <lb> --error-page-file maintenance.html
      cetic lb update <lb> --reset-error-page
    """
    if error_page_file and reset_error_page:
        rprint("[red]Erreur : --error-page-file et --reset-error-page s'excluent.[/red]")
        raise typer.Exit(1)
    body: dict[str, object] = {}
    if name:
        body["name"] = name
    if error_page_file:
        try:
            body["custom_error_page_html"] = read_error_page(error_page_file)
        except ValueError as e:
            rprint(f"[red]Erreur : {e}[/red]")
            raise typer.Exit(1)
    elif reset_error_page:
        # Présence du champ à null = retour à la page par défaut.
        body["custom_error_page_html"] = None
    if not body:
        rprint("[red]Erreur : rien à modifier (--name, --error-page-file ou "
               "--reset-error-page).[/red]")
        raise typer.Exit(1)
    try:
        client.patch(f"/v1/load-balancers/{lb_id}", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Load balancer mis à jour.")


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
    """Attache une IP publique à un LB (adresse flottante)."""
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


# ---------------------------------------------------------------------------
# ACME / Let's Encrypt
# ---------------------------------------------------------------------------


@app.command(name="acme-providers")
def acme_providers() -> None:
    """Liste les providers DNS-01 supportés pour les certificats Let's Encrypt."""
    try:
        data = client.get("/v1/load-balancers/acme/dns-providers")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = []
    for key, spec in (data or {}).items():
        spec = spec or {}
        fields = spec.get("fields") or []
        rows.append({
            "key": key,
            "label": spec.get("label") or "—",
            "fields": ", ".join(str(f) for f in fields) or "—",
        })
    render_list(rows, title=f"Providers DNS-01 ({len(rows)})",
                columns=[("key", "Identifiant"), ("label", "Libellé"),
                         ("fields", "Credentials requis")])


@app.command(name="acme-retry")
def acme_retry(
    lb_id: str = typer.Argument(...),
    listener_id: str = typer.Argument(...),
) -> None:
    """Relance l'émission du certificat Let's Encrypt d'un listener HTTPS.

    Pour un listener qui utilise votre propre certificat, utilisez
    `cetic lb listener rotate-cert`.
    """
    try:
        client.post(
            f"/v1/load-balancers/{lb_id}/listeners/{listener_id}/acme/retry"
        )
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        if e.status_code == 409:
            rprint("[yellow]Pour remplacer votre certificat : cetic lb listener "
                   "rotate-cert LB LISTENER --cert-file … --key-file …[/yellow]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Réémission du certificat demandée.")


# ---------------------------------------------------------------------------
# Listeners
# ---------------------------------------------------------------------------


@listener_app.command(name="add")
def listener_add(
    lb_id: str = typer.Argument(...),
    protocol: str = typer.Option(..., "--protocol", help="tcp, http ou https."),
    port: int = typer.Option(..., "--port", min=1, max=65535, help="Port d'écoute."),
    algorithm: str = typer.Option("roundrobin", "--algorithm",
        help="Algorithme : roundrobin, leastconn, source ou random."),
    backend: list[str] = typer.Option(None, "--backend",  # noqa: B008
        help=f"Backend, répétable. Format : {_BACKEND_FORMAT}."),
    domain: str | None = typer.Option(None, "--domain", help="Domaine du certificat."),
    acme_challenge: str | None = typer.Option(None, "--acme-challenge",
        help="http01 ou dns01 (Let's Encrypt), ou custom (votre certificat)."),
    acme_dns_provider: str | None = typer.Option(None, "--acme-dns-provider",
        help="Provider DNS pour dns01 (cf. cetic lb acme-providers)."),
    acme_dns_credential: list[str] = typer.Option(None, "--acme-dns-credential",  # noqa: B008
        help="Credential DNS, répétable. Format : KEY=VALUE."),
    cert_file: Path | None = typer.Option(None, "--cert-file", exists=True, dir_okay=False,  # noqa: B008
        help="Certificat PEM (+ chaîne) pour --acme-challenge custom."),
    key_file: Path | None = typer.Option(None, "--key-file", exists=True, dir_okay=False,  # noqa: B008
        help="Clé privée PEM NON chiffrée pour --acme-challenge custom."),
) -> None:
    """Ajoute un listener à un load balancer existant.

    Exemple (votre certificat) :
      cetic lb listener add <lb> --protocol https --port 443 --domain www.example.com \\
        --acme-challenge custom --cert-file fullchain.pem --key-file privkey.pem \\
        --backend ctss:<scale-set-uuid>:8080
    """
    try:
        body = _build_listener(
            protocol=protocol, port=port, algorithm=algorithm, backends=backend or [],
            domain=domain, acme_challenge=acme_challenge,
            acme_dns_provider=acme_dns_provider,
            acme_dns_credentials=acme_dns_credential or [],
            cert_file=cert_file, key_file=key_file,
        )
    except ValueError as e:
        rprint(f"[red]Erreur : {e}[/red]")
        raise typer.Exit(1)
    try:
        client.post(f"/v1/load-balancers/{lb_id}/listeners", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Listener {protocol}:{port} ajouté.")


@listener_app.command(name="rotate-cert")
def listener_rotate_cert(
    lb_id: str = typer.Argument(...),
    listener_id: str = typer.Argument(...),
    cert_file: Path = typer.Option(..., "--cert-file", exists=True, dir_okay=False,  # noqa: B008
        help="Nouveau certificat PEM (+ chaîne)."),
    key_file: Path = typer.Option(..., "--key-file", exists=True, dir_okay=False,  # noqa: B008
        help="Clé privée PEM NON chiffrée correspondante."),
) -> None:
    """Remplace le certificat d'un listener HTTPS par le vôtre.

    Sert à la rotation d'un certificat fourni par vous (il ne se renouvelle
    pas tout seul), ou à remplacer un certificat Let's Encrypt par le vôtre.
    Le certificat est vérifié à l'envoi : clé correspondante, validité, nom
    couvert.
    """
    try:
        body = read_cert_pair(cert_file, key_file)
    except ValueError as e:
        rprint(f"[red]Erreur : {e}[/red]")
        raise typer.Exit(1)
    try:
        lb = client.post(
            f"/v1/load-balancers/{lb_id}/listeners/{listener_id}/acme/retry", json=body,
        )
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    listener = next((ls for ls in (lb or {}).get("listeners") or []
                     if ls.get("id") == listener_id), {})
    expires = cert_expiry_label(listener.get("tls_cert_not_after"))
    rprint(f"[green]✓[/green] Certificat remplacé (expire le {expires}).")


# ---------------------------------------------------------------------------
# Backends d'un listener
# ---------------------------------------------------------------------------


@backend_app.command(name="add")
def backend_add(
    lb_id: str = typer.Argument(...),
    listener_id: str = typer.Argument(...),
    container: str | None = typer.Option(None, "--container", help="UUID d'un container backend"),
    vm: str | None = typer.Option(None, "--vm", help="UUID d'une VM backend"),
    container_scale_set: str | None = typer.Option(
        None, "--container-scale-set",
        help="UUID d'un scale set de containers (cible dynamique)"),
    vm_scale_set: str | None = typer.Option(
        None, "--vm-scale-set", help="UUID d'un scale set de VM (cible dynamique)"),
    port: int = typer.Option(..., "--port", min=1, max=65535, help="Port d'écoute du backend"),
    weight: int = typer.Option(1, "--weight", min=0, max=256, help="Poids (0-256, défaut 1)"),
) -> None:
    """Ajoute un backend à un listener : container, VM ou scale set.

    Un scale set est UNE cible dynamique : toutes ses instances en service
    reçoivent le trafic, y compris celles ajoutées après coup.
    """
    targets = {
        "container_id": container,
        "vm_instance_id": vm,
        "container_scale_set_id": container_scale_set,
        "vm_scale_set_id": vm_scale_set,
    }
    chosen = {k: v for k, v in targets.items() if v}
    if len(chosen) != 1:
        rprint("[red]Erreur : fournir exactement un de --container, --vm, "
               "--container-scale-set ou --vm-scale-set.[/red]")
        raise typer.Exit(1)
    body: dict[str, object] = {"port": port, "weight": weight, **chosen}
    try:
        client.post(
            f"/v1/load-balancers/{lb_id}/listeners/{listener_id}/backends",
            json=body,
        )
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Backend ajouté.")


@backend_app.command(name="update")
def backend_update(
    lb_id: str = typer.Argument(...),
    listener_id: str = typer.Argument(...),
    backend_id: str = typer.Argument(...),
    port: int | None = typer.Option(None, "--port", min=1, max=65535, help="Nouveau port"),
    weight: int | None = typer.Option(None, "--weight", min=0, max=256, help="Nouveau poids (0-256)"),
) -> None:
    """Modifie le port et/ou le poids d'un backend."""
    body: dict[str, object] = {}
    if port is not None:
        body["port"] = port
    if weight is not None:
        body["weight"] = weight
    if not body:
        rprint("[red]Erreur : préciser --port et/ou --weight.[/red]")
        raise typer.Exit(1)
    try:
        client.patch(
            f"/v1/load-balancers/{lb_id}/listeners/{listener_id}/backends/{backend_id}",
            json=body,
        )
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Backend mis à jour.")


@backend_app.command(name="remove")
def backend_remove(
    lb_id: str = typer.Argument(...),
    listener_id: str = typer.Argument(...),
    backend_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Retire un backend d'un listener."""
    if not yes and not typer.confirm(f"Retirer le backend {backend_id} ?"):
        raise typer.Abort()
    try:
        client.delete(
            f"/v1/load-balancers/{lb_id}/listeners/{listener_id}/backends/{backend_id}"
        )
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Backend retiré.")
