"""cetic k8s — clusters Kubernetes managés (CLKS)."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._catalog import render_compute_plans
from cetic.commands._render import render_list, render_one


def _parse_label_arg(label_str: str) -> tuple[str, str]:
    """Parse `key=value` → (key, value). Raise typer.BadParameter if invalid."""
    if "=" not in label_str:
        raise typer.BadParameter(f"Invalid label '{label_str}', expected 'key=value'")
    key, _, value = label_str.partition("=")
    key = key.strip()
    if not key:
        raise typer.BadParameter(f"Invalid label '{label_str}', empty key")
    return key, value


def _parse_taint_arg(taint_str: str) -> dict[str, str | None]:
    """Parse `key=value:effect` or `key:effect` → dict. Raise on invalid."""
    if ":" not in taint_str:
        raise typer.BadParameter(
            f"Invalid taint '{taint_str}', expected 'key=value:effect' "
            f"(effect ∈ NoSchedule|PreferNoSchedule|NoExecute)"
        )
    head, _, effect = taint_str.rpartition(":")
    effect = effect.strip()
    if effect not in ("NoSchedule", "PreferNoSchedule", "NoExecute"):
        raise typer.BadParameter(
            f"Invalid taint effect '{effect}', must be NoSchedule|PreferNoSchedule|NoExecute"
        )
    if "=" in head:
        key, _, value = head.partition("=")
        key = key.strip()
        value = value.strip() or None
    else:
        key = head.strip()
        value = None
    if not key:
        raise typer.BadParameter(f"Invalid taint '{taint_str}', empty key")
    return {"key": key, "value": value, "effect": effect}

app = typer.Typer(help="CETIC Cloud Kubernetes Service (CLKS)")
pool_app = typer.Typer(help="Node pools d'un cluster K8s")
app.add_typer(pool_app, name="pool")


VALID_TIERS = ("dev", "prod")
VALID_KUBECONFIG_MODES = ("private", "public")

# Rich styling par tier — aligné console / docs.
_TIER_STYLE = {
    "dev": "cyan",
    "prod": "yellow",  # `amber` n'existe pas natif Rich ; `yellow` est la couleur amber/ambre.
}


def _fmt_tier(tier: str | None) -> str:
    """Renvoie le tier coloré pour le rendu table Rich."""
    if not tier:
        return "—"
    style = _TIER_STYLE.get(tier, "white")
    return f"[{style}]{tier}[/{style}]"


@app.command(name="list")
def list_clusters(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les clusters K8s du tenant."""
    try:
        items = client.get("/v1/k8s/clusters", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": c["id"], "name": c["name"], "region": c["region"],
         "k8s_version": c.get("k8s_version", "—"),
         "tier": _fmt_tier(c.get("tier")),
         "status": c["status"]}
        for c in items
    ]
    render_list(rows, title=f"Clusters K8s ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("k8s_version", "Version"), ("tier", "Tier"),
                         ("status", "Statut")])


