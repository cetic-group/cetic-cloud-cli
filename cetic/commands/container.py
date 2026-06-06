"""cetic container — gestion des containers CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._catalog import (
    render_compute_plans,
    render_custom_templates,
    render_lxc_templates,
)
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Containers (CT) CETIC Cloud")


@app.command(name="list")
def list_containers(
    region: str | None = typer.Option(None, "--region", "-r", help="Filtrer par région"),
) -> None:
    """Liste les containers."""
    try:
        items = client.get("/v1/containers", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)

    rows = [
        {
            "id": c["id"],
            "name": c["name"],
            "region": c["region"],
            "plan": c["plan"],
            "status": c["status"],
            "ip": c.get("ip_address") or "—",
        }
        for c in items
    ]
    render_list(
        rows,
        title=f"Containers ({len(rows)})",
        columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                 ("plan", "Plan"), ("status", "Statut"), ("ip", "IP")],
    )


@app.command()
def get(container_id: str = typer.Argument(..., help="UUID du container")) -> None:
    """Affiche les détails d'un container."""
    try:
        c = client.get(f"/v1/containers/{container_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(c, title=f"Container {c.get('name', container_id)}")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
    plan: str = typer.Option("nano", "--plan", "-p", help="nano|micro|small|medium|large|xlarge"),
    template: str = typer.Option("debian-12", "--template", "-t"),
    vnet_id: str = typer.Option(..., "--vnet", help="UUID du VNet"),
    ssh_key_ids: list[str] = typer.Option(None, "--ssh-key", help="UUID(s) des clés SSH (répéter)"),
    root_password: str = typer.Option(
        ..., "--root-password",
        prompt=True, hide_input=True, confirmation_prompt=True,
        help="Mot de passe root (8 chars min). Demandé interactivement si non fourni.",
    ),
) -> None:
    """Crée un container.

    Le mot de passe root est obligatoire (politique CCP v1.4.0+, 8 chars min).
    Si non fourni via `--root-password`, Typer le demande interactivement
    (avec masquage + confirmation).
    """
    if len(root_password) < 8:
        rprint("[red]Erreur : le mot de passe root doit faire au moins 8 caractères.[/red]")
        raise typer.Exit(1)
    body = {
        "name": name,
        "region": region,
        "plan": plan,
        "template": template,
        "vnet_id": vnet_id,
        "root_password": root_password,
    }
    if ssh_key_ids:
        body["ssh_key_ids"] = ssh_key_ids
    try:
        c = client.post("/v1/containers", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Container créé : [bold]{c['id']}[/bold]")
    render_one(c, title=c.get("name", c["id"]))


@app.command()
def delete(
    container_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Supprime un container."""
    if not yes and not typer.confirm(f"Supprimer le container {container_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/containers/{container_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Container supprimé.")


@app.command()
def start(container_id: str = typer.Argument(...)) -> None:
    """Démarre un container."""
    try:
        client.post(f"/v1/containers/{container_id}/start")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Démarrage demandé.")


@app.command()
def stop(container_id: str = typer.Argument(...)) -> None:
    """Stoppe un container."""
    try:
        client.post(f"/v1/containers/{container_id}/stop")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Arrêt demandé.")


@app.command()
def reboot(container_id: str = typer.Argument(...)) -> None:
    """Redémarre un container."""
    try:
        client.post(f"/v1/containers/{container_id}/reboot")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Redémarrage demandé.")


# ── Catalogue (plans & templates) ─────────────────────────────────────────


@app.command()
def plans() -> None:
    """Liste les plans compute disponibles (partagés VM/container)."""
    render_compute_plans(kind="container", title="Plans container")


@app.command()
def templates(
    include_infra: bool = typer.Option(
        False, "--include-infra",
        help="Inclure les templates d'infrastructure interne (réservés CETIC).",
    ),
) -> None:
    """Liste les templates container (CT) disponibles."""
    render_lxc_templates(include_infra)


@app.command(name="custom-templates")
def custom_templates() -> None:
    """Liste les templates custom container de l'organisation (snapshots réutilisables)."""
    render_custom_templates(template_type="container")


# ── Snapshots ─────────────────────────────────────────────────────────────

snapshot_app = typer.Typer(help="Snapshots d'un container")
app.add_typer(snapshot_app, name="snapshot")


@snapshot_app.command(name="list")
def list_snapshots(container_id: str = typer.Argument(...)) -> None:
    """Liste les snapshots d'un container."""
    try:
        items = client.get(f"/v1/containers/{container_id}/snapshots")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [{"id": s["id"], "name": s["name"], "status": s["status"],
             "size": s.get("size_bytes") or "—", "created_at": s.get("created_at", "")[:10]}
            for s in items]
    render_list(rows, title=f"Snapshots du container {container_id[:8]} ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("status", "Statut"),
                         ("size", "Taille"), ("created_at", "Créé le")])


@snapshot_app.command()
def create(
    container_id: str = typer.Argument(...),
    name: str = typer.Option(..., "--name", "-n"),
    description: str | None = typer.Option(None, "--desc"),
) -> None:
    """Crée un snapshot du container."""
    body: dict = {"name": name}
    if description:
        body["description"] = description
    try:
        s = client.post(f"/v1/containers/{container_id}/snapshots", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Snapshot créé : [bold]{s['id']}[/bold]")


@snapshot_app.command()
def delete(
    container_id: str = typer.Argument(...),
    snapshot_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un snapshot."""
    if not yes and not typer.confirm(f"Supprimer le snapshot {snapshot_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/containers/{container_id}/snapshots/{snapshot_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Snapshot supprimé.")


@snapshot_app.command()
def restore(
    container_id: str = typer.Argument(...),
    snapshot_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Restaure le container depuis un snapshot (rollback)."""
    if not yes and not typer.confirm(
        f"Restaurer le container {container_id[:8]} depuis le snapshot {snapshot_id[:8]} ?"
    ):
        raise typer.Abort()
    try:
        client.post(f"/v1/containers/{container_id}/snapshots/{snapshot_id}/rollback")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Restauration en cours.")
