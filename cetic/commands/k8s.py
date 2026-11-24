"""cetic k8s — clusters Kubernetes managés (CLKS)."""

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich import print as rprint

from cetic import client, config
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


# ── Erreurs de l'API ──────────────────────────────────────────────────────

#: Codes machine des refus CCKS (#2143) — `detail.code` du corps de l'erreur.
CODE_PLAN_RECREATION = "ccks_plan_change_requires_recreation"
CODE_PLAN_NODE_LIMIT = "ccks_plan_node_limit"
CODE_IP_BLOCK_NOT_EXTENDABLE = "ccks_node_ip_block_not_extendable"


def _error_hint(e: client.APIError) -> str | None:
    """Conseil actionnable selon le `code` du corps (ou le statut 402).

    Le message de l'API (`e.detail`) dit ce qui est refusé ; le conseil dit
    quoi faire avec le CLI. `None` quand l'erreur n'appelle rien de plus."""
    body = e.body or {}
    if e.code == CODE_PLAN_RECREATION:
        return _recreation_hint(body.get("to") or "<plan>")
    if e.code == CODE_PLAN_NODE_LIMIT:
        plan = body.get("plan") or "?"
        return (
            f"Plan {plan} : {body.get('max_nodes', '?')} nœuds au plus, "
            f"{body.get('requested_nodes', '?')} demandés (somme, par pool, de "
            "max(replicas, max)). Réduisez --replicas / --max, ou changez de "
            "plan (cetic k8s cluster-plans, cetic k8s set-plan)."
        )
    if e.code == CODE_IP_BLOCK_NOT_EXTENDABLE:
        return (
            f"Bloc d'adresses des nœuds : {body.get('node_ip_block_size', '?')}, "
            f"il en faut {body.get('required', '?')}. Ce cluster ne peut pas "
            "monter de plan à chaud : recréez-le avec le plan visé."
        )
    if e.status_code == 402:
        url = body.get("action_url")
        return "Plafond de dépenses atteint : relevez-le avant de réessayer" + (
            f" ({url})." if url else " (console, Facturation)."
        )
    return None


def _recreation_hint(cible: str) -> str:
    return (
        "Starter ↔ Standard/Pro ne se change pas à chaud : créez un nouveau "
        f"cluster avec --plan {cible} (cetic k8s create … --plan {cible}) "
        "puis migrez les charges."
    )


def _fail(e: client.APIError) -> NoReturn:
    """Affiche l'erreur de l'API (et son conseil éventuel) puis sort en 1."""
    # stderr : `kubeconfig > fichier` et `-o json` ne reçoivent jamais l'erreur.
    rprint(f"[red]Erreur : {e.detail}[/red]", file=sys.stderr)
    hint = _error_hint(e)
    if hint:
        rprint(f"[dim]{hint}[/dim]", file=sys.stderr)
    raise typer.Exit(1)


def _warn(message: str) -> None:
    """Avertissement sur stderr : la sortie json/yaml (stdout) reste lisible."""
    rprint(f"[yellow]⚠[/yellow] {message}", file=sys.stderr)


def _is_table() -> bool:
    return config.get_output() not in ("json", "yaml")


app = typer.Typer(help="CETIC Cloud Kubernetes Service (CLKS)")
pool_app = typer.Typer(help="Node pools d'un cluster K8s")
app.add_typer(pool_app, name="pool")
maintenance_app = typer.Typer(help="Mise à jour automatique d'un cluster (patchs, fenêtre, gels)")
app.add_typer(maintenance_app, name="maintenance")
oidc_app = typer.Typer(help="SSO (OIDC) d'un cluster : connexion kubectl par votre IdP")
app.add_typer(oidc_app, name="oidc")


VALID_TIERS = ("dev", "prod")
VALID_KUBECONFIG_MODES = ("private", "public", "sso")

# Familles d'OS proposées pour les nœuds K8s (slug backend `os_image`).
VALID_OS = ("flatcar", "ubuntu", "rocky9")
_OS_LABEL = {
    "flatcar": "Flatcar",
    "ubuntu": "Ubuntu",
    "rocky9": "Rocky Linux 9",
}


def _fmt_os(os_slug: str | None) -> str:
    """Renvoie le libellé lisible d'un slug d'OS (`os_image`)."""
    if not os_slug:
        return "—"
    return _OS_LABEL.get(os_slug, os_slug)


# Version K8s : `vX.Y.Z` ou `X.Y.Z` (le backend re-valide ≤ control plane → 422 sinon).
_K8S_VERSION_RE = re.compile(r"^v?\d+\.\d+\.\d+$")


def _validate_k8s_version(version: str) -> str:
    """Valide le format `vX.Y.Z` / `X.Y.Z`. Raise typer.BadParameter sinon."""
    if not _K8S_VERSION_RE.match(version):
        raise typer.BadParameter(
            f"Version Kubernetes invalide : '{version}'. Format attendu : vX.Y.Z (ex: v1.32.0)."
        )
    return version


def _fmt_pool_version(pool_version: str | None, cp_version: str | None = None) -> str:
    """Affiche la version K8s d'un pool : la version pinée, ou « (héritée) » si null.

    Quand la version du control plane est connue, on l'affiche entre parenthèses
    pour expliciter ce qui est réellement déployé : « (héritée: v1.31.0) »."""
    if pool_version:
        return pool_version
    if cp_version:
        return f"(héritée: {cp_version})"
    return "(héritée)"

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


# ── Plans du plan de contrôle (#2143) ─────────────────────────────────────

VALID_PLANS = ("starter", "standard", "pro")
# `tier` (déprécié) ↔ `plan` : mêmes correspondances que l'API.
_PLAN_FROM_TIER = {"dev": "starter", "prod": "standard"}
_TIER_FROM_PLAN = {"starter": "dev", "standard": "prod", "pro": "prod"}
_PLAN_STYLE = {"starter": "cyan", "standard": "yellow", "pro": "magenta"}

# Disque OS par défaut d'un pool (`CCKS_POOL_DISK_DEFAULT_GB` côté API).
POOL_DISK_DEFAULT_GB = 20
_DISK_HELP = (
    f"Taille du disque OS (Go) de chaque nœud. Défaut : {POOL_DISK_DEFAULT_GB} Go "
    "(ou le disque minimal du plan s'il est plus grand). Fixée à la création "
    "du pool."
)


def _cluster_plan(c: dict) -> str | None:
    """Plan du cluster ; une API antérieure à #2143 n'expose que `tier`."""
    return c.get("plan") or _PLAN_FROM_TIER.get(c.get("tier") or "")


def _fmt_plan(plan: str | None) -> str:
    if not plan:
        return "—"
    style = _PLAN_STYLE.get(plan, "white")
    return f"[{style}]{plan}[/{style}]"


def _normalize_plan(plan: str) -> str:
    p = plan.lower()
    if p not in VALID_PLANS:
        rprint(
            f"[red]--plan invalide : '{plan}'. "
            f"Valeurs autorisées : {', '.join(VALID_PLANS)}.[/red]"
        )
        raise typer.Exit(1)
    return p


def _fetch_cluster_plans() -> list[dict]:
    try:
        return client.get("/v1/k8s/clusters/plans")
    except client.APIError as e:
        _fail(e)


def _plan_entry(plan: str | None) -> dict | None:
    """Entrée du catalogue d'un plan, best-effort (`None` si illisible) :
    l'affichage d'un cluster ne doit pas échouer faute de catalogue."""
    if not plan:
        return None
    try:
        plans = client.get("/v1/k8s/clusters/plans")
    except Exception:  # noqa: BLE001 — lecture de confort
        return None
    return next((p for p in plans or [] if p.get("key") == plan), None)


def _fmt_price(p: dict) -> str:
    monthly = p.get("price_monthly")
    if monthly is None:
        return "—"
    try:
        if float(monthly) == 0:
            return "Gratuit"
    except ValueError:
        pass
    return f"{monthly} {p.get('currency') or 'EUR'}/mois"


# ── Mises à niveau (#2143) ────────────────────────────────────────────────

_UPGRADE_TYPE = {
    "plan_de_controle": "plan de contrôle",
    "pool": "pool",
    "patch": "patch",
    "mineure": "mineure",
}
_UPGRADE_STATUT = {
    "en_cours": "en cours",
    "en_pause": "en pause",
    "terminee": "terminée",
    "echec": "échec",
    "annulee": "annulée",
    "en_attente": "en attente",
}


def _etapes_annulees(etapes: list[dict]) -> int:
    return sum(1 for e in etapes if e.get("statut") == "annulee")


