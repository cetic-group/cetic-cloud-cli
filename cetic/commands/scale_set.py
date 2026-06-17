"""cetic scale-set / vm-scale-set — auto-scaling groups CETIC Cloud."""

from pathlib import Path

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._catalog import (
    render_compute_plans,
    render_lxc_templates,
    render_qemu_templates,
)
from cetic.commands._compute import (
    apply_compute_access_options,
    validate_windows_password,
)
from cetic.commands._render import render_list, render_one

container_app = typer.Typer(help="Container Scale Sets CETIC Cloud")
vm_app = typer.Typer(help="VM Scale Sets CETIC Cloud")


def _list(endpoint: str, kind: str, show_os: bool = False) -> None:
    try:
        items = client.get(endpoint)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": s["id"], "name": s["name"], "region": s["region"],
         "os": "Windows" if s.get("os_family") == "windows" else "Linux",
         "plan": s["plan"], "replicas": s.get("desired_replicas") or s.get("replicas", 0),
         "status": s["status"]}
        for s in items
    ]
    columns = [("id", "ID"), ("name", "Nom"), ("region", "Région")]
    if show_os:
        columns.append(("os", "OS"))
    columns += [("plan", "Plan"), ("replicas", "Replicas"), ("status", "Statut")]
    render_list(rows, title=f"{kind} ({len(rows)})", columns=columns)


def _scale(endpoint: str, replicas: int) -> None:
    try:
        client.post(f"{endpoint}/scale", json={"desired_replicas": replicas})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Scale → {replicas} replicas demandé.")


def _create(  # noqa: PLR0913
    *, endpoint: str, kind: str, name: str, region: str, plan: str, template: str,
    vnet: str | None, root_password: str, desired: int, min_: int, max_: int,
    ssh_key: list[str] | None, tag: list[str] | None,
    cloud_init: Path | None = None, bastion_access: bool = False,
    windows_license_consent: bool = False,
) -> None:
    if len(root_password) < 8:
        rprint("[red]Erreur : le mot de passe root doit faire au moins 8 caractères.[/red]")
        raise typer.Exit(1)
    if windows_license_consent:
        validate_windows_password(root_password)
    body: dict = {
        "name": name, "region": region, "plan": plan, "template": template,
        "root_password": root_password,
        "desired_instances": desired, "min_instances": min_, "max_instances": max_,
    }
    if vnet:
        body["vnet_id"] = vnet
    if ssh_key:
        body["ssh_key_ids"] = ssh_key
    if tag:
        body["tags"] = tag
    apply_compute_access_options(
        body, cloud_init=cloud_init, bastion_access=bastion_access,
        windows_license_consent=windows_license_consent,
    )
    try:
        s = client.post(endpoint, json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] {kind} créé : [bold]{s['id']}[/bold] "
           f"([dim]{s.get('status', '?')}[/dim], {desired} replicas)")
    render_one(s, title=s.get("name", s["id"]))


