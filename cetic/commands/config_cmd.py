"""cetic config — get, set, view."""

import typer
from rich import print as rprint
from rich.table import Table

from cetic import config

app = typer.Typer(help="Configuration CLI CETIC Cloud")

SETTABLE_KEYS = {
    "region": f"Région active ({', '.join(config.VALID_REGIONS)})",
    "output": f"Format de sortie ({', '.join(config.VALID_OUTPUTS)})",
    "lang": f"Langue ({', '.join(config.VALID_LANGS)})",
    "api_url": "URL API (dev uniquement)",
}


@app.command()
def view() -> None:
    """Affiche toute la configuration courante."""
    table = Table(title="Configuration lake")
    table.add_column("Clé", style="cyan")
    table.add_column("Valeur", style="white")
    table.add_column("Source", style="dim")

    import os

    for key, val in config.view_all().items():
        source = "env" if os.environ.get(f"CL_{key.upper()}") else "fichier"
        table.add_row(key, val or "[dim]—[/dim]", source)

    rprint(table)


@app.command()
def get(key: str = typer.Argument(..., help="Clé de configuration")) -> None:
    """Affiche la valeur d'une clé de configuration."""
    val = config.get(key)
    if val is None:
        rprint(f"[yellow]Clé '{key}' non définie.[/yellow]")
        raise typer.Exit(1)
    rprint(val)


@app.command(name="set")
def set_cmd(
    key: str = typer.Argument(..., help="Clé de configuration"),
    value: str = typer.Argument(..., help="Valeur"),
) -> None:
    """Définit une valeur de configuration."""
    if key not in SETTABLE_KEYS:
        rprint(
            f"[red]Clé inconnue : '{key}'. "
            f"Clés valides : {', '.join(SETTABLE_KEYS)}[/red]"
        )
        raise typer.Exit(1)

    # Validations
    if key == "region" and value not in config.VALID_REGIONS:
        rprint(f"[red]Région invalide. Valeurs acceptées : {', '.join(config.VALID_REGIONS)}[/red]")
        raise typer.Exit(1)
    if key == "output" and value not in config.VALID_OUTPUTS:
        rprint(f"[red]Format invalide. Valeurs acceptées : {', '.join(config.VALID_OUTPUTS)}[/red]")
        raise typer.Exit(1)
    if key == "lang" and value not in config.VALID_LANGS:
        rprint(f"[red]Langue invalide. Valeurs acceptées : {', '.join(config.VALID_LANGS)}[/red]")
        raise typer.Exit(1)

    config.set_value(key, value)
    rprint(f"[green]{key}[/green] → {value}")
