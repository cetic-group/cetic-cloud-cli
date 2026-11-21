"""cetic config — get, set, view."""

import typer
from rich import print as rprint
from rich.table import Table

from cetic import config

app = typer.Typer(help="Configuration CLI CETIC Cloud")

SETTABLE_KEYS = {
    "region": f"Région enregistrée ({', '.join(config.VALID_REGIONS)}) — pas encore appliquée par les commandes",
    "output": f"Format de sortie ({', '.join(config.VALID_OUTPUTS)})",
    "lang": f"Langue ({', '.join(config.VALID_LANGS)}) — sans effet, la CLI est en français",
    "api_url": "URL API (dev uniquement)",
}


@app.command()
def view() -> None:
    """Affiche toute la configuration courante."""
    table = Table(title="Configuration CETIC Cloud")
    table.add_column("Clé", style="cyan")
    table.add_column("Valeur", style="white")
    table.add_column("Source", style="dim")

    import os

    for key, val in config.view_all().items():
        source = "env" if os.environ.get(f"CCP_{key.upper()}") else "fichier"
        table.add_row(key, val or "[dim]—[/dim]", source)

    rprint(table)


_SECRETES = {"api_key", "refresh_token"}


@app.command()
def get(
    key: str = typer.Argument(..., help="Clé de configuration"),
    reveal: bool = typer.Option(False, "--reveal", help="Affiche une valeur secrète en clair"),
) -> None:
    """Affiche la valeur d'une clé de configuration (jeton masqué sauf --reveal)."""
    val = config.get(key)
    if val is None:
        rprint(f"[yellow]Clé '{key}' non définie.[/yellow]")
        raise typer.Exit(1)
    # Le jeton s'affichait en clair (historique du terminal, partage d'écran,
    # cetic-cloud-platform#2004) : masqué comme dans `config view` (`***`).
    if key in _SECRETES and not reveal:
        rprint("*** [dim](masqué : --reveal pour l'afficher)[/dim]")
        return
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
    # Ces deux réglages sont enregistrés mais aucune commande ne les lit
    # encore (cetic-cloud-platform#2004) : le dire plutôt que laisser croire
    # qu'ils s'appliquent.
    if key == "lang":
        rprint("[yellow]La CLI n'est disponible qu'en français : ce réglage est sans effet.[/yellow]")
    elif key == "region":
        rprint("[yellow]Les commandes ne lisent pas encore cette région : passez --region.[/yellow]")