@container_app.command(name="list")
def list_css(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les container scale sets."""
    suffix = f"?region={region}" if region else ""
    _list(f"/v1/container-scale-sets{suffix}", "Container Scale Sets")


@container_app.command()
def create(  # noqa: PLR0913
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
    plan: str = typer.Option("nano", "--plan", "-p", help="nano|micro|small|medium|large|xlarge"),
    template: str = typer.Option("debian-12", "--template", "-t"),
    vnet: str = typer.Option(..., "--vnet", help="UUID du VNet"),
    desired: int = typer.Option(1, "--desired", help="Replicas souhaités au démarrage."),
    min_: int = typer.Option(1, "--min", help="Replicas minimum (autoscaling)."),
    max_: int = typer.Option(10, "--max", help="Replicas maximum (autoscaling)."),
    ssh_key: list[str] = typer.Option(None, "--ssh-key", help="UUID(s) des clés SSH (répéter)."),
    tag: list[str] = typer.Option(None, "--tag", help="Tag(s) (répéter)."),
    cloud_init: Path = typer.Option(
        None, "--cloud-init",
        exists=True, file_okay=True, dir_okay=False, readable=True,
        help="Fichier cloud-init (cloud-config) appliqué au premier démarrage.",
    ),
    bastion_access: bool = typer.Option(
        False, "--bastion-access",
        help="Autoriser l'accès SSH via le Bastion du tenant (opt-in).",
    ),
    root_password: str = typer.Option(
        ..., "--root-password", prompt=True, hide_input=True, confirmation_prompt=True,
        help="Mot de passe root (8 chars min). Demandé interactivement si non fourni.",
    ),
) -> None:
    """Crée un container scale set (auto-scaling group de containers)."""
    _create(endpoint="/v1/container-scale-sets", kind="Container scale set",
            name=name, region=region, plan=plan, template=template, vnet=vnet,
            root_password=root_password, desired=desired, min_=min_, max_=max_,
            ssh_key=ssh_key, tag=tag,
            cloud_init=cloud_init, bastion_access=bastion_access)


@container_app.command()
def get(set_id: str = typer.Argument(...)) -> None:
    """Détails d'un scale set."""
    try:
        s = client.get(f"/v1/container-scale-sets/{set_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(s, title=s.get("name", set_id))


@container_app.command()
def scale(
    set_id: str = typer.Argument(...),
    replicas: int = typer.Option(..., "--replicas", "-n"),
) -> None:
    """Change le nombre de replicas."""
    _scale(f"/v1/container-scale-sets/{set_id}", replicas)


@container_app.command()
def delete(
    set_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un scale set (cascade replicas)."""
    if not yes and not typer.confirm(f"Supprimer le scale set {set_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/container-scale-sets/{set_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Scale set supprimé.")


@container_app.command()
def plans() -> None:
    """Liste les plans compute disponibles (partagés VM/container)."""
    render_compute_plans(kind="container", title="Plans container scale set")


@container_app.command()
def templates() -> None:
    """Liste les templates container (CT) disponibles."""
    render_lxc_templates()


@vm_app.command(name="list")
def list_vmss(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les VM scale sets."""
    suffix = f"?region={region}" if region else ""
    _list(f"/v1/vm-scale-sets{suffix}", "VM Scale Sets", show_os=True)


@vm_app.command()
def create(  # noqa: PLR0913
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
    plan: str = typer.Option("nano", "--plan", "-p", help="nano|micro|small|medium|large|xlarge"),
    template: str = typer.Option("ubuntu-24.04", "--template", "-t"),
    vnet: str = typer.Option(..., "--vnet", help="UUID du VNet"),
    desired: int = typer.Option(1, "--desired", help="Replicas souhaités au démarrage."),
    min_: int = typer.Option(1, "--min", help="Replicas minimum (autoscaling)."),
    max_: int = typer.Option(10, "--max", help="Replicas maximum (autoscaling)."),
    ssh_key: list[str] = typer.Option(None, "--ssh-key", help="UUID(s) des clés SSH (répéter)."),
    tag: list[str] = typer.Option(None, "--tag", help="Tag(s) (répéter)."),
    cloud_init: Path = typer.Option(
        None, "--cloud-init",
        exists=True, file_okay=True, dir_okay=False, readable=True,
        help="Fichier cloud-init (cloud-config) appliqué au premier démarrage.",
    ),
    bastion_access: bool = typer.Option(
        False, "--bastion-access",
        help="Autoriser l'accès SSH via le Bastion du tenant (opt-in).",
    ),
    windows_license_consent: bool = typer.Option(
        False, "--windows-license-consent",
        help="Obligatoire pour un template Windows (win-*) : reconnaître que CETIC Cloud "
             "ne fournit pas les licences Windows. Plan medium+ et mot de passe "
             "administrateur fort requis.",
    ),
    root_password: str = typer.Option(
        ..., "--root-password", prompt=True, hide_input=True, confirmation_prompt=True,
        help="Mot de passe root (Linux) ou administrateur (Windows). 8 chars min "
             "(Windows : 12 chars min + 3 catégories). Demandé interactivement si non fourni.",
    ),
) -> None:
    """Crée un VM scale set (auto-scaling group de machines virtuelles, Linux ou Windows).

    Pour un scale set Windows (template `win-*` ou template custom Windows),
    passer `--windows-license-consent` : accès RDP, plan `medium`+ et mot de passe
    administrateur ≥ 12 caractères / ≥ 3 catégories.
    """
    _create(endpoint="/v1/vm-scale-sets", kind="VM scale set",
            name=name, region=region, plan=plan, template=template, vnet=vnet,
            root_password=root_password, desired=desired, min_=min_, max_=max_,
            ssh_key=ssh_key, tag=tag,
            cloud_init=cloud_init, bastion_access=bastion_access,
            windows_license_consent=windows_license_consent)


@vm_app.command()
def get(set_id: str = typer.Argument(...)) -> None:
    """Détails d'un VM scale set."""
    try:
        s = client.get(f"/v1/vm-scale-sets/{set_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(s, title=s.get("name", set_id))


@vm_app.command()
def scale(
    set_id: str = typer.Argument(...),
    replicas: int = typer.Option(..., "--replicas", "-n"),
) -> None:
    """Change le nombre de replicas."""
    _scale(f"/v1/vm-scale-sets/{set_id}", replicas)


@vm_app.command()
def delete(
    set_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un VM scale set."""
    if not yes and not typer.confirm(f"Supprimer le VM scale set {set_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/vm-scale-sets/{set_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] VM scale set supprimé.")


@vm_app.command()
def plans() -> None:
    """Liste les plans compute disponibles (partagés VM/container)."""
    render_compute_plans(kind="vm", title="Plans VM scale set")


@vm_app.command()
def templates() -> None:
    """Liste les templates VM (QEMU) disponibles."""
    render_qemu_templates()