def _fmt_patch_disponible(p: dict | None) -> str | None:
    """« v1.31.4 (plan de contrôle + 2 pool(s), ~25 min) »."""
    if not p:
        return None
    cibles = []
    if p.get("plan_de_controle"):
        cibles.append("plan de contrôle")
    pools = p.get("pools") or []
    if pools:
        cibles.append(f"{len(pools)} pool(s)")
    parts = [" + ".join(cibles)] if cibles else []
    if p.get("duree_estimee_minutes"):
        parts.append(f"~{p['duree_estimee_minutes']} min")
    txt = p.get("version", "?") + (f" ({', '.join(parts)})" if parts else "")
    if p.get("templates_ok") is False:
        txt += " — template manquant"
    return txt


def _fmt_upgrade(op: dict | None) -> str | None:
    """« patch v1.31.3 → v1.31.4, en cours (manuelle, 1/3 étapes) »."""
    if not op:
        return None
    etapes = op.get("etapes") or []
    faites = sum(1 for e in etapes if e.get("statut") == "terminee")
    details = [_UPGRADE_STATUT.get(op.get("statut", ""), op.get("statut") or "?")]
    extra = [op["origine"]] if op.get("origine") else []
    if etapes:
        extra.append(f"{faites}/{len(etapes)} étapes")
        if _etapes_annulees(etapes):
            extra.append(f"{_etapes_annulees(etapes)} annulée(s)")
    return (
        f"{_UPGRADE_TYPE.get(op.get('type', ''), op.get('type') or '?')} "
        f"{op.get('version_depart') or '?'} → {op.get('version_cible', '?')}, "
        f"{details[0]}" + (f" ({', '.join(extra)})" if extra else "")
    )


def _fmt_update_column(c: dict) -> str:
    """Colonne « Mise à jour » de la liste : l'opération en cours prime sur le
    patch disponible."""
    op = c.get("upgrade_en_cours")
    if op:
        return f"{_UPGRADE_STATUT.get(op.get('statut', ''), op.get('statut'))} → {op.get('version_cible', '?')}"
    p = c.get("patch_disponible")
    if p:
        return f"patch {p.get('version', '?')} disponible"
    return "—"


@app.command(name="list")
def list_clusters(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les clusters K8s du tenant."""
    try:
        items = client.get("/v1/k8s/clusters", params={"region": region} if region else None)
    except client.APIError as e:
        _fail(e)
    rows = [
        {"id": c["id"], "name": c["name"], "region": c["region"],
         "k8s_version": c.get("k8s_version", "—"),
         "os": _fmt_os(c.get("os_image")),
         "plan": _fmt_plan(_cluster_plan(c)) if _is_table() else _cluster_plan(c),
         "tier": _fmt_tier(c.get("tier")) if _is_table() else c.get("tier"),
         "status": c["status"],
         "patch_disponible": c.get("patch_disponible"),
         "upgrade_en_cours": c.get("upgrade_en_cours")}
        for c in items
    ]
    if _is_table():
        # Colonne de lecture humaine : jamais dans le JSON/YAML.
        for row, c in zip(rows, items, strict=True):
            row["update"] = _fmt_update_column(c)
    render_list(rows, title=f"Clusters K8s ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("k8s_version", "Version"), ("os", "OS"),
                         ("plan", "Plan"), ("status", "Statut"),
                         ("update", "Mise à jour")])


@app.command()
def get(cluster_id: str = typer.Argument(...)) -> None:
    """Détails d'un cluster."""
    try:
        c = client.get(f"/v1/k8s/clusters/{cluster_id}")
    except client.APIError as e:
        _fail(e)
    # Les champs `proxy_*` ne concernent pas le client : on les retire de la
    # sortie (table comme JSON/YAML).
    if isinstance(c, dict):
        c = {k: v for k, v in c.items() if not k.startswith("proxy_")}
    # Libellé OS lisible à côté du slug brut `os_image` (laissé intact pour les
    # scripts JSON/YAML).
    if "os_image" in c:
        c["os"] = _fmt_os(c.get("os_image"))
    # #2143 — plan et sa limite de nœuds (le cluster n'expose pas `max_nodes` :
    # lu dans le catalogue des plans, best-effort).
    plan = _cluster_plan(c)
    if plan:
        c["plan"] = plan
    if _is_table():
        # `max_nodes` n'est pas un champ de l'API : affiché en table seulement.
        entry = _plan_entry(plan)
        if entry and entry.get("max_nodes") is not None:
            c["max_nodes"] = entry["max_nodes"]
        c = _cluster_table_view(c)
    render_one(c, title=f"Cluster {c.get('name', cluster_id)}")


def _fmt_pool_line(p: dict, cp_version: str | None) -> str:
    disk = f"{p['disk_gb']} Go" if p.get("disk_gb") else "disque du plan"
    return (
        f"{p.get('name', '?')} : {p.get('plan', '?')}, {p.get('replicas', '?')} nœud(s), "
        f"{disk}, {_fmt_pool_version(p.get('k8s_version'), cp_version)}, "
        f"{p.get('status', '?')}"
    )


def _cluster_table_view(c: dict) -> dict:
    """Rendu table de `get` : objets imbriqués résumés en une ligne lisible.

    JSON/YAML gardent les objets bruts (scripts)."""
    out = dict(c)
    out["plan"] = _fmt_plan(c.get("plan"))
    if c.get("etcd_volume_gb") is not None:
        out["etcd_volume_gb"] = f"{c['etcd_volume_gb']} Gi"
    if "patch_disponible" in c:
        out["patch_disponible"] = _fmt_patch_disponible(c.get("patch_disponible"))
    if "upgrade_en_cours" in c:
        out["upgrade_en_cours"] = _fmt_upgrade(c.get("upgrade_en_cours"))
    if "oidc" in c:
        o = c.get("oidc")
        out["oidc"] = f"{o.get('issuer_url')} (client {o.get('client_id')})" if o else None
    if isinstance(c.get("node_pools"), list):
        out["node_pools"] = " ; ".join(
            _fmt_pool_line(p, c.get("k8s_version")) for p in c["node_pools"]
        ) or None
    return out


@app.command()
def kubeconfig(
    cluster_id: str = typer.Argument(...),
    mode: str = typer.Option(
        "private",
        "--mode",
        help=(
            "Endpoint du kubeconfig : private (VNet, défaut) | public (Gateway) | "
            "sso (endpoint public, connexion par votre fournisseur d'identité via "
            "kubelogin — cf. cetic k8s oidc set)."
        ),
        case_sensitive=False,
    ),
) -> None:
    """Récupère le kubeconfig du cluster (à coller dans `~/.kube/config`).

    `private` / `public` : kubeconfig admin par certificat client.
    `sso` : aucun certificat ; `kubectl` lance `kubectl oidc-login` (plugin
    kubelogin, à installer) et les droits viennent des (Cluster)RoleBinding
    posés sur vos groupes (préfixe `oidc:` par défaut).
    """
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
        _fail(e)
    if isinstance(kc, dict) and "kubeconfig" in kc:
        print(kc["kubeconfig"])
    else:
        print(kc)
    if mode_norm == "sso":
        # Sur stderr : `> ~/.kube/config` ne doit recevoir que le YAML.
        rprint(
            "[dim]Kubeconfig SSO : nécessite le plugin kubelogin "
            "(`kubectl krew install oidc-login`). Sans (Cluster)RoleBinding sur "
            "vos groupes, l'utilisateur n'a aucun droit.[/dim]",
            file=sys.stderr,
        )


@app.command()
def plans() -> None:
    """Liste les plans utilisables pour les node pools K8s (CCKS).

    Restriction CCKS : seuls les plans assez grands pour kubelet + CNI sont
    proposés (les variantes nano/micro sont exclues côté backend).
    """
    render_compute_plans(kind="k8s_node", title="Plans node pool K8s")


@app.command(name="cluster-plans")
def cluster_plans() -> None:
    """Liste les plans du cluster (plan de contrôle) : starter, standard, pro.

    Le plan fixe la haute disponibilité, le nombre maximal de nœuds (somme,
    par pool, de max(replicas, max)), le volume etcd et le prix. À ne pas
    confondre avec `cetic k8s plans` (gabarits des nœuds des pools).
    """
    data = _fetch_cluster_plans()
    if not _is_table():
        render_list(data, title="Plans de cluster K8s", columns=[])
        return
    rows = [
        {
            "key": _fmt_plan(p.get("key")),
            "price": _fmt_price(p),
            "max_nodes": p.get("max_nodes"),
            "cp": p.get("control_plane_replicas"),
            "etcd": f"{p.get('etcd_members', '?')} × {p.get('etcd_volume_gb', '?')} Gi",
            "ha": "oui" if p.get("high_availability") else "non",
            "block": p.get("node_ip_block_size"),
            "vnet": f"/{p['recommended_vnet_prefix']}" if p.get("recommended_vnet_prefix") else "—",
        }
        for p in data
    ]
    render_list(rows, title=f"Plans de cluster K8s ({len(rows)})",
                columns=[("key", "Plan"), ("price", "Prix"), ("max_nodes", "Nœuds max"),
                         ("cp", "Réplicas plan de contrôle"), ("etcd", "etcd"),
                         ("ha", "Haute dispo."), ("block", "Bloc d'IP nœuds"),
                         ("vnet", "VNet conseillé")])


@app.command()
def versions(
    region: str | None = typer.Option(None, "--region", "-r", help="Filtrer par région"),
) -> None:
    """Liste les versions Kubernetes disponibles."""
    try:
        data = client.get("/v1/k8s/templates", params={"region": region} if region else None)
    except client.APIError as e:
        _fail(e)
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


def _version_sort_key(version: str | None) -> tuple[int, ...]:
    """Clé de tri numérique pour une version `vX.Y.Z` / `X.Y.Z`.

    Retourne un tuple d'entiers (major, minor, patch, …) pour trier les versions
    par ordre **décroissant** (majeure en haut). Les segments non numériques ou
    absents valent 0 → toujours comparables."""
    raw = (version or "").lstrip("vV")
    parts: list[int] = []
    for seg in raw.split("."):
        num = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts) if parts else (0,)


