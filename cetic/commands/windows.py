"""cetic windows — gestion des instances Windows CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._catalog import render_compute_plans
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Instances Windows (dockur) CETIC Cloud")


@app.command(name="list")
def list_windows(
    region: str | None = typer.Option(None, "--region", "-r"),
) -> None:
    """Liste les instances Windows."""
    try:
        items = client.get("/v1/windows-instances", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {
            "id": w["id"], "name": w["name"], "region": w["region"],
            "plan": w["plan"], "status": w["status"], "ip": w.get("ip_address") or "—",
        }
        for w in items
    ]
    render_list(rows, title=f"Instances Windows ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("plan", "Plan"), ("statut", "Statut"), ("ip", "IP")])


@app.command()
def get(instance_id: str = typer.Argument(...)) -> None:
    """Détails d'une instance Windows."""
    try:
        w = client.get(f"/v1/windows-instances/{instance_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(w, title=f"Instance Windows {w.get('name', instance_id)}")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
    plan: str = typer.Option(..., "--plan", "-p"),
    template: str = typer.Option(..., "--template", "-t", help="Version Windows (clé de template)"),
    administrator_password: str = typer.Option(..., "--administrator-password", prompt=True, hide_input=True, confirmation_prompt=True),
    vnet_id: str = typer.Option(None, "--vnet"),
    data_volume: list[str] = typer.Option(None, "--data-volume", help="ID de volume data (répétable, max 5)"),
    public_ip_id: str = typer.Option(None, "--public-ip"),
    tags: list[str] = typer.Option(None, "--tag"),
    accept_license: bool = typer.Option(False, "--accept-license", help="Obligatoire : j'atteste détenir une licence Windows valide"),
) -> None:
    """Crée une instance Windows.

    AVERTISSEMENT LICENCE : CETIC Cloud Platform ne fournit ni ne gère aucune
    licence Microsoft Windows. Vous êtes seul responsable de l'acquisition d'une
    licence valide pour chaque instance. Relancez avec --accept-license pour
    confirmer.
    """
    if not accept_license:
        rprint(
            "[yellow]⚠️ Licence Windows non incluse[/yellow] — "
            "CETIC Cloud Platform ne fournit ni ne gère aucune licence Microsoft Windows. "
            "Vous êtes seul responsable de l'acquisition d'une licence valide pour chaque instance. "
            "Relancez avec --accept-license pour confirmer."
        )
        raise typer.Exit(1)

    if data_volume and len(data_volume) > 5:
        raise typer.BadParameter("max 5 disques data")

    body = {
        "name": name, "region": region, "plan": plan, "template_key": template,
        "administrator_password": administrator_password, "license_consent": True,
    }
    if vnet_id:
        body["vnet_id"] = vnet_id
    if public_ip_id:
        body["public_ip_id"] = public_ip_id
    if data_volume:
        body["data_volume_ids"] = data_volume
    if tags:
        body["tags"] = tags

    try:
        w = client.post("/v1/windows-instances", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Instance Windows créée : [bold]{w['id']}[/bold]")


@app.command()
def delete(
    instance_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime une instance Windows."""
    if not yes and not typer.confirm(f"Supprimer l'instance Windows {instance_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/windows-instances/{instance_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Instance Windows supprimée.")


@app.command()
def start(instance_id: str = typer.Argument(...)) -> None:
    """Démarre une instance Windows."""
    try:
        client.post(f"/v1/windows-instances/{instance_id}/start")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Démarrage demandé.")


@app.command()
def stop(instance_id: str = typer.Argument(...)) -> None:
    """Stoppe une instance Windows."""
    try:
        client.post(f"/v1/windows-instances/{instance_id}/stop")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Arrêt demandé.")


@app.command()
def reboot(instance_id: str = typer.Argument(...)) -> None:
    """Redémarre une instance Windows."""
    try:
        client.post(f"/v1/windows-instances/{instance_id}/reboot")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Redémarrage demandé.")


# ── Catalogue (plans & templates) ─────────────────────────────────────────


@app.command()
def plans() -> None:
    """Liste les plans compute disponibles (partagés VM/container/Windows)."""
    render_compute_plans(kind="compute", title="Plans Windows")


@app.command()
def templates() -> None:
    """Liste les templates Windows disponibles."""
    try:
        data = client.get("/v1/windows-instances/templates")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"key": t["key"], "display_name": t.get("display_name", ""),
         "dockur_version": t.get("dockur_version", "—")}
        for t in data
    ]
    render_list(
        rows,
        title=f"Templates Windows ({len(rows)})",
        columns=[("key", "Clé"), ("display_name", "Nom"), ("dockur_version", "Version Dockur")],
    )


@app.command()
def credentials(instance_id: str = typer.Argument(...)) -> None:
    """Récupère les identifiants d'une instance Windows."""
    try:
        creds = client.get(f"/v1/windows-instances/{instance_id}/credentials")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[bold]Identifiants de {instance_id[:8]}[/bold]")
    rprint(f"Utilisateur : {creds.get('username', '—')}")
    rprint(f"Mot de passe : {creds.get('password', '—')}")
