"""cetic auth — login, logout, whoami."""

import typer
from rich import print as rprint
from rich.prompt import Prompt

from cetic import client, config

app = typer.Typer(help="Authentification CETIC Cloud")


@app.command()
def login() -> None:
    """Connexion à CETIC Cloud — stocke le token localement."""
    rprint("[bold]CETIC Cloud — Connexion[/bold]")
    email = Prompt.ask("Email")
    password = Prompt.ask("Mot de passe", password=True)

    try:
        data = client.post("/v1/auth/login", json={"email": email, "password": password})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)

    access_token: str = data["access_token"]
    refresh_token: str = data["refresh_token"]

    config.set_value("api_key", access_token)
    config.set_value("refresh_token", refresh_token)

    rprint("[green]Connecté avec succès.[/green]")


@app.command()
def logout() -> None:
    """Déconnexion — supprime le token local."""
    config.set_value("api_key", "")
    config.set_value("refresh_token", "")
    rprint("[green]Déconnecté.[/green]")


@app.command()
def whoami() -> None:
    """Affiche les informations du compte connecté."""
    token = config.get("api_key")
    if not token:
        rprint("[yellow]Non connecté. Utilisez `cetic auth login`.[/yellow]")
        raise typer.Exit(1)

    try:
        me = client.get("/v1/tenants/me")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)

    rprint(f"[bold]Email[/bold]      : {me['email']}")
    rprint(f"[bold]Prénom[/bold]     : {me['first_name']}")
    rprint(f"[bold]Nom[/bold]        : {me['last_name']}")
    if me.get("company_name"):
        rprint(f"[bold]Société[/bold]    : {me['company_name']}")
    rprint(f"[bold]Statut[/bold]     : {me['status']}")
    rprint(f"[bold]Région active[/bold]: {config.get_region()}")