@app.command()
def templates(
    region: str | None = typer.Option(None, "--region", "-r", help="Filtrer par région"),
    name: str | None = typer.Option(
        None, "--name", "-n",
        help="Filtrer par nom (sous-chaîne, insensible à la casse, sur la clé "
             "ou le nom affiché).",
    ),
    k8s_version: str | None = typer.Option(
        None, "--k8s-version", "-k",
        help="Filtrer par version Kubernetes (préfixe, ex `1.34` ou `v1.34.6`).",
    ),
) -> None:
    """Liste les templates OS Kubernetes disponibles.

    La clé (`--template` à la création) est l'`os_key` (ex `kube-v1-34-6`).
    Résultats triés par version Kubernetes décroissante (majeure en haut).
    """
    try:
        data = client.get("/v1/k8s/templates", params={"region": region} if region else None)
    except client.APIError as e:
        _fail(e)

    if name:
        needle = name.lower()
        data = [
            t for t in data
            if needle in (t.get("os_key") or "").lower()
            or needle in (t.get("display_name") or "").lower()
        ]
    if k8s_version:
        ver = k8s_version.lstrip("vV")
        data = [t for t in data if (t.get("k8s_version") or "").lstrip("vV").startswith(ver)]

    # Tri par défaut : **nom croissant** (clé primaire — regroupe par OS) puis
    # **version Kubernetes décroissante** (majeure en haut au sein de chaque OS).
    # Double passe stable : on trie d'abord par version (clé secondaire, desc),
    # puis par nom (clé primaire) — le tri stable préserve l'ordre des versions
    # au sein d'un même nom. Le « nom » = famille OS (`os_label`) pour ne pas
    # mélanger la version embarquée dans `display_name`.
    def _name(t: dict) -> str:
        return (t.get("os_label") or t.get("display_name") or "").lower()

    data = sorted(data, key=lambda t: _version_sort_key(t.get("k8s_version")),
                  reverse=True)
    data = sorted(data, key=_name)

    rows = [
        {
            # Un template est identifié par le COUPLE (version, OS) — `kube-v<ver>` n'est plus
            # unique depuis le multi-OS.
            "os_key": t.get("os_key", ""),                       # kube-v<ver>
            "os_tag": f"ccks-os-{t['os']}" if t.get("os") else "—",  # ccks-os-<slug>
            "display_name": t.get("display_name", ""),
            "k8s_version": t.get("k8s_version", "—"),
            "os": t.get("os_label", "—"),
            "region": t.get("region", "—"),
            "built_at": (t.get("built_at") or "")[:10],
        }
        for t in data
    ]
    render_list(rows, title=f"Templates Kubernetes ({len(rows)})",
                columns=[("os_key", "Clé (version)"), ("os_tag", "Clé (OS)"),
                         ("display_name", "Nom"), ("k8s_version", "Version"),
                         ("os", "OS"), ("region", "Région"),
                         ("built_at", "Buildé le")])


def vnet_egress_state(vnets: list[dict], vnet_id: str) -> bool | None:
    """Le VNet a-t-il une sortie internet ? `None` = inconnu.

    `None` quand le réseau n'est pas dans la liste (course, droits partiels) :
    l'inconnu ne doit jamais se lire comme « isolé », un avertissement faux
    apprend surtout à ignorer les avertissements.
    """
    for v in vnets:
        if str(v.get("id")) == vnet_id:
            return bool(v.get("snat"))
    return None


def _guard_isolated_vnet(vpc_id: str, vnet_id: str, *, wants_public_ip: bool) -> None:
    """Réseau isolé : rappel toujours, refus quand une IP publique est demandée.

    Un cluster se crée parfaitement dans un réseau sans sortie internet (images
    préchargées dans le template, relais DNS interne) : le CLI n'écarte aucun
    VNet, c'est tout l'objet de l'issue #48.

    ⚠️ **Le garde `snat` de la plateforme est sur `attach-ip`, PAS sur la
    création.** `POST /v1/k8s/clusters` valide propriété, région et statut de
    l'IP fournie, puis la passe à `ALLOCATED` — sans jamais regarder le `snat`
    du VNet (l'unique contrôle vit dans `k8s_clusters.attach_public_ip`).
    Annoncer « seront refusés » puis afficher « ✓ Cluster créé » était donc
    faux, et laissait une IP publique réservée — donc facturée — sur un cluster
    qui ne pourra jamais la porter. On refuse ici plutôt que d'annoncer un
    refus qui n'aura pas lieu : le coût pour l'utilisateur est de retirer un
    drapeau, celui de l'inverse est une IP immobilisée en silence.

    Le refus n'a lieu que sur un `snat=false` CONSTATÉ. Lecture inaccessible ou
    VNet absent de la liste → aucun blocage : un `except` large parce qu'une
    panne réseau (`httpx`) n'est pas une `APIError`, et qu'aucune des deux ne
    doit empêcher de créer un cluster.
    """
    try:
        vnets = client.get(f"/v1/vpcs/{vpc_id}/vnets")
    except Exception:  # noqa: BLE001 — lecture de confort, jamais bloquante
        return
    if vnet_egress_state(vnets, vnet_id) is not False:
        return
    rprint(
        "[yellow]⚠[/yellow] Ce réseau est isolé (aucune sortie internet) : "
        "le cluster s'y crée normalement, mais aucune IP publique ne pourra "
        "lui être attachée (l'attache exige un réseau avec sortie internet)."
    )
    if wants_public_ip:
        rprint(
            "[red]--ingress-ip / --apiserver-ip sont refusés ici : la création "
            "réserverait l'IP sans que le cluster puisse jamais la porter.[/red]\n"
            "[dim]Retirez le drapeau, ou visez un réseau avec sortie internet "
            "(cetic vpc vnet list <VPC>, colonne « Sortie »).[/dim]"
        )
        raise typer.Exit(1)


