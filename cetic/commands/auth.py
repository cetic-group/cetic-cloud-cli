"""cetic auth — login (mot de passe ou SSO), logout, whoami."""
from __future__ import annotations

import http.server
import secrets
import threading
import webbrowser
from urllib.parse import parse_qs, urlencode, urlparse

import typer
from rich import print as rprint
from rich.prompt import Prompt

from cetic import client, config

app = typer.Typer(help="Authentification CETIC Cloud")

_SSO_PROVIDERS = ("github", "google")
_SSO_TIMEOUT_SECONDS = 180


@app.command()
def login(
    sso: str | None = typer.Option(
        None, "--sso",
        help=f"Connexion via fournisseur SSO : {', '.join(_SSO_PROVIDERS)}.",
    ),
) -> None:
    """Connexion à CETIC Cloud — stocke le token localement.

    Par défaut : email + mot de passe. Avec [cyan]--sso github[/cyan] ou
    [cyan]--sso google[/cyan] : ouvre le navigateur pour s'authentifier via le
    fournisseur (un serveur local éphémère récupère le jeton, façon `gh`).
    """
    if sso is not None:
        _login_sso(sso.lower())
        return

    rprint("[bold]CETIC Cloud — Connexion[/bold]")
    email = Prompt.ask("Email")
    password = Prompt.ask("Mot de passe", password=True)

    try:
        data = client.post("/v1/auth/login", json={"email": email, "password": password})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)

    _store_tokens(data["access_token"], data["refresh_token"])
    rprint("[green]Connecté avec succès.[/green]")


def _store_tokens(access_token: str, refresh_token: str | None) -> None:
    config.set_value("api_key", access_token)
    if refresh_token:
        config.set_value("refresh_token", refresh_token)


def _login_sso(provider: str) -> None:
    """Flux SSO loopback : navigateur → callback local → tokens stockés."""
    if provider not in _SSO_PROVIDERS:
        rprint(
            f"[red]Erreur : fournisseur SSO inconnu « {provider} ».[/red] "
            f"Valeurs acceptées : {', '.join(_SSO_PROVIDERS)}."
        )
        raise typer.Exit(1)

    cli_state = secrets.token_urlsafe(24)
    result: dict[str, str] = {}
    done = threading.Event()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            params = parse_qs(parsed.query)
            # Lie le callback à CETTE invocation (anti-requête parasite locale).
            if params.get("cli_state", [""])[0] != cli_state:
                self.send_response(400)
                self.end_headers()
                return
            result["access_token"] = params.get("access_token", [""])[0]
            result["refresh_token"] = params.get("refresh_token", [""])[0]
            result["error"] = params.get("oauth_error", [""])[0]
            ok = bool(result["access_token"])
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = (
                "Connexion réussie — vous pouvez fermer cet onglet et revenir au terminal."
                if ok else
                "Échec de la connexion. Revenez au terminal pour les détails."
            )
            self.wfile.write(
                f"<!doctype html><html><body style='font-family:sans-serif'>"
                f"<h2>CETIC Cloud</h2><p>{msg}</p></body></html>".encode()
            )
            done.set()

        def log_message(self, *args: object) -> None:  # silence le logging HTTP
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    cli_redirect = f"http://127.0.0.1:{port}/callback?{urlencode({'cli_state': cli_state})}"
    authorize_url = (
        f"{config.get_api_url().rstrip('/')}/v1/auth/oauth/{provider}/authorize"
        f"?{urlencode({'cli_redirect': cli_redirect})}"
    )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rprint(f"[bold]Connexion SSO via {provider}[/bold]")
        rprint(
            "Ouverture du navigateur… Si rien ne s'ouvre, collez cette URL :\n"
            f"[cyan]{authorize_url}[/cyan]"
        )
        try:
            webbrowser.open(authorize_url)
        except Exception:  # noqa: BLE001 — navigateur indispo (headless)
            pass

        if not done.wait(timeout=_SSO_TIMEOUT_SECONDS):
            rprint(
                f"[red]Délai dépassé ({_SSO_TIMEOUT_SECONDS}s) sans réponse du "
                "fournisseur.[/red]"
            )
            raise typer.Exit(1)
    finally:
        server.shutdown()

    if result.get("error"):
        rprint(f"[red]Erreur SSO : {result['error']}[/red]")
        raise typer.Exit(1)
    if not result.get("access_token"):
        rprint("[red]Erreur : aucun jeton reçu du fournisseur.[/red]")
        raise typer.Exit(1)

    _store_tokens(result["access_token"], result.get("refresh_token"))
    rprint("[green]Connecté avec succès via SSO.[/green]")


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
