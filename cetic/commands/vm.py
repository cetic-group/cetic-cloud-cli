"""cetic vm — gestion des machines virtuelles CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Machines virtuelles (QEMU) CETIC Cloud")


@app.command(name="list")
def list_vms(
    region: str | None = typer.Option(None, "--region", "-r"),
) -> None:
    """Liste les VMs."""
    try:
        items = client.get("/v1/vm-instances", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "id": v["id"][:8], "name": v["name"], "region": v["region"],
            "plan": v["plan"], "status": v["status"], "ip": v.get("ip_address") or "—",
        }
        for v in items
    ]
    render_list(rows, title=f"VMs ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("plan", "Plan"), ("status", "Statut"), ("ip", "IP")])


@app.command()
def get(vm_id: str = typer.Argument(...)) -> None:
    """Détails d'une VM."""
    try:
        v = client.get(f"/v1/vm-instances/{vm_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(v, title=f"VM {v.get('name', vm_id)}")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
    plan: str = typer.Option("small", "--plan", "-p"),
    template: str = typer.Option("ubuntu-24.04", "--template", "-t"),
    vnet_id: str = typer.Option(..., "--vnet"),
    ssh_key_ids: list[str] = typer.Option(None, "--ssh-key"),
    root_password: str = typer.Option(
        ..., "--root-password",
        prompt=True, hide_input=True, confirmation_prompt=True,
        help="Mot de passe root (8 chars min). Demandé interactivement si non fourni.",
    ),
) -> None:
    """Crée une VM.

    Le mot de passe root est obligatoire (politique CCP v1.4.0+, 8 chars min).
    Si non fourni via `--root-password`, Typer le demande interactivement
    (avec masquage + confirmation).
    """
    if len(root_password) < 8:
        rprint("[red]Erreur : le mot de passe root doit faire au moins 8 caractères.[/red]")
        raise typer.Exit(1)
    body = {
        "name": name, "region": region, "plan": plan, "template": template,
        "vnet_id": vnet_id, "root_password": root_password,
    }
    if ssh_key_ids:
        body["ssh_key_ids"] = ssh_key_ids
    try:
        v = client.post("/v1/vm-instances", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] VM créée : [bold]{v['id']}[/bold]")


@app.command()
def delete(
    vm_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime une VM."""
    if not yes and not typer.confirm(f"Supprimer la VM {vm_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/vm-instances/{vm_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] VM supprimée.")


@app.command()
def start(vm_id: str = typer.Argument(...)) -> None:
    """Démarre une VM."""
    try:
        client.post(f"/v1/vm-instances/{vm_id}/start")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Démarrage demandé.")


@app.command()
def stop(vm_id: str = typer.Argument(...)) -> None:
    """Stoppe une VM."""
    try:
        client.post(f"/v1/vm-instances/{vm_id}/stop")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Arrêt demandé.")


@app.command()
def reboot(vm_id: str = typer.Argument(...)) -> None:
    """Redémarre une VM."""
    try:
        client.post(f"/v1/vm-instances/{vm_id}/reboot")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Redémarrage demandé.")


# ── Snapshots ─────────────────────────────────────────────────────────────

snapshot_app = typer.Typer(help="Snapshots d'une VM")
app.add_typer(snapshot_app, name="snapshot")


@snapshot_app.command(name="list")
def list_snapshots(vm_id: str = typer.Argument(...)) -> None:
    """Liste les snapshots d'une VM."""
    try:
        items = client.get(f"/v1/vms/{vm_id}/snapshots")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [{"id": s["id"][:8], "name": s["name"], "status": s["status"],
             "size": s.get("size_bytes") or "—", "created_at": s.get("created_at", "")[:10]}
            for s in items]
    render_list(rows, title=f"Snapshots de la VM {vm_id[:8]} ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("status", "Statut"),
                         ("size", "Taille"), ("created_at", "Créé le")])


@snapshot_app.command()
def create(
    vm_id: str = typer.Argument(...),
    name: str = typer.Option(..., "--name", "-n"),
    description: str | None = typer.Option(None, "--desc"),
) -> None:
    """Crée un snapshot de la VM."""
    body: dict = {"name": name}
    if description:
        body["description"] = description
    try:
        s = client.post(f"/v1/vms/{vm_id}/snapshots", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Snapshot créé : [bold]{s['id']}[/bold]")


@snapshot_app.command()
def delete(
    vm_id: str = typer.Argument(...),
    snapshot_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un snapshot."""
    if not yes and not typer.confirm(f"Supprimer le snapshot {snapshot_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/vms/{vm_id}/snapshots/{snapshot_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Snapshot supprimé.")


@snapshot_app.command()
def restore(
    vm_id: str = typer.Argument(...),
    snapshot_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Restaure la VM depuis un snapshot (rollback)."""
    if not yes and not typer.confirm(
        f"Restaurer la VM {vm_id[:8]} depuis le snapshot {snapshot_id[:8]} ?"
    ):
        raise typer.Abort()
    try:
        client.post(f"/v1/vms/{vm_id}/snapshots/{snapshot_id}/rollback")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Restauration en cours.")