def _resolve_os_template_key(
    templates: list[dict], *, region: str, k8s_version: str, os_slug: str
) -> str | None:
    """Sélectionne l'`os_key` du template qui matche région + version + OS.

    Retourne la clé du premier template dont (`os`, `k8s_version`, `region`)
    correspond exactement au triplet demandé, ou `None` si aucun ne matche
    (le backend re-validera de toute façon, 422 sinon)."""
    for t in templates:
        if (
            t.get("os") == os_slug
            and t.get("k8s_version") == k8s_version
            and t.get("region") == region
        ):
            return t.get("os_key")
    return None


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
    vpc_id: str = typer.Option(..., "--vpc"),
    vnet_id: str = typer.Option(
        ..., "--vnet",
        help=(
            "Sous-réseau des nœuds. Un réseau isolé (sans sortie internet) est "
            "accepté — aucune IP publique ne pourra alors être attachée. "
            "Voir : cetic vpc vnet list <VPC>."
        ),
    ),
    k8s_version: str = typer.Option(
        "v1.31.0", "--version",
        help="Version Kubernetes du CONTROL PLANE (ex: v1.31.0).",
    ),
    pool_version: str | None = typer.Option(
        None, "--pool-version",
        help=(
            "Version Kubernetes (workers) du pool initial. Omis = hérite du "
            "control plane. Doit être ≤ --version (le backend re-valide → 422)."
        ),
    ),
    os_image: str = typer.Option(
        "flatcar",
        "--os",
        help="Famille d'OS des nœuds : flatcar (défaut) | ubuntu | rocky9.",
        case_sensitive=False,
    ),
    os_template_key: str | None = typer.Option(
        None,
        "--template",
        help=(
            "Clé de template OS (ex: kube-v1-34-8). Optionnel : si omis, "
            "résolu automatiquement depuis --version + --os."
        ),
    ),
    plan: str | None = typer.Option(
        None,
        "--plan",
        help=(
            "Plan du cluster : starter (défaut, gratuit, sans haute disponibilité) | "
            "standard | pro (haute disponibilité). Limites et prix : "
            "cetic k8s cluster-plans."
        ),
        case_sensitive=False,
    ),
    tier: str | None = typer.Option(
        None,
        "--tier",
        help="DÉPRÉCIÉ, utilisez --plan (dev = starter, prod = standard).",
        case_sensitive=False,
    ),
    # Pool initial
    pool_name: str = typer.Option("default", "--pool-name"),
    pool_plan: str = typer.Option("small", "--pool-plan"),
    pool_replicas: int = typer.Option(1, "--pool-replicas"),
    pool_disk_gb: int | None = typer.Option(
        None, "--disk-gb",
        help=_DISK_HELP,
    ),
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
    plan_norm = _normalize_plan(plan) if plan is not None else None
    if tier is not None:
        tier_norm = tier.lower()
        if tier_norm not in VALID_TIERS:
            rprint(
                f"[red]--tier invalide : '{tier}'. "
                f"Valeurs autorisées : {', '.join(VALID_TIERS)}.[/red]"
            )
            raise typer.Exit(1)
        if plan_norm is not None and _TIER_FROM_PLAN[plan_norm] != tier_norm:
            rprint(
                f"[red]--plan {plan_norm} et --tier {tier_norm} (déprécié) en "
                "désaccord : ne passez que --plan.[/red]"
            )
            raise typer.Exit(1)
        if plan_norm is None:
            plan_norm = _PLAN_FROM_TIER[tier_norm]
        _warn(f"--tier est déprécié : utilisez --plan {plan_norm}.")

    os_norm = os_image.lower()
    if os_norm not in VALID_OS:
        rprint(
            f"[red]--os invalide : '{os_image}'. "
            f"Valeurs autorisées : {', '.join(VALID_OS)}.[/red]"
        )
        raise typer.Exit(1)

    # Résolution du template : si --template n'est pas fourni, on cherche le
    # template buildé qui matche région + version + OS choisi et on envoie son
    # os_key. Le backend re-valide (os_image, version, région) → 422 sinon.
    resolved_template_key = os_template_key
    if not resolved_template_key:
        try:
            templates = client.get("/v1/k8s/templates", params={"region": region})
        except client.APIError as e:
            _fail(e)
        resolved_template_key = _resolve_os_template_key(
            templates, region=region, k8s_version=k8s_version, os_slug=os_norm
        )
        if not resolved_template_key:
            rprint(
                f"[red]Aucun template {_fmt_os(os_norm)} en {k8s_version} "
                f"disponible pour la région {region}. "
                f"Voir : cetic k8s templates --region {region}.[/red]"
            )
            raise typer.Exit(1)

    if pool_version is not None:
        _validate_k8s_version(pool_version)

    # Un VNet sans sortie internet est accepté (rien ne le filtre ici) ; on dit
    # ce qu'il implique avant de lancer un provisioning de 5-15 min, et on
    # refuse la seule combinaison que la plateforme laisse passer en silence.
    _guard_isolated_vnet(
        vpc_id, vnet_id, wants_public_ip=bool(ingress_ip_id or apiserver_ip_id)
    )

    pool_body: dict = {"name": pool_name, "plan": pool_plan, "replicas": pool_replicas}
    if pool_min is not None:
        pool_body["min_size"] = pool_min
    if pool_max is not None:
        pool_body["max_size"] = pool_max
    if pool_version is not None:
        pool_body["k8s_version"] = pool_version
    if pool_disk_gb is not None:
        pool_body["disk_gb"] = pool_disk_gb

    body: dict = {
        "name": name,
        "region": region,
        "vpc_id": vpc_id,
        "vnet_id": vnet_id,
        "k8s_version": k8s_version,
        "os_image": os_norm,
        "os_template_key": resolved_template_key,
        "initial_pool": pool_body,
        "autoscaler_scale_down_delay_after_add": scale_down_delay,
        "autoscaler_scale_down_unneeded_time": scale_down_unneeded,
        "ingress_controller_enabled": not no_ingress,
        "ingress_controller_scope": ingress_scope,
        "ingress_controller_class": ingress_class,
    }
    # Ni --plan ni --tier : rien d'envoyé, le serveur applique son défaut
    # (starter). `tier` est transmis quand il est fourni : une API antérieure à
    # #2143 ignore `plan` et créerait sinon un cluster `dev` en silence.
    if plan_norm is not None:
        body["plan"] = plan_norm
        # `tier` cohérent toujours joint : une API antérieure à #2143 ne lit que lui.
        body["tier"] = tier.lower() if tier is not None else _TIER_FROM_PLAN[plan_norm]
    elif tier is not None:
        body["tier"] = tier.lower()
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
        _fail(e)
    rprint(
        f"[green]✓[/green] Cluster créé : [bold]{c['id']}[/bold] "
        f"(plan : {_cluster_plan(c) or plan_norm or 'starter'}, status: {c.get('status', '?')})"
    )
    rprint("[dim]Le provisioning prend 5-15 min. Suivre avec : cetic k8s get {id}[/dim]")