@app.command()
def get(cluster_id: str = typer.Argument(...)) -> None:
    """Détails d'un cluster."""
    try:
        c = client.get(f"/v1/k8s/clusters/{cluster_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(c, title=f"Cluster {c.get('name', cluster_id)}")


@app.command()
def kubeconfig(
    cluster_id: str = typer.Argument(...),
    mode: str = typer.Option(
        "private",
        "--mode",
        help="Endpoint du kubeconfig : private (VNet, défaut) | public (Gateway).",
        case_sensitive=False,
    ),
) -> None:
    """Récupère le kubeconfig admin du cluster (à coller dans `~/.kube/config`)."""
    mode_norm = mode.lower()
    if mode_norm not in VALID_KUBECONFIG_MODES:
        rprint(
            f"[red]--mode invalide : '{mode}'. "
            f"Valeurs autorisées : {', '.join(VALID_KUBECONFIG_MODES)}.[/red]"
        )
        raise typer.Exit(1)
    try:
        kc = client.get(
            f"/v1/k8s/clusters/{cluster_id}/kubeconfig",
            params={"mode": mode_norm},
        )
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    if isinstance(kc, dict) and "kubeconfig" in kc:
        print(kc["kubeconfig"])
    else:
        print(kc)


@app.command()
def plans() -> None:
    """Liste les plans utilisables pour les node pools K8s (CCKS).

    Restriction CCKS : seuls les plans assez grands pour kubelet + CNI sont
    proposés (les variantes nano/micro sont exclues côté backend).
    """
    render_compute_plans(kind="k8s_node", title="Plans node pool K8s")


@app.command()
def versions(
    region: str | None = typer.Option(None, "--region", "-r", help="Filtrer par région"),
) -> None:
    """Liste les versions Kubernetes disponibles (images CAPI buildées)."""
    try:
        data = client.get("/v1/k8s/templates", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    # Une version peut exister sur plusieurs OS/régions ; on dédoublonne par
    # (version, région) et on agrège les OS proposés.
    seen: dict[tuple[str, str], dict] = {}
    for t in data:
        ver = t.get("k8s_version", "")
        reg = t.get("region", "—")
        key = (ver, reg)
        row = seen.setdefault(key, {"version": ver, "region": reg, "_os": set()})
        if t.get("os_label"):
            row["_os"].add(t["os_label"])
    rows = [
        {"version": r["version"], "region": r["region"], "os": ", ".join(sorted(r["_os"])) or "—"}
        for r in seen.values()
    ]
    rows.sort(key=lambda r: (r["region"], r["version"]))
    render_list(rows, title=f"Versions Kubernetes ({len(rows)})",
                columns=[("version", "Version"), ("region", "Région"), ("os", "OS")])


@app.command()
def templates(
    region: str | None = typer.Option(None, "--region", "-r", help="Filtrer par région"),
) -> None:
    """Liste les templates OS Kubernetes disponibles (images CAPI buildées).

    La clé (`--template` à la création) est l'`os_key` (ex `kube-v1-34-6`).
    """
    try:
        data = client.get("/v1/k8s/templates", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "os_key": t.get("os_key", ""),
            "display_name": t.get("display_name", ""),
            "k8s_version": t.get("k8s_version", "—"),
            "os": t.get("os_label", "—"),
            "region": t.get("region", "—"),
            "built_at": (t.get("built_at") or "")[:10],
        }
        for t in data
    ]
    render_list(rows, title=f"Templates Kubernetes ({len(rows)})",
                columns=[("os_key", "Clé"), ("display_name", "Nom"),
                         ("k8s_version", "Version"), ("os", "OS"),
                         ("region", "Région"), ("built_at", "Buildé le")])


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
    vpc_id: str = typer.Option(..., "--vpc"),
    vnet_id: str = typer.Option(..., "--vnet"),
    k8s_version: str = typer.Option("v1.31.0", "--version"),
    os_template_key: str = typer.Option(..., "--template", help="Clé de template QEMU (ex: clks-capi-debian-13)"),
    tier: str = typer.Option(
        "dev",
        "--tier",
        help="Tier: dev=1 LXC proxy, prod=2 LXC HA actif/passif",
        case_sensitive=False,
    ),
    # Pool initial
    pool_name: str = typer.Option("default", "--pool-name"),
    pool_plan: str = typer.Option("small", "--pool-plan"),
    pool_replicas: int = typer.Option(1, "--pool-replicas"),
    pool_min: int | None = typer.Option(None, "--pool-min", help="Active l'autoscaler"),
    pool_max: int | None = typer.Option(None, "--pool-max"),
    # Autoscaler timers
    scale_down_delay: str = typer.Option("10m", "--scale-down-delay", help="Délai avant scale-down post scale-up"),
    scale_down_unneeded: str = typer.Option("10m", "--scale-down-unneeded", help="Temps avant suppression nœud inutile"),
    # Ingress controller
    no_ingress: bool = typer.Option(False, "--no-ingress", help="Désactiver l'ingress controller"),
    ingress_scope: str = typer.Option("internal", "--ingress-scope", help="internal | external"),
    ingress_class: str = typer.Option("incluster", "--ingress-class", help="incluster | managed"),
    ingress_ip_id: str | None = typer.Option(None, "--ingress-ip", help="UUID d'une IP publique pré-réservée pour l'ingress"),
    ingress_internal_ip: str | None = typer.Option(None, "--ingress-internal-ip"),
    # API server
    apiserver_ip_id: str | None = typer.Option(None, "--apiserver-ip", help="UUID d'une IP publique pour l'apiserver"),
    apiserver_internal_ip: str | None = typer.Option(None, "--apiserver-internal-ip"),
) -> None:
    """Crée un cluster K8s (provisioning ~5-15 min, asynchrone)."""
    tier_norm = tier.lower()
    if tier_norm not in VALID_TIERS:
        rprint(
            f"[red]--tier invalide : '{tier}'. "
            f"Valeurs autorisées : {', '.join(VALID_TIERS)}.[/red]"
        )
        raise typer.Exit(1)

    pool_body: dict = {"name": pool_name, "plan": pool_plan, "replicas": pool_replicas}
    if pool_min is not None:
        pool_body["min_size"] = pool_min
    if pool_max is not None:
        pool_body["max_size"] = pool_max

    body: dict = {
        "name": name,
        "region": region,
        "vpc_id": vpc_id,
        "vnet_id": vnet_id,
        "k8s_version": k8s_version,
        "os_template_key": os_template_key,
        "tier": tier_norm,
        "initial_pool": pool_body,
        "autoscaler_scale_down_delay_after_add": scale_down_delay,
        "autoscaler_scale_down_unneeded_time": scale_down_unneeded,
        "ingress_controller_enabled": not no_ingress,
        "ingress_controller_scope": ingress_scope,
        "ingress_controller_class": ingress_class,
    }
    if ingress_ip_id:
        body["ingress_public_ip_id"] = ingress_ip_id
    if ingress_internal_ip:
        body["ingress_internal_ip"] = ingress_internal_ip
    if apiserver_ip_id:
        body["apiserver_public_ip_id"] = apiserver_ip_id
    if apiserver_internal_ip:
        body["apiserver_internal_ip"] = apiserver_internal_ip

    try:
        c = client.post("/v1/k8s/clusters", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Cluster créé : [bold]{c['id']}[/bold] (status: {c.get('status', '?')})")
    rprint("[dim]Le provisioning prend 5-15 min. Suivre avec : cetic k8s get {id}[/dim]")


@app.command()
def upgrade(
    cluster_id: str = typer.Argument(...),
    k8s_version: str = typer.Option(..., "--version", "-v", help="Ex: v1.32.0"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Upgrade la version Kubernetes du cluster (rolling, via CAPI/CAPMOX)."""
    if not yes and not typer.confirm(f"Upgrader le cluster {cluster_id} vers {k8s_version} ?"):
        raise typer.Abort()
    try:
        c = client.post(f"/v1/k8s/clusters/{cluster_id}/upgrade-version",
                        json={"k8s_version": k8s_version})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Upgrade déclenché → status : {c.get('status', '?')}")


@app.command(name="attach-ip")
def attach_ip(
    cluster_id: str = typer.Argument(...),
    public_ip_id: str = typer.Option(..., "--ip", help="UUID d'une IP publique du tenant"),
) -> None:
    """Attache une IP publique à l'apiserver du cluster."""
    try:
        c = client.post(f"/v1/k8s/clusters/{cluster_id}/attach-ip",
                        json={"public_ip_id": public_ip_id})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] IP attachée. Apiserver : {c.get('public_ip_address', '?')}")


@app.command(name="detach-ip")
def detach_ip(
    cluster_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Détache l'IP publique de l'apiserver du cluster."""
    if not yes and not typer.confirm(f"Détacher l'IP publique du cluster {cluster_id} ?"):
        raise typer.Abort()
    try:
        client.post(f"/v1/k8s/clusters/{cluster_id}/detach-ip")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] IP détachée.")


@app.command()
def delete(
    cluster_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un cluster (cascade nodes + namespaces)."""
    if not yes and not typer.confirm(f"Supprimer le cluster {cluster_id} (CASCADE) ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/k8s/clusters/{cluster_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Cluster en cours de suppression.")


# ── Node pools ────────────────────────────────────────────────────────────


@pool_app.command(name="list")
def list_pools(cluster_id: str = typer.Argument(...)) -> None:
    """Liste les node pools d'un cluster."""
    try:
        items = client.get(f"/v1/k8s/clusters/{cluster_id}/node-pools")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": p["id"], "name": p["name"], "plan": p["plan"],
         "replicas": p["replicas"],
         "min": p.get("min_size") or "—",
         "max": p.get("max_size") or "—",
         "status": p["status"]}
        for p in items
    ]
    render_list(rows, title=f"Pools du cluster {cluster_id[:8]} ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("plan", "Plan"),
                         ("replicas", "Replicas"), ("min", "Min"), ("max", "Max"),
                         ("status", "Statut")])


