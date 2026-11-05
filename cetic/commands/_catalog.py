"""Helpers partagés — catalogue compute (plans, templates, templates custom).

Factorise le rendu des listes de catalogue consommées par plusieurs sous-apps
(`container`, `vm`, `scale-set`, `vm-scale-set`, `k8s`). Les plans compute sont
partagés VM + containers + scale sets (endpoint `GET /v1/compute/plans`), d'où la factorisation ; chaque sous-app expose
néanmoins la commande pour l'ergonomie.

Endpoints :
  GET /v1/compute/plans[?kind=&family=]  → plans VM / container / k8s_node / lb / appgw
  GET /v1/templates                      → templates container
  GET /v1/qemu-templates                 → templates VM
  GET /v1/custom-templates               → snapshots réutilisables
  GET /v1/k8s/templates[?region=]        → images OS des nœuds Kubernetes
  GET /v1/db/engine-versions?engine=...  → versions des moteurs (réutilisé par db)
"""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list


# Familles tarifaires acceptées par le backend (kind='compute' uniquement).
COMPUTE_FAMILIES = ("standard", "cpu", "mem")


def render_compute_plans(
    kind: str | None = None,
    family: str | None = None,
    *,
    title: str = "Plans compute",
) -> None:
    """Liste les plans compute (`GET /v1/compute/plans`).

    `kind` filtre côté backend : compute (défaut), vm, container, k8s_node, lb,
    appgw. `family` (standard|cpu|mem) ne s'applique qu'au kind compute.
    """
    params: dict[str, str] = {}
    if kind:
        params["kind"] = kind
    if family:
        params["family"] = family
    try:
        data = client.get("/v1/compute/plans", params=params or None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = []
    appliance = kind in ("appgw", "lb")
    for plan in data:
        row = {
            "key": plan["key"],
            "label": plan.get("name") or "—",
            "family": plan.get("family", "—"),
            "vcpu": plan.get("cores", "—"),
            "ram_mb": plan.get("memory_mb", "—"),
            "disk_gb": plan.get("disk_gb", "—"),
            "min_disk_gb": plan.get("min_disk_gb") if plan.get("min_disk_gb") is not None else "—",
            "prix_mois": plan.get("price_eur_month") if plan.get("price_eur_month") is not None else "—",
            "défaut": "✓" if plan.get("is_default") else "",
        }
        if appliance and "appliance_pricing" in plan:
            # Les montants sont les totaux du mode retenu, calculés par le
            # serveur. Une composante absente ne vaut ni zéro ni l'ancien prix.
            for variant in plan.get("appliance_pricing") or [{}]:
                base = variant.get("base") or {}
                cache = variant.get("cache") or {}
                rows.append({
                    **row,
                    "replica_count": variant.get("replica_count"),
                    "prix_mois": base.get("monthly_price_eur"),
                    "cache_prix_mois": cache.get("monthly_price_eur"),
                    "base_is_free": base.get("is_free"),
                    "cache_is_free": cache.get("is_free"),
                    "currency": base.get("currency") or cache.get("currency"),
                })
        else:
            # Ancienne API : garder le prix indicatif sans inventer de variante.
            if appliance:
                row.update(replica_count=None, cache_prix_mois=None)
            rows.append(row)
    columns = [
        ("key", "Plan"), ("label", "Libellé"), ("family", "Famille"),
        ("vcpu", "vCPU"), ("ram_mb", "RAM (Mo)"), ("disk_gb", "Disque (Go)"),
        ("min_disk_gb", "Disque min (Go)"),
    ]
    if appliance:
        columns += [("replica_count", "Instances"), ("prix_mois", "Base €/mois")]
        if kind == "appgw":
            columns += [("cache_prix_mois", "Option cache €/mois")]
    else:
        columns += [("prix_mois", "€/mois")]
    columns += [("défaut", "Défaut")]
    render_list(rows, title=f"{title} ({len(rows)})", columns=columns)



def render_lxc_templates() -> None:
    """Liste les templates de conteneurs actifs (`GET /v1/templates`)."""
    try:
        data = client.get("/v1/templates")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"key": t["key"], "display_name": t.get("display_name", ""),
         "défaut": "✓" if t.get("is_default") else ""}
        for t in data
    ]
    render_list(
        rows,
        title=f"Templates container (CT) ({len(rows)})",
        columns=[("key", "Clé"), ("display_name", "Nom"), ("défaut", "Défaut")],
    )


def render_qemu_templates() -> None:
    """Liste les templates de machines virtuelles actifs (`GET /v1/qemu-templates`)."""
    try:
        data = client.get("/v1/qemu-templates")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"key": t["key"], "display_name": t.get("display_name", ""),
         "défaut": "✓" if t.get("is_default") else ""}
        for t in data
    ]
    render_list(
        rows,
        title=f"Templates de machines virtuelles ({len(rows)})",
        columns=[("key", "Clé"), ("display_name", "Nom"), ("défaut", "Défaut")],
    )


def render_custom_templates(template_type: str | None = None) -> None:
    """Liste les templates custom de l'org (`GET /v1/custom-templates`).

    `template_type` (container|vm) filtre côté client — l'endpoint renvoie un
    set mélangé. ID jamais tronqué (règle repo).
    """
    try:
        items = client.get("/v1/custom-templates")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    if template_type:
        items = [t for t in items if t.get("template_type") == template_type]
    rows = [
        {
            "id": t["id"],
            "name": t.get("name", ""),
            "template_type": t.get("template_type", "—"),
            "os": "Windows" if t.get("os_family") == "windows" else "Linux",
            "region": t.get("region", "—"),
            "status": t.get("status", "—"),
            "disk_gb": t.get("disk_gb") if t.get("disk_gb") is not None else "—",
            "source": t.get("source_instance_type") or "—",
            "created_at": (t.get("created_at") or "")[:10],
        }
        for t in items
    ]
    suffix = f" {template_type}" if template_type else ""
    render_list(
        rows,
        title=f"Templates custom{suffix} ({len(rows)})",
        columns=[
            ("id", "ID"), ("name", "Nom"), ("template_type", "Type"), ("os", "OS"),
            ("region", "Région"), ("status", "Statut"), ("disk_gb", "Disque (Go)"),
            ("source", "Source"), ("created_at", "Créé le"),
        ],
    )