@app.command(name="set-plan")
def set_plan(
    cluster_id: str = typer.Argument(...),
    plan: str = typer.Option(
        ..., "--plan", "-p",
        help="Plan visé : starter | standard | pro (cf. cetic k8s cluster-plans).",
        case_sensitive=False,
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Change le plan du cluster.

    Standard ↔ Pro : à chaud (le plan de contrôle passe de 2 à 3 réplicas, ou
    l'inverse). Starter ↔ Standard/Pro : refusé, il faut recréer le cluster.
    Une descente est refusée si les nœuds des pools dépassent la limite du plan
    visé. Le prix du plan s'applique dès le changement.
    """
    plan_norm = _normalize_plan(plan)
    try:
        actuel = _cluster_plan(client.get(f"/v1/k8s/clusters/{cluster_id}"))
    except client.APIError as e:
        _fail(e)
    if actuel == plan_norm:
        rprint(f"[yellow]Le cluster est déjà au plan {plan_norm} : rien à changer.[/yellow]")
        raise typer.Exit(0)
    if actuel and (actuel == "starter") != (plan_norm == "starter"):
        # Même refus que l'API (409), avant toute confirmation.
        rprint(f"[red]Passer de {actuel} à {plan_norm} demande de recréer le "
               "cluster.[/red]", file=sys.stderr)
        rprint(f"[dim]{_recreation_hint(plan_norm)}[/dim]", file=sys.stderr)
        raise typer.Exit(1)
    if not yes and not typer.confirm(
        f"Passer le cluster {cluster_id} du plan {actuel or '?'} au plan {plan_norm} ? "
        "Le prix du plan s'applique dès le changement.",
        err=True,
    ):
        raise typer.Abort()
    try:
        c = client.patch(f"/v1/k8s/clusters/{cluster_id}", json={"plan": plan_norm})
    except client.APIError as e:
        _fail(e)
    rprint(
        f"[green]✓[/green] Plan du cluster : [bold]{_cluster_plan(c) or plan_norm}[/bold] "
        f"(status : {c.get('status', '?')})"
    )


@app.command(name="upgrade-version")
def upgrade_version(
    cluster_id: str = typer.Argument(...),
    k8s_version: str = typer.Option(..., "--version", "-v", help="Ex: v1.32.0"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Met à niveau le PLAN DE CONTRÔLE seul (une mineure à la fois).

    Les pools ne bougent pas : ceux qui héritaient de la version du plan de
    contrôle sont épinglés sur l'ancienne. Mettez-les ensuite à niveau un par
    un (`cetic k8s pool upgrade`). Pour un simple patch, plan de contrôle et
    pools en une fois : `cetic k8s upgrade-patch`. Refusé (422) si un pool
    resterait à plus de 3 mineures de retard.
    """
    _validate_k8s_version(k8s_version)
    if not yes and not typer.confirm(
        f"Mettre à niveau le plan de contrôle du cluster {cluster_id} vers "
        f"{k8s_version} (les pools restent sur leur version) ?",
        err=True,
    ):
        raise typer.Abort()
    try:
        c = client.post(f"/v1/k8s/clusters/{cluster_id}/upgrade-version",
                        json={"k8s_version": k8s_version})
    except client.APIError as e:
        _fail(e)
    rprint(
        f"[green]✓[/green] Plan de contrôle → {c.get('k8s_version', k8s_version)} "
        f"(status : {c.get('status', '?')})"
    )
    cible = c.get("k8s_version") or k8s_version
    restants = [
        p for p in c.get("node_pools") or []
        if _norm_version(p.get("k8s_version") or cible) != _norm_version(cible)
    ]
    if restants:
        rprint("Pools restés sur leur version, à mettre à niveau un par un :")
        for p in restants:
            rprint(
                f"  {p.get('name', p.get('id'))} ({p.get('k8s_version')}) : "
                f"cetic k8s pool upgrade {cluster_id} {p.get('id')} --version {cible}"
            )


def _norm_version(v: str | None) -> str:
    return (v or "").lstrip("vV")


@app.command(name="upgrade", hidden=True)
def upgrade_deprecated(
    cluster_id: str = typer.Argument(...),
    k8s_version: str = typer.Option(..., "--version", "-v"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """DÉPRÉCIÉ : alias de `cetic k8s upgrade-version` (plan de contrôle seul)."""
    _warn("`cetic k8s upgrade` est déprécié : utilisez `cetic k8s upgrade-version` "
          "(plan de contrôle seul).")
    upgrade_version(cluster_id=cluster_id, k8s_version=k8s_version, yes=yes)


@app.command(name="upgrade-patch")
def upgrade_patch(
    cluster_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Applique le patch disponible en un geste : plan de contrôle puis pools.

    Une mise à niveau de PATCH en pause (fenêtre de maintenance terminée) est
    proposée à la reprise immédiate. Une mise à niveau en pause d'un autre type
    (plan de contrôle, pool, mineure) n'est pas reprise ici : annulez-la avec
    `cetic k8s upgrade-cancel`.

    La cible est le patch affiché par `cetic k8s get` (`patch_disponible`) :
    plan de contrôle d'abord, puis chaque pool de la même mineure, l'un après
    l'autre (remplacement progressif des nœuds, avec drain). Les pools d'une
    autre mineure ne sont pas touchés. Si le patch disponible change entre
    l'affichage et l'envoi, l'API refuse (409) : relancez la commande.
    """
    try:
        c = client.get(f"/v1/k8s/clusters/{cluster_id}")
    except client.APIError as e:
        _fail(e)
    op_courante = c.get("upgrade_en_cours")
    if op_courante and op_courante.get("statut") == "en_pause":
        if op_courante.get("type") != "patch":
            # L'API refuse (409) : seule une opération de patch se reprend ici.
            rprint(f"[red]Mise à niveau en pause d'un autre type : "
                   f"{_fmt_upgrade(op_courante)}.[/red]", file=sys.stderr)
            rprint("[dim]Elle n'est pas reprise par la mise à jour de patch : annulez-la "
                   f"d'abord (cetic k8s upgrade-cancel {cluster_id}).[/dim]", file=sys.stderr)
            raise typer.Exit(1)
        if not yes and not typer.confirm(
            f"Mise à niveau en pause : {_fmt_upgrade(op_courante)}. La reprendre "
            "maintenant ?",
            err=True,
        ):
            raise typer.Abort()
        try:
            # Sans `?version=` : l'API reprend l'opération en pause telle quelle.
            c = client.post(f"/v1/k8s/clusters/{cluster_id}/upgrade-patch")
        except client.APIError as e:
            _fail(e)
        if _emit_machine(c):
            return
        rprint(f"[green]✓[/green] Mise à niveau reprise (status : {c.get('status', '?')}).")
        rprint(f"[dim]Suivre avec : cetic k8s upgrades {cluster_id}[/dim]")
        return
    patch = c.get("patch_disponible")
    if not patch:
        en_cours = _fmt_upgrade(c.get("upgrade_en_cours"))
        if en_cours:
            rprint(f"[yellow]Mise à niveau déjà en cours : {en_cours}.[/yellow]",
                   file=sys.stderr)
        else:
            rprint("[yellow]Aucun patch disponible pour ce cluster.[/yellow]",
                   file=sys.stderr)
        raise typer.Exit(0)
    version = patch.get("version")
    if not version or not _K8S_VERSION_RE.match(version):
        rprint(f"[red]Patch disponible illisible : {patch!r}.[/red]", file=sys.stderr)
        raise typer.Exit(1)
    if patch.get("templates_ok") is False:
        _warn(f"Aucun template {version} n'est prêt pour l'OS d'au moins un pool : "
              "l'API refusera la mise à niveau.")
    if not yes and not typer.confirm(
        f"Appliquer le patch {_fmt_patch_disponible(patch)} au cluster {cluster_id} ?",
        err=True,
    ):
        raise typer.Abort()
    try:
        # `?version=` : l'API refuse (409) si le patch a changé depuis la lecture.
        c = client.post(f"/v1/k8s/clusters/{cluster_id}/upgrade-patch?version={version}")
    except client.APIError as e:
        _fail(e)
    if _emit_machine(c):
        return
    rprint(f"[green]✓[/green] Patch {version} lancé (status : {c.get('status', '?')}).")
    op = _fmt_upgrade(c.get("upgrade_en_cours"))
    if op:
        rprint(f"[dim]{op}[/dim]")
        rprint(f"[dim]Suivre avec : cetic k8s upgrades {cluster_id}[/dim]")


def _emit_machine(c: dict) -> bool:
    """`-o json|yaml` : émet le cluster renvoyé sur stdout (sans les champs
    `proxy_*`, comme `get`) et rend True ; en table, ne fait rien."""
    if _is_table():
        return False
    render_one({k: v for k, v in (c or {}).items() if not k.startswith("proxy_")},
               title="Cluster")
    return True


@app.command(name="upgrade-cancel")
def upgrade_cancel(
    cluster_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Annule les étapes restantes d'une mise à niveau EN PAUSE.

    Les étapes en attente passent « annulée » et la version qu'elles devaient
    poser est restaurée ; les étapes déjà terminées restent en place (aucun
    retour arrière). Une étape en cours se termine toujours : seule une mise à
    niveau en pause s'annule. Un pool laissé sur une mineure plus ancienne ne
    peut plus créer de nœud avant d'être mis à niveau.
    """
    try:
        c = client.get(f"/v1/k8s/clusters/{cluster_id}")
    except client.APIError as e:
        _fail(e)
    op = c.get("upgrade_en_cours")
    if not op or op.get("statut") != "en_pause":
        if op:
            statut = _UPGRADE_STATUT.get(op.get("statut", ""), op.get("statut") or "?")
            rprint(f"[red]Mise à niveau {statut}, pas en pause : {_fmt_upgrade(op)}.[/red]",
                   file=sys.stderr)
            rprint("[dim]Une étape en cours se termine toujours ; seule une mise à niveau "
                   "en pause s'annule.[/dim]", file=sys.stderr)
        else:
            rprint("[red]Aucune mise à niveau en attente sur ce cluster.[/red]",
                   file=sys.stderr)
        raise typer.Exit(1)
    restantes = [
        (e.get("pool_name") or e.get("pool_id") or "pool") if e.get("type") == "pool"
        else "plan de contrôle"
        for e in op.get("etapes") or [] if e.get("statut") == "en_attente"
    ]
    rprint(
        f"Mise à niveau en pause : "
        f"{_UPGRADE_TYPE.get(op.get('type', ''), op.get('type') or '?')} vers "
        f"{op.get('version_cible', '?')} ({op.get('origine') or '?'}).\n"
        f"Étapes annulées : {', '.join(restantes) or 'aucune'} ; les étapes terminées "
        "restent en place.",
        file=sys.stderr,
    )
    if not yes and not typer.confirm("Annuler cette mise à niveau ?", err=True):
        raise typer.Abort()
    try:
        c = client.post(f"/v1/k8s/clusters/{cluster_id}/upgrade-cancel")
    except client.APIError as e:
        if e.status_code == 409:
            rprint(f"[red]Erreur : {e.detail}[/red]", file=sys.stderr)
            rprint("[dim]Seule une mise à niveau en pause s'annule ; état actuel : "
                   f"cetic k8s upgrades {cluster_id}.[/dim]", file=sys.stderr)
            raise typer.Exit(1) from None
        _fail(e)
    if _emit_machine(c):
        return
    rprint(f"[green]✓[/green] Mise à niveau annulée (version du plan de contrôle : "
           f"{c.get('k8s_version', '?')}, status : {c.get('status', '?')}).")
    rprint(f"[dim]Historique : cetic k8s upgrades {cluster_id}[/dim]")


@app.command(name="upgrades")
def list_upgrades(
    cluster_id: str = typer.Argument(...),
    limit: int = typer.Option(50, "--limit", "-l", min=1, max=200),
) -> None:
    """Historique des mises à niveau du cluster (manuelles et automatiques)."""
    try:
        items = client.get(f"/v1/k8s/clusters/{cluster_id}/upgrades",
                           params={"limit": limit})
    except client.APIError as e:
        _fail(e)
    if not _is_table():
        render_list(items, title="Mises à niveau", columns=[])
        return
    rows = []
    for op in items:
        etapes = op.get("etapes") or []
        faites = sum(1 for e in etapes if e.get("statut") == "terminee")
        rows.append({
            "date": (op.get("created_at") or "")[:16].replace("T", " ") or "—",
            "type": _UPGRADE_TYPE.get(op.get("type", ""), op.get("type")),
            "origine": op.get("origine"),
            "versions": f"{op.get('version_depart') or '?'} → {op.get('version_cible', '?')}",
            "statut": _UPGRADE_STATUT.get(op.get("statut", ""), op.get("statut")),
            "etapes": (f"{faites}/{len(etapes)}"
                       + (f" ({_etapes_annulees(etapes)} annulée(s))"
                          if _etapes_annulees(etapes) else "")) if etapes else "—",
            "fin": (op.get("termine_at") or "")[:16].replace("T", " ") or "—",
            "erreur": op.get("error_message"),
        })
    render_list(rows, title=f"Mises à niveau du cluster {cluster_id[:8]} ({len(rows)})",
                columns=[("date", "Date"), ("type", "Type"), ("origine", "Origine"),
                         ("versions", "Versions"), ("statut", "Statut"),
                         ("etapes", "Étapes"), ("fin", "Fin"), ("erreur", "Erreur")])


# ── Maintenance (mise à jour automatique) ─────────────────────────────────

_JOURS = ("lun", "mar", "mer", "jeu", "ven", "sam", "dim")
_JOURS_COMPLETS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
# Abréviations exactes ou noms complets, rien d'autre (`jeuxyz` est refusé).
_JOURS_NOMS = {n: i + 1 for noms in (_JOURS, _JOURS_COMPLETS) for i, n in enumerate(noms)}
_HEURE_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _parse_jours(raw: str) -> list[int]:
    """`2,3,4` | `mar,mer,jeu` | `2-4` → jours ISO triés (1 = lundi)."""
    jours: set[int] = set()
    for part in (x.strip().lower() for x in raw.split(",")):
        if not part:
            continue
        if re.fullmatch(r"[1-7]-[1-7]", part):
            a, b = (int(x) for x in part.split("-"))
            if a > b:
                raise typer.BadParameter(f"Plage de jours inversée : '{part}'.")
            jours.update(range(a, b + 1))
        elif re.fullmatch(r"[1-7]", part):
            jours.add(int(part))
        elif part in _JOURS_NOMS:
            jours.add(_JOURS_NOMS[part])
        else:
            raise typer.BadParameter(
                f"Jour invalide : '{part}'. Attendu 1-7 (1 = lundi), lun…dim ou "
                "lundi…dimanche."
            )
    if not jours:
        raise typer.BadParameter("--jours : au moins un jour.")
    return sorted(jours)


def _parse_gel(raw: str) -> dict[str, str]:
    """`AAAA-MM-JJ:AAAA-MM-JJ` → `{debut, fin}` (bornes incluses)."""
    debut_s, sep, fin_s = raw.partition(":")
    try:
        if not sep:
            raise ValueError
        debut, fin = date.fromisoformat(debut_s.strip()), date.fromisoformat(fin_s.strip())
    except ValueError:
        raise typer.BadParameter(
            f"Période de gel invalide : '{raw}'. Format : AAAA-MM-JJ:AAAA-MM-JJ."
        ) from None
    if fin < debut:
        raise typer.BadParameter(f"Période de gel inversée : '{raw}'.")
    if (fin - debut).days + 1 > 31:
        raise typer.BadParameter(f"Période de gel de plus de 31 jours : '{raw}'.")
    return {"debut": debut.isoformat(), "fin": fin.isoformat()}


def _fmt_fenetre(f: dict | None) -> str | None:
    if not f:
        return None
    jours = ", ".join(_JOURS[j - 1] for j in f.get("jours") or [] if 1 <= j <= 7)
    return (f"{jours} à {f.get('heure_debut', '?')}, {f.get('duree_heures', '?')} h "
            f"({f.get('fuseau', '?')})")


def _fmt_prevue(p: dict | None) -> str | None:
    if not p:
        return None
    cibles = []
    if p.get("plan_de_controle"):
        cibles.append("plan de contrôle")
    if p.get("pools"):
        cibles.append(f"{len(p['pools'])} pool(s)")
    txt = f"{p.get('type', '?')} {p.get('version', '?')}"
    if cibles:
        txt += f" ({' + '.join(cibles)})"
    if p.get("debut_prevu"):
        txt += f", prévue le {str(p['debut_prevu'])[:16].replace('T', ' ')}"
    if p.get("force"):
        txt += ", forcée"
    if p.get("motif"):
        txt += f" — {p['motif']}"
    return txt


def _render_maintenance(m: dict, cluster_id: str) -> None:
    if not _is_table():
        render_one(m, title="Maintenance")
        return

    def _oui(v: Any) -> str:
        return "oui" if v else "non"

    gels = ", ".join(f"{g.get('debut')} → {g.get('fin')}" for g in m.get("dates_gel") or [])
    view = {
        "auto_patch": _oui(m.get("auto_patch")),
        "auto_mineure": _oui(m.get("auto_mineure")),
        "auto_majeure": _oui(m.get("auto_majeure")),
        "fenetre": _fmt_fenetre(m.get("fenetre")),
        "dates_gel": gels or None,
        "personnalise": _oui(m.get("personnalise")),
        "fenetre_plateforme": _fmt_fenetre(m.get("fenetre_plateforme")),
        "delai_stabilisation_jours": m.get("delai_stabilisation_jours"),
        "delai_patch_force_jours": m.get("delai_patch_force_jours"),
        "prochaine_execution": _fmt_prevue(m.get("prochaine_execution")),
        "reprise_prevue": m.get("reprise_prevue"),
        "upgrade_en_cours": _fmt_upgrade(m.get("upgrade_en_cours")),
        "notifications": len(m.get("notifications") or []),
    }
    render_one(view, title=f"Maintenance du cluster {cluster_id[:8]}")


@maintenance_app.command(name="get")
def maintenance_get(cluster_id: str = typer.Argument(...)) -> None:
    """Affiche les réglages de mise à jour automatique et la prochaine exécution.

    Défauts : patch automatique activé, mineure et majeure désactivées, fenêtre
    mardi→jeudi 02:00 pendant 4 h (Europe/Paris). Un patch est appliqué de force
    30 jours après la sortie de son template, même sans auto-patch.
    """
    try:
        m = client.get(f"/v1/k8s/clusters/{cluster_id}/maintenance")
    except client.APIError as e:
        _fail(e)
    _render_maintenance(m, cluster_id)


@maintenance_app.command(name="set")
def maintenance_set(
    cluster_id: str = typer.Argument(...),
    auto_patch: bool | None = typer.Option(
        None, "--auto-patch/--no-auto-patch", help="Patchs appliqués automatiquement."),
    auto_mineure: bool | None = typer.Option(
        None, "--auto-mineure/--no-auto-mineure",
        help="Montées de mineure (1.31 → 1.32) automatiques."),
    auto_majeure: bool | None = typer.Option(
        None, "--auto-majeure/--no-auto-majeure", help="Montées de majeure automatiques."),
    jours: str | None = typer.Option(
        None, "--jours",
        help="Jours de la fenêtre : 2,3,4 | 2-4 | mar,mer,jeu (1 = lundi)."),
    heure_debut: str | None = typer.Option(None, "--heure-debut", help="Début, HH:MM."),
    duree_heures: int | None = typer.Option(
        None, "--duree-heures", min=4, max=24, help="Durée de la fenêtre (4 à 24 h)."),
    fuseau: str | None = typer.Option(None, "--fuseau", help="Fuseau IANA (Europe/Paris)."),
    gels: list[str] | None = typer.Option(
        None, "--gel",
        help=("Période sans mise à jour AAAA-MM-JJ:AAAA-MM-JJ (répétable, 31 jours "
              "au plus chacune, 12 au plus). Remplace la liste existante."),
    ),
    sans_gel: bool = typer.Option(False, "--sans-gel", help="Vide la liste des périodes de gel."),
) -> None:
    """Modifie les réglages de mise à jour automatique (champ omis = inchangé).

    Les options de fenêtre omises reprennent la fenêtre actuelle.
    """
    if gels and sans_gel:
        rprint("[red]--gel et --sans-gel sont incompatibles.[/red]")
        raise typer.Exit(1)
    if heure_debut is not None and not _HEURE_RE.match(heure_debut):
        rprint(f"[red]--heure-debut invalide : '{heure_debut}' (HH:MM attendu).[/red]")
        raise typer.Exit(1)
    body: dict[str, Any] = {}
    for key, val in (("auto_patch", auto_patch), ("auto_mineure", auto_mineure),
                     ("auto_majeure", auto_majeure)):
        if val is not None:
            body[key] = val
    if gels:
        if len(gels) > 12:
            rprint("[red]12 périodes de gel au plus.[/red]")
            raise typer.Exit(1)
        body["dates_gel"] = [_parse_gel(g) for g in gels]
    elif sans_gel:
        body["dates_gel"] = []
    fenetre_opts = (jours, heure_debut, duree_heures, fuseau)
    if any(v is not None for v in fenetre_opts):
        parsed_jours = _parse_jours(jours) if jours is not None else None
        # L'API attend la fenêtre complète : les morceaux omis viennent de
        # l'actuelle.
        actuelle: dict = {}
        if None in (parsed_jours, heure_debut, duree_heures, fuseau):
            try:
                actuelle = client.get(
                    f"/v1/k8s/clusters/{cluster_id}/maintenance").get("fenetre") or {}
            except client.APIError as e:
                _fail(e)
        body["fenetre"] = {
            "jours": parsed_jours if parsed_jours is not None else actuelle.get("jours"),
            "heure_debut": heure_debut if heure_debut is not None else actuelle.get("heure_debut"),
            "duree_heures": duree_heures if duree_heures is not None
            else actuelle.get("duree_heures"),
            "fuseau": fuseau if fuseau is not None else actuelle.get("fuseau"),
        }
    if not body:
        rprint("[yellow]Aucun paramètre à modifier.[/yellow]")
        raise typer.Exit(0)
    try:
        m = client.put(f"/v1/k8s/clusters/{cluster_id}/maintenance", json=body)
    except client.APIError as e:
        _fail(e)
    rprint("[green]✓[/green] Réglages de maintenance enregistrés.", file=sys.stderr)
    _render_maintenance(m, cluster_id)


# ── SSO OIDC ──────────────────────────────────────────────────────────────


_OIDC_DEFAUTS: dict[str, Any] = {
    "username_claim": "email", "username_prefix": "oidc:", "groups_claim": "groups",
    "groups_prefix": "oidc:", "extra_scopes": [], "ca_pem": None,
}
_OIDC_CLES = ("issuer_url", "client_id", "username_claim", "username_prefix",
              "groups_claim", "groups_prefix", "extra_scopes", "ca_pem")
_CA_PEM_MAX = 65536


def _fmt_ca(pem: str | None) -> str | None:
    """CA abrégée pour la table : nombre de certificats et taille."""
    if not pem:
        return None
    n = pem.count("-----BEGIN CERTIFICATE-----")
    return f"{n} certificat(s) PEM, {len(pem)} caractères"


def _read_ca_file(path: Path) -> str:
    """Lit une CA PEM ; sortie propre (pas de trace) si illisible ou invalide."""
    try:
        pem = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        rprint(f"[red]--ca-file illisible ({path}) : {exc.__class__.__name__}. "
               "Attendu un fichier PEM texte (un certificat DER doit être converti : "
               "openssl x509 -inform der -in ca.der -out ca.pem).[/red]", file=sys.stderr)
        raise typer.Exit(1) from None
    if "-----BEGIN CERTIFICATE-----" not in pem:
        rprint(f"[red]--ca-file : {path} ne contient aucun certificat PEM "
               "(-----BEGIN CERTIFICATE-----).[/red]", file=sys.stderr)
        raise typer.Exit(1)
    if len(pem) > _CA_PEM_MAX:
        rprint(f"[red]--ca-file : {len(pem)} caractères, {_CA_PEM_MAX} au plus.[/red]",
               file=sys.stderr)
        raise typer.Exit(1)
    return pem


@oidc_app.command(name="get")
def oidc_get(cluster_id: str = typer.Argument(...)) -> None:
    """Affiche la configuration SSO (OIDC) du cluster."""
    try:
        c = client.get(f"/v1/k8s/clusters/{cluster_id}")
    except client.APIError as e:
        _fail(e)
    o = c.get("oidc")
    if not _is_table():
        # JSON/YAML : `null` sans SSO, l'objet brut sinon.
        if config.get_output() == "yaml":
            import yaml
            print(yaml.safe_dump(o, allow_unicode=True, sort_keys=False), end="")
        else:
            print(json.dumps(o, ensure_ascii=False, indent=2))
        return
    if not o:
        rprint("[yellow]SSO non configuré sur ce cluster (cetic k8s oidc set).[/yellow]")
        return
    vue = dict(o)
    vue["ca_pem"] = _fmt_ca(o.get("ca_pem"))
    render_one(vue, title=f"SSO du cluster {c.get('name', cluster_id)}")


@oidc_app.command(name="set")
def oidc_set(
    cluster_id: str = typer.Argument(...),
    issuer_url: str | None = typer.Option(
        None, "--issuer-url",
        help=("URL de l'émetteur OIDC (https, port 443, joignable publiquement). "
              "Requise si le SSO n'est pas encore configuré.")),
    client_id: str | None = typer.Option(
        None, "--client-id",
        help="Identifiant du client OIDC. Requis si le SSO n'est pas encore configuré."),
    username_claim: str | None = typer.Option(None, "--username-claim",
                                              help="Défaut : email."),
    username_prefix: str | None = typer.Option(None, "--username-prefix",
                                               help="Défaut : oidc:."),
    groups_claim: str | None = typer.Option(
        None, "--groups-claim", help="Claim des groupes (défaut : groups ; cf. --no-groups)."),
    no_groups: bool = typer.Option(
        False, "--no-groups", help="Ne lit aucun claim de groupes."),
    groups_prefix: str | None = typer.Option(None, "--groups-prefix",
                                             help="Défaut : oidc:."),
    extra_scopes: list[str] | None = typer.Option(
        None, "--scope",
        help="Scope OIDC supplémentaire (répétable, 10 au plus). Remplace la liste."),
    no_scopes: bool = typer.Option(False, "--no-scopes", help="Vide la liste des scopes."),
    ca_file: Path | None = typer.Option(
        None, "--ca-file", dir_okay=False,
        help="CA (PEM) de l'émetteur s'il n'est pas signé par une autorité publique."),
    no_ca: bool = typer.Option(False, "--no-ca", help="Retire la CA configurée."),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Configure le SSO du cluster sur votre fournisseur d'identité (OIDC).

    Les options omises gardent la valeur actuelle (CA et scopes compris), ou
    leur défaut si le SSO n'est pas encore configuré. La confirmation liste ce
    qui change. L'apiserver redémarre si la configuration change (coupure
    brève de l'API sur le plan Starter). Ensuite : `cetic k8s kubeconfig <ID>
    --mode sso`, et des (Cluster)RoleBinding sur vos groupes préfixés (ex.
    `oidc:admins`).
    """
    for a, b, msg in ((groups_claim, no_groups, "--groups-claim et --no-groups"),
                      (extra_scopes, no_scopes, "--scope et --no-scopes"),
                      (ca_file, no_ca, "--ca-file et --no-ca")):
        if a and b:
            rprint(f"[red]{msg} sont incompatibles.[/red]", file=sys.stderr)
            raise typer.Exit(1)
    if issuer_url is not None:
        issuer_url = issuer_url.strip()
    if issuer_url is not None and not issuer_url.lower().startswith("https://"):
        rprint("[red]--issuer-url doit être en https://.[/red]", file=sys.stderr)
        raise typer.Exit(1)
    ca_pem = _read_ca_file(ca_file) if ca_file else None
    try:
        c = client.get(f"/v1/k8s/clusters/{cluster_id}")
    except client.APIError as e:
        _fail(e)
    actuelle = c.get("oidc") or None
    base = {k: (actuelle or {}).get(k, _OIDC_DEFAUTS.get(k)) for k in _OIDC_CLES}
    demandes: dict[str, Any] = {
        "issuer_url": issuer_url, "client_id": client_id,
        "username_claim": username_claim, "username_prefix": username_prefix,
        "groups_claim": groups_claim, "groups_prefix": groups_prefix,
        "extra_scopes": list(extra_scopes) if extra_scopes else None, "ca_pem": ca_pem,
    }
    body = {k: (demandes[k] if demandes[k] is not None else base[k]) for k in _OIDC_CLES}
    if no_groups:
        body["groups_claim"] = None
    if no_scopes:
        body["extra_scopes"] = []
    if no_ca:
        body["ca_pem"] = None
    body["extra_scopes"] = list(body["extra_scopes"] or [])
    manquants = [f"--{k.replace('_', '-')}" for k in ("issuer_url", "client_id")
                 if not body[k]]
    if manquants:
        rprint(f"[red]SSO non configuré : {' et '.join(manquants)} requis.[/red]",
               file=sys.stderr)
        raise typer.Exit(1)

    def _montre(k: str, v: Any) -> str:
        if k == "ca_pem":
            return _fmt_ca(v) or "aucune"
        if isinstance(v, list):
            return ", ".join(v) or "aucun"
        return "aucun" if v is None else str(v)

    if actuelle:
        changes = [f"  {k} : {_montre(k, base[k])} → {_montre(k, body[k])}"
                   for k in _OIDC_CLES if base[k] != body[k]]
        if not changes:
            rprint("[yellow]Configuration SSO identique : rien à changer.[/yellow]")
            raise typer.Exit(0)
    else:
        changes = [f"  {k} : {_montre(k, body[k])}" for k in _OIDC_CLES]
    entete = "Modifications du SSO" if actuelle else "Nouvelle configuration SSO"
    rprint(f"{entete} du cluster {cluster_id} :\n" + "\n".join(changes), file=sys.stderr)
    if not yes and not typer.confirm(
        "Appliquer ? L'apiserver redémarre.", err=True,
    ):
        raise typer.Abort()
    try:
        client.put(f"/v1/k8s/clusters/{cluster_id}/oidc", json=body)
    except client.APIError as e:
        _fail(e)
    rprint("[green]✓[/green] SSO configuré.")
    rprint(f"[dim]Kubeconfig : cetic k8s kubeconfig {cluster_id} --mode sso[/dim]")
    rprint(f"[dim]Droits : kubectl create clusterrolebinding … "
           f"--group={body['groups_prefix']}<groupe>[/dim]")


@oidc_app.command(name="delete")
def oidc_delete(
    cluster_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Retire le SSO du cluster (idempotent ; l'apiserver redémarre s'il était
    configuré). Le kubeconfig par certificat reste l'accès administrateur."""
    if not yes and not typer.confirm(
        f"Retirer le SSO du cluster {cluster_id} ?", err=True
    ):
        raise typer.Abort()
    try:
        client.delete(f"/v1/k8s/clusters/{cluster_id}/oidc")
    except client.APIError as e:
        _fail(e)
    rprint("[green]✓[/green] SSO retiré.")


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
        _fail(e)
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
        _fail(e)
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
        _fail(e)
    rprint("[green]✓[/green] Cluster en cours de suppression.")


# ── Node pools ────────────────────────────────────────────────────────────


@pool_app.command(name="list")
def list_pools(cluster_id: str = typer.Argument(...)) -> None:
    """Liste les node pools d'un cluster."""
    try:
        items = client.get(f"/v1/k8s/clusters/{cluster_id}/node-pools")
    except client.APIError as e:
        _fail(e)
    # Version du control plane (pour expliciter les pools en version héritée).
    # Best-effort : on n'échoue pas la liste si le cluster n'est pas lisible.
    cp_version: str | None = None
    try:
        cp_version = client.get(f"/v1/k8s/clusters/{cluster_id}").get("k8s_version")
    except client.APIError:
        cp_version = None
    rows = [
        {"id": p["id"], "name": p["name"], "plan": p["plan"],
         "replicas": p["replicas"],
         "version": _fmt_pool_version(p.get("k8s_version"), cp_version),
         "disk": f"{p['disk_gb']} Go" if p.get("disk_gb") else "(plan)",
         "min": p.get("min_size") or "—",
         "max": p.get("max_size") or "—",
         "status": p["status"]}
        for p in items
    ]
    render_list(rows, title=f"Pools du cluster {cluster_id[:8]} ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("plan", "Plan"),
                         ("replicas", "Replicas"), ("version", "Version"),
                         ("disk", "Disque"), ("min", "Min"), ("max", "Max"),
                         ("status", "Statut")])


@pool_app.command()
def create(
    cluster_id: str = typer.Argument(...),
    name: str = typer.Option(..., "--name", "-n"),
    plan: str = typer.Option("small", "--plan", "-p"),
    replicas: int = typer.Option(1, "--replicas"),
    disk_gb: int | None = typer.Option(
        None, "--disk-gb",
        help=_DISK_HELP,
    ),
    k8s_version: str | None = typer.Option(
        None, "--version",
        help=(
            "Version Kubernetes (workers) du pool (ex: v1.31.0). Omis = hérite du "
            "control plane. Doit être ≤ celle du control plane (le backend re-valide)."
        ),
    ),
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
    if k8s_version is not None:
        _validate_k8s_version(k8s_version)
    body: dict = {"name": name, "plan": plan, "replicas": replicas}
    if k8s_version is not None:
        body["k8s_version"] = k8s_version
    if disk_gb is not None:
        body["disk_gb"] = disk_gb
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
        _fail(e)
    rprint(f"[green]✓[/green] Pool créé : [bold]{p['id']}[/bold]")


@pool_app.command()
def update(
    cluster_id: str = typer.Argument(...),
    pool_id: str = typer.Argument(...),
    replicas: int | None = typer.Option(None, "--replicas"),
    k8s_version: str | None = typer.Option(
        None, "--version",
        help=(
            "Pin/upgrade la version Kubernetes (workers) du pool (ex: v1.31.0). "
            "Doit être ≤ celle du control plane (le backend re-valide → 422). "
            "Pour une mise à niveau suivie : cetic k8s pool upgrade."
        ),
    ),
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
    if k8s_version is not None:
        _validate_k8s_version(k8s_version)
    body: dict = {}
    if replicas is not None:
        body["replicas"] = replicas
    if k8s_version is not None:
        body["k8s_version"] = k8s_version
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
        _fail(e)
    rprint(f"[green]✓[/green] Pool mis à jour : {p.get('name', pool_id)} → replicas={p.get('replicas', '?')}")


@pool_app.command(name="upgrade")
def upgrade_pool(
    cluster_id: str = typer.Argument(...),
    pool_id: str = typer.Argument(...),
    k8s_version: str = typer.Option(
        ..., "--version", "-v",
        help=("Version visée (ex: v1.32.4) : > version du pool, ≤ plan de contrôle, "
              "même mineure que le plan de contrôle."),
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Met à niveau les nœuds d'UN pool (remplacement progressif avec drain).

    À faire après `cetic k8s upgrade-version`, pool par pool. Refusé si une
    autre mise à niveau est active sur le cluster ou si le pool n'est pas actif.
    """
    _validate_k8s_version(k8s_version)
    if not yes and not typer.confirm(
        f"Mettre à niveau le pool {pool_id} vers {k8s_version} ? "
        "Ses nœuds sont remplacés un par un (drain).",
        err=True,
    ):
        raise typer.Abort()
    try:
        p = client.post(f"/v1/k8s/clusters/{cluster_id}/node-pools/{pool_id}/upgrade",
                        json={"k8s_version": k8s_version})
    except client.APIError as e:
        _fail(e)
    rprint(
        f"[green]✓[/green] Pool {p.get('name', pool_id)} → "
        f"{p.get('k8s_version') or k8s_version} (status : {p.get('status', '?')})"
    )


@pool_app.command()
def scale(
    cluster_id: str = typer.Argument(...),
    pool_id: str = typer.Argument(...),
    replicas: int = typer.Option(..., "--replicas", "-n"),
) -> None:
    """Change le nombre de replicas d'un pool."""
    try:
        # Le backend n'expose pas de route /scale ; le scaling passe par le
        # PATCH du node pool avec `replicas` (cf. pool update).
        client.patch(f"/v1/k8s/clusters/{cluster_id}/node-pools/{pool_id}",
                     json={"replicas": replicas})
    except client.APIError as e:
        _fail(e)
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
        _fail(e)
    rprint("[green]✓[/green] Pool supprimé.")
