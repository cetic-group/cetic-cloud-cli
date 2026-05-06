"""cetic api-key — clés API CETIC Cloud (machine-to-machine auth)."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Clés API CETIC Cloud (ccp_live_)")


@app.command(name="list")
def list_keys() -> None:
    """Liste les clés API du tenant courant."""
    try:
        items = client.get("/v1/api-keys")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": k["id"][:8], "name": k["name"], "prefix": k.get("prefix", "—"),
         "scopes": ",".join(k.get("scopes", [])),
         "expires_at": (k.get("expires_at") or "—")[:10],
         "last_used_at": (k.get("last_used_at") or "—")[:10]}
        for k in items
    ]
    render_list(rows, title=f"Clés API ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("prefix", "Préfixe"),
                         ("scopes", "Scopes"), ("expires_at", "Expire"), ("last_used_at", "Utilisée")])


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    scopes: list[str] = typer.Option(["read"], "--scope", "-s",
                                     help="read | write | billing | admin (répéter)"),
    expires_in_days: int | None = typer.Option(None, "--expires", help="1-3650"),
) -> None:
    """Crée une clé API. Le token est affiché UNE SEULE FOIS — copier immédiatement."""
    body = {"name": name, "scopes": scopes}
    if expires_in_days:
        body["expires_in_days"] = expires_in_days
    try:
        k = client.post("/v1/api-keys", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Clé API créée : [bold]{k['id']}[/bold]")
    if "token" in k:
        rprint(f"\n[bold yellow]Token (à copier maintenant — ne sera plus jamais affiché) :[/bold yellow]")
        rprint(f"[bold]{k['token']}[/bold]")


@app.command()
def get(key_id: str = typer.Argument(...)) -> None:
    """Détails d'une clé API."""
    try:
        k = client.get(f"/v1/api-keys/{key_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(k, title=f"Clé API {k.get('name', key_id)}")


@app.command()
def revoke(
    key_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Révoque (supprime) une clé API."""
    if not yes and not typer.confirm(f"Révoquer la clé {key_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/api-keys/{key_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Clé révoquée.")
