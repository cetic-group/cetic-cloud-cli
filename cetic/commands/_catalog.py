"""Helpers partagés — catalogue compute (plans, templates, templates custom).

Factorise le rendu des listes de catalogue consommées par plusieurs sous-apps
(`container`, `vm`, `scale-set`, `vm-scale-set`, `k8s`). Les plans compute sont
partagés VM + LXC + scale sets côté backend (table `compute_plans`, endpoint
`GET /v1/compute/plans`), d'où la factorisation ; chaque sous-app expose
néanmoins la commande pour l'ergonomie.

Endpoints backend (source de vérité `apps/api/app/api/v1/`) :
  GET /v1/compute/plans[?kind=&family=]  → compute_plans.py (plans VM/LXC/k8s_node/lb/appgw)
  GET /v1/templates[?include_infra=]     → templates.py (templates LXC)
  GET /v1/qemu-templates                 → qemu_templates.py (templates VM)
  GET /v1/custom-templates               → custom_templates.py (snapshots réutilisables)
  GET /v1/k8s/templates[?region=]        → k8s_templates.py (images CAPI buildées)
  GET /v1/db/engine-versions?engine=...  → db_engine_versions.py (réutilisé par db)
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
    rows = [
        {
            "key": p["key"],
            "label": p.get("name") or "—",
            "family": p.get("family", "—"),
            "vcpu": p.get("cores", "—"),
            "ram_mb": p.get("memory_mb", "—"),
            "disk_gb": p.get("disk_gb", "—"),
            "prix_mois": p.get("price_eur_month") if p.get("price_eur_month") is not None else "—",
            "défaut": "✓" if p.get("is_default") else "",
        }
        for p in data
    ]
    render_list(
        rows,
        title=f"{title} ({len(rows)})",
        columns=[
            ("key", "Plan"), ("label", "Libellé"), ("family", "Famille"),
            ("vcpu", "vCPU"), ("ram_mb", "RAM (Mo)"), ("disk_gb", "Disque (Go)"),
            ("prix_mois", "€/mois"), ("défaut", "Défaut"),
        ],
    )


def render_lxc_templates(include_infra: bool = False) -> None:
    """Liste les templates de conteneurs actifs (`GET /v1/templates`)."""
    params = {"include_infra": "true"} if include_infra else None
    try:
        data = client.get("/v1/templates", params=params)
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
