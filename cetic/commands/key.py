"""cetic key — add, list, delete."""

from pathlib import Path

import typer
from rich import print as rprint
from rich.prompt import Confirm
from rich.table import Table

from cetic import client, config

app = typer.Typer(help="Clés SSH")


@app.command()
def add(
    name: str = typer.Option(..., "--name", "-n", help="Nom de la clé"),
    public_key_file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Chemin vers la clé publique (ex: ~/.ssh/id_ed25519.pub)",
        exists=True,
        readable=True,
    ),
) -> None:
    """Ajoute une clé SSH publique au compte."""
    public_key = public_key_file.read_text(encoding="utf-8").strip()

    try:
        key = client.post("/v1/ssh-keys", json={"name": name, "public_key": public_key})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)

    rprint(f"[green]Clé ajoutée[/green] : {key['name']} ({key['fingerprint']})")


@app.command(name="list")
def list_keys() -> None:
    """Liste les clés SSH du compte."""
    try:
        keys = client.get("/v1/ssh-keys")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)

    fmt = config.get_output()

    if fmt == "json":
        import json
        rprint(json.dumps(keys, ensure_ascii=False, indent=2))
        return

    if not keys:
        rprint("[dim]Aucune clé SSH enregistrée.[/dim]")
        return

    table = Table(title="Clés SSH")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Nom", style="cyan")
    table.add_column("Fingerprint", style="white")
    table.add_column("Ajoutée le", style="dim")

    for k in keys:
        table.add_row(k["id"][:8] + "…", k["name"], k["fingerprint"], k["created_at"][:10])

    rprint(table)


@app.command()
def delete(
    key_id: str = typer.Argument(..., help="ID de la clé SSH"),
    force: bool = typer.Option(False, "--force", "-f", help="Supprimer sans confirmation"),
) -> None:
    """Supprime une clé SSH du compte."""
    if not force:
        confirmed = Confirm.ask(f"Supprimer la clé {key_id} ?", default=False)
        if not confirmed:
            raise typer.Abort()

    try:
        client.delete(f"/v1/ssh-keys/{key_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)

    rprint("[green]Clé supprimée.[/green]")
