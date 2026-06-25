"""cetic lb — load balancers CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._catalog import render_compute_plans
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Load Balancers CETIC Cloud")
backend_app = typer.Typer(help="Backends d'un listener LB")
app.add_typer(backend_app, name="backend")


@app.command()
def plans() -> None:
    """Liste les plans Load Balancer disponibles (lb-small/medium/large)."""
    render_compute_plans(kind="lb", title="Plans Load Balancer")

_LB_PLANS = ("small", "medium", "large")
_LB_PROTOCOLS = ("tcp", "http", "https")
_LB_ALGORITHMS = ("roundrobin", "leastconn", "source", "random")
_ACME_CHALLENGES = ("http01", "dns01")


def _parse_backend_spec(spec: str) -> dict:
    """container:UUID:PORT[:WEIGHT] ou vm:UUID:PORT[:WEIGHT] → dict backend API.

    Lève ValueError avec un message clair en français si le format est invalide.
    """
    parts = spec.split(":")
    if len(parts) not in (3, 4):
        raise ValueError(
            f"Backend invalide « {spec} ». Format attendu : "
            "container:UUID:PORT[:WEIGHT] ou vm:UUID:PORT[:WEIGHT]."
        )
    kind, uuid_, port_str = parts[0], parts[1], parts[2]
    if kind not in ("container", "vm"):
        raise ValueError(
            f"Backend « {spec} » : le type doit être « container » ou « vm »."
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
    if kind == "container":
        out["container_id"] = uuid_
    else:
        out["vm_instance_id"] = uuid_
    return out


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
    listeners = lb.get("listeners") or []
    if listeners:
        rows = [
            {"id": ls.get("id"), "protocol": ls.get("protocol"),
             "listen_port": ls.get("listen_port"), "algorithm": ls.get("algorithm"),
             "domain": ls.get("domain") or "—",
             "acme_status": ls.get("acme_status") or "—",
             "backends": str(len(ls.get("backends") or []))}
            for ls in listeners
        ]
        render_list(rows, title=f"Listeners ({len(rows)})",
                    columns=[("id", "ID"), ("protocol", "Proto"),
                             ("listen_port", "Port"), ("algorithm", "Algo"),
                             ("domain", "Domaine"), ("acme_status", "Cert"),
                             ("backends", "Backends")])


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
    # — Listener (optionnel, un seul définissable via flags) —
    listener_protocol: str | None = typer.Option(None, "--listener-protocol",
        help="Protocole du listener : tcp, http ou https."),
    listener_port: int | None = typer.Option(None, "--listener-port", min=1, max=65535,
        help="Port d'écoute du listener."),
    algorithm: str = typer.Option("roundrobin", "--algorithm",
        help="Algorithme : roundrobin, leastconn, source ou random."),
    backend: list[str] = typer.Option(None, "--backend",  # noqa: B008
        help="Backend, répétable. Format : container:UUID:PORT[:WEIGHT] ou vm:UUID:PORT[:WEIGHT]"),
    # — Certificat Let's Encrypt (listener https uniquement) —
    domain: str | None = typer.Option(None, "--domain",
        help="Domaine du certificat Let's Encrypt (ex: www.example.com)."),
    acme_challenge: str | None = typer.Option(None, "--acme-challenge",
        help="Challenge ACME : http01 ou dns01."),
    acme_dns_provider: str | None = typer.Option(None, "--acme-dns-provider",
        help="Provider DNS pour dns01 (cf. cetic lb acme-providers ; ex: cloudflare, "
             "ionos (champs prefix + secret))."),
    acme_dns_credential: list[str] = typer.Option(None, "--acme-dns-credential",  # noqa: B008
        help="Credential DNS, répétable. Format : KEY=VALUE (ex: api_token=xxx)."),
) -> None:
    """Crée un load balancer, avec optionnellement un listener HTTPS + Let's Encrypt.

    Le plan détermine la taille de la paire d'instances LB (HA active/passive).

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
        if listener_protocol not in _LB_PROTOCOLS:
            rprint(f"[red]Erreur : --listener-protocol doit être l'un de "
                   f"{', '.join(_LB_PROTOCOLS)}.[/red]")
            raise typer.Exit(1)
        if algorithm not in _LB_ALGORITHMS:
            rprint(f"[red]Erreur : --algorithm doit être l'un de "
                   f"{', '.join(_LB_ALGORITHMS)}.[/red]")
            raise typer.Exit(1)
        listener_dict = {
            "protocol": listener_protocol,
            "listen_port": listener_port,
            "algorithm": algorithm,
        }
        # Backends
        try:
            backends = [_parse_backend_spec(b) for b in backend]
        except ValueError as e:
            rprint(f"[red]Erreur : {e}[/red]")
            raise typer.Exit(1)
        if backends:
            listener_dict["backends"] = backends

        # ACME / Let's Encrypt
        if acme_challenge is not None:
            if acme_challenge not in _ACME_CHALLENGES:
                rprint(f"[red]Erreur : --acme-challenge doit être l'un de "
                       f"{', '.join(_ACME_CHALLENGES)}.[/red]")
                raise typer.Exit(1)
            if listener_protocol != "https":
                rprint("[red]Erreur : --acme-challenge requiert "
                       "--listener-protocol https.[/red]")
                raise typer.Exit(1)
            if not domain:
                rprint("[red]Erreur : --acme-challenge requiert --domain.[/red]")
                raise typer.Exit(1)
            listener_dict["domain"] = domain
            listener_dict["acme_challenge"] = acme_challenge
            if acme_challenge == "dns01":
                if not acme_dns_provider:
                    rprint("[red]Erreur : le challenge dns01 requiert "
                           "--acme-dns-provider (cf. cetic lb acme-providers).[/red]")
                    raise typer.Exit(1)
                if not acme_dns_credential:
                    rprint("[red]Erreur : le challenge dns01 requiert au moins "
                           "un --acme-dns-credential KEY=VALUE.[/red]")
                    raise typer.Exit(1)
                try:
                    creds = _parse_credentials(acme_dns_credential)
                except ValueError as e:
                    rprint(f"[red]Erreur : {e}[/red]")
                    raise typer.Exit(1)
                listener_dict["acme_dns_provider"] = acme_dns_provider
                listener_dict["acme_dns_credentials"] = creds
        elif domain:
            # Domaine fourni sans ACME : on le passe quand même (cert manuel futur).
            listener_dict["domain"] = domain

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
    if listener_dict is not None:
        body["listeners"] = [listener_dict]
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
    """État UP/DOWN des backends d'un LB (état temps réel)."""
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
    """Relance l'émission du certificat Let's Encrypt d'un listener HTTPS."""
    try:
        client.post(
            f"/v1/load-balancers/{lb_id}/listeners/{listener_id}/acme/retry"
        )
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Réémission du certificat demandée.")


# ---------------------------------------------------------------------------
# Backends d'un listener
# ---------------------------------------------------------------------------


@backend_app.command(name="add")
def backend_add(
    lb_id: str = typer.Argument(...),
    listener_id: str = typer.Argument(...),
    container: str | None = typer.Option(None, "--container", help="UUID d'un container backend"),
    vm: str | None = typer.Option(None, "--vm", help="UUID d'une VM backend"),
    port: int = typer.Option(..., "--port", min=1, max=65535, help="Port d'écoute du backend"),
    weight: int = typer.Option(1, "--weight", min=0, max=256, help="Poids (0-256, défaut 1)"),
) -> None:
    """Ajoute un backend (container OU VM) à un listener."""
    if bool(container) == bool(vm):
        rprint("[red]Erreur : fournir exactement un de --container ou --vm.[/red]")
        raise typer.Exit(1)
    body: dict[str, object] = {"port": port, "weight": weight}
    if container:
        body["container_id"] = container
    else:
        body["vm_instance_id"] = vm
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