@pool_app.command()
def create(
    cluster_id: str = typer.Argument(...),
    name: str = typer.Option(..., "--name", "-n"),
    plan: str = typer.Option("small", "--plan", "-p"),
    replicas: int = typer.Option(1, "--replicas"),
    min_size: int | None = typer.Option(None, "--min", help="Active autoscaler"),
    max_size: int | None = typer.Option(None, "--max"),
    labels: list[str] | None = typer.Option(
        None, "--label",
        help="Label nœud au format key=value (répétable). Ex: --label env=prod --label zone=fr"
    ),
    taints: list[str] | None = typer.Option(
        None, "--taint",
        help=(
            "Taint nœud au format key=value:effect ou key:effect (répétable). "
            "effect ∈ NoSchedule|PreferNoSchedule|NoExecute"
        ),
    ),
) -> None:
    """Crée un node pool."""
    body: dict = {"name": name, "plan": plan, "replicas": replicas}
    if min_size is not None:
        body["min_size"] = min_size
    if max_size is not None:
        body["max_size"] = max_size
    if labels:
        parsed_labels = [_parse_label_arg(lbl) for lbl in labels]
        body["labels"] = {k: v for k, v in parsed_labels}
    if taints:
        body["taints"] = [_parse_taint_arg(t) for t in taints]
    try:
        p = client.post(f"/v1/k8s/clusters/{cluster_id}/node-pools", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Pool créé : [bold]{p['id']}[/bold]")


@pool_app.command()
def update(
    cluster_id: str = typer.Argument(...),
    pool_id: str = typer.Argument(...),
    replicas: int | None = typer.Option(None, "--replicas"),
    min_size: int | None = typer.Option(None, "--min"),
    max_size: int | None = typer.Option(None, "--max"),
    labels: list[str] | None = typer.Option(
        None, "--label",
        help=(
            "Label nœud au format key=value (répétable). "
            "Remplace l'ensemble des labels existants si au moins un est passé."
        ),
    ),
    taints: list[str] | None = typer.Option(
        None, "--taint",
        help=(
            "Taint nœud au format key=value:effect ou key:effect (répétable). "
            "Remplace l'ensemble des taints existants si au moins un est passé. "
            "effect ∈ NoSchedule|PreferNoSchedule|NoExecute"
        ),
    ),
    labels_clear: bool = typer.Option(
        False, "--labels-clear",
        help="Vide tous les labels du pool (incompatible avec --label).",
    ),
    taints_clear: bool = typer.Option(
        False, "--taints-clear",
        help="Vide tous les taints du pool (incompatible avec --taint).",
    ),
) -> None:
    """Modifie un pool (replicas, bornes autoscaler, labels, taints)."""
    if labels and labels_clear:
        rprint("[red]--label et --labels-clear sont incompatibles.[/red]")
        raise typer.Exit(1)
    if taints and taints_clear:
        rprint("[red]--taint et --taints-clear sont incompatibles.[/red]")
        raise typer.Exit(1)
    body: dict = {}
    if replicas is not None:
        body["replicas"] = replicas
    if min_size is not None:
        body["min_size"] = min_size
    if max_size is not None:
        body["max_size"] = max_size
    if labels:
        parsed_labels = [_parse_label_arg(lbl) for lbl in labels]
        body["labels"] = {k: v for k, v in parsed_labels}
    elif labels_clear:
        body["labels"] = {}
    if taints:
        body["taints"] = [_parse_taint_arg(t) for t in taints]
    elif taints_clear:
        body["taints"] = []
    if not body:
        rprint("[yellow]Aucun paramètre à modifier.[/yellow]")
        raise typer.Exit(0)
    try:
        p = client.patch(f"/v1/k8s/clusters/{cluster_id}/node-pools/{pool_id}", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Pool mis à jour : {p.get('name', pool_id)} → replicas={p.get('replicas', '?')}")


@pool_app.command()
def scale(
    cluster_id: str = typer.Argument(...),
    pool_id: str = typer.Argument(...),
    replicas: int = typer.Option(..., "--replicas", "-n"),
) -> None:
    """Change le nombre de replicas d'un pool."""
    try:
        client.post(f"/v1/k8s/clusters/{cluster_id}/node-pools/{pool_id}/scale",
                    json={"replicas": replicas})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Scale → {replicas}.")


@pool_app.command()
def delete(
    cluster_id: str = typer.Argument(...),
    pool_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un pool."""
    if not yes and not typer.confirm(f"Supprimer le pool {pool_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/k8s/clusters/{cluster_id}/node-pools/{pool_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Pool supprimé.")
