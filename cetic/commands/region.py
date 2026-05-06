"""cetic region — list."""

import typer
from rich import print as rprint
from rich.table import Table

from cetic import client, config

app = typer.Typer(help="Régions CETIC Cloud")


@app.command(name="list")
def list_regions() -> None:
    """Liste les régions CETIC Cloud disponibles."""
    try:
        regions = client.get("/v1/regions")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)

    fmt = config.get_output()
    active = config.get_region()

    if fmt == "json":
        import json
        rprint(json.dumps(regions, ensure_ascii=False, indent=2))
        return

    table = Table(title="Régions CETIC Cloud")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Nom", style="white")
    table.add_column("Localisation", style="dim")
    table.add_column("Disponible", justify="center")
    table.add_column("Active", justify="center")

    for r in regions:
        available = "[green]✓[/green]" if r["available"] else "[red]✗[/red]"
        is_active = "[bold cyan]●[/bold cyan]" if r["id"] == active else ""
        table.add_row(r["id"], r["name"], r["location"], available, is_active)

    rprint(table)
