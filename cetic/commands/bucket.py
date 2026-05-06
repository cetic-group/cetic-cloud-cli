"""cetic bucket — object storage S3 (Ceph RGW) CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Buckets S3 CETIC Cloud")


@app.command(name="list")
def list_buckets(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les buckets S3."""
    try:
        items = client.get("/v1/buckets", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": b["id"][:8], "name": b["name"], "region": b["region"], "status": b["status"]}
        for b in items
    ]
    render_list(rows, title=f"Buckets ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"), ("status", "Statut")])


@app.command()
def get(bucket_id: str = typer.Argument(...)) -> None:
    """Détails d'un bucket."""
    try:
        b = client.get(f"/v1/buckets/{bucket_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(b, title=f"Bucket {b.get('name', bucket_id)}")


@app.command()
def credentials(bucket_id: str = typer.Argument(...)) -> None:
    """Affiche les credentials master S3 du bucket."""
    try:
        creds = client.get(f"/v1/buckets/{bucket_id}/credentials")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(creds, title="Credentials S3")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
) -> None:
    """Crée un bucket S3."""
    try:
        b = client.post("/v1/buckets", json={"name": name, "region": region})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Bucket créé : [bold]{b['id']}[/bold]")


@app.command()
def delete(
    bucket_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un bucket."""
    if not yes and not typer.confirm(f"Supprimer le bucket {bucket_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/buckets/{bucket_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Bucket supprimé.")


# ── Clés S3 scopées (subusers RGW) ────────────────────────────────────────

key_app = typer.Typer(help="Clés S3 scopées (subusers RGW)")
app.add_typer(key_app, name="key")


@key_app.command(name="list")
def list_keys(
    region: str | None = typer.Option(None, "--region", "-r"),
) -> None:
    """Liste les clés S3 scopées."""
    try:
        items = client.get("/v1/object-storage/keys",
                           params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": k["id"][:8], "label": k["label"], "region": k["region"],
         "access_level": k.get("access_level", "—"),
         "prefix": k.get("access_key_prefix", "—"),
         "expires": (k.get("expires_at") or "—")[:10]}
        for k in items
    ]
    render_list(rows, title=f"Clés S3 ({len(rows)})",
                columns=[("id", "ID"), ("label", "Label"), ("region", "Région"),
                         ("access_level", "Accès"), ("prefix", "Prefix clé"), ("expires", "Expire le")])


@key_app.command()
def create(
    region: str = typer.Option(..., "--region", "-r"),
    label: str = typer.Option(..., "--label", "-l"),
    access_level: str = typer.Option("readwrite", "--access", "-a",
                                     help="read | write | readwrite | full"),
    expires_in_days: int | None = typer.Option(None, "--expires-days",
                                               help="Durée de validité en jours (1–3650)"),
) -> None:
    """Crée une clé S3 scopée. Les credentials ne sont affichés qu'une seule fois."""
    body: dict = {"region": region, "label": label, "access_level": access_level}
    if expires_in_days:
        body["expires_in_days"] = expires_in_days
    try:
        k = client.post("/v1/object-storage/keys", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Clé créée : [bold]{k['id']}[/bold]")
    rprint(f"  [dim]Access Key :[/dim] {k.get('access_key')}")
    rprint(f"  [dim]Secret Key :[/dim] {k.get('secret_key')}")
    rprint(f"  [dim]Endpoint   :[/dim] {k.get('endpoint_url')}")
    rprint("[yellow]⚠ Sauvegardez ces credentials — ils ne seront plus affichés.[/yellow]")


@key_app.command()
def revoke(
    key_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Révoque une clé S3 (suppression permanente)."""
    if not yes and not typer.confirm(f"Révoquer la clé {key_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/object-storage/keys/{key_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Clé révoquée.")
