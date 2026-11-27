"""cetic auth — login (SSO ou clé d'API), logout, whoami."""
from __future__ import annotations

import getpass
import http.server
import os
import re
import secrets
import sys
import threading
import webbrowser
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import typer
from rich import print as rprint
from rich.console import Console
from rich.markup import escape

from cetic import client, config
from cetic._auth_hint import CONNEXION

_err = Console(stderr=True)

app = typer.Typer(help="Authentification CETIC Cloud")

_SSO_PROVIDERS = ("github", "google")
_SSO_TIMEOUT_SECONDS = 180


@app.command()
def login(
    sso: str | None = typer.Option(
        None, "--sso",
        help=f"Connexion via fournisseur SSO : {', '.join(_SSO_PROVIDERS)}.",
    ),
    api_key: bool = typer.Option(
        False, "--api-key",
        help="Enregistre une clé d'API (lue sur l'entrée standard, sans écho) "
             "comme identifiant persistant de la CLI.",
    ),
) -> None:
    """Connexion à CETIC Cloud — stocke l'identifiant localement.

    [cyan]--sso github|google[/cyan] : ouvre le navigateur (un serveur local
    éphémère récupère le jeton, façon `gh`). [cyan]--api-key[/cyan] : enregistre
    une clé d'API créée dans la console. La variable [cyan]CCP_API_KEY[/cyan], si
    elle est définie, prime sur l'identifiant enregistré.
    """
    if sso is not None and api_key:
        _err.print("[red]--sso et --api-key sont exclusifs.[/red]")
        raise typer.Exit(2)
    if sso is not None:
        _login_sso(sso.lower())
        _avertir_si_env()
        return
    if api_key:
        _login_api_key()
        _avertir_si_env()
        return
    # La connexion par e-mail et mot de passe exige la vérification anti-robot
    # de la page de connexion de la console : l'API la refuse toujours en
    # production à un client qui n'est pas un navigateur (403 « Vérification
    # anti-robot échouée »). La CLI ne la propose donc plus. Message sur
    # stderr : la sortie standard reste propre pour un script.
    _err.print(
        "[red]La connexion par e-mail et mot de passe n'est pas disponible "
        "dans la CLI.[/red] Elle exige la vérification anti-robot de la page "
        "de connexion de la console.\n\n"
        "Connectez-vous autrement :\n"
        f"  • par SSO : [cyan]cetic auth login --sso {'|'.join(_SSO_PROVIDERS)}[/cyan] "
        "(compte du même e-mail chez le fournisseur) ;\n"
        "  • par clé d'API, créée dans la console (Clés d'API) : "
        "[cyan]cetic auth login --api-key[/cyan] (enregistrée) ou "
        "[cyan]export CCP_API_KEY=<clé>[/cyan] (session du shell)."
    )
    raise typer.Exit(1)


# Clé d'API (`ccp_live_…`) ou jeton de service account (`ccp_sa_…`) : préfixe
# puis base64 urlsafe. Toute autre forme est une erreur de collage, et un `"`
# ou un `\` aurait en plus cassé le fichier de configuration (revue #85).
_FORME_CLE = re.compile(r"[A-Za-z0-9_\-]{16,512}")


def _login_api_key() -> None:
    """Lit la clé sur l'entrée standard (sans écho si c'est un terminal),
    la VÉRIFIE auprès de l'API, puis l'enregistre comme identifiant persistant,
    sans jeton de rafraîchissement (une clé d'API n'expire pas comme une
    session). Une clé refusée n'est jamais écrite."""
    cle = (getpass.getpass("Clé d'API : ") if sys.stdin.isatty() else sys.stdin.readline()).strip()
    if not cle:
        _err.print("[red]Aucune clé lue.[/red]")
        raise typer.Exit(1)
    if not _FORME_CLE.fullmatch(cle):
        _err.print("[red]Ce n'est pas une clé d'API CETIC Cloud[/red] (caractère inattendu "
                   "ou longueur anormale) : vérifiez le collage. Rien n'a été enregistré.")
        raise typer.Exit(1)
    email = _verifier_cle(cle)
    # Écrase un éventuel jeton de rafraîchissement d'une session SSO : sinon
    # un 401 déclencherait un rafraîchissement qui remplacerait la clé.
    config.set_value("api_key", cle)
    config.set_value("refresh_token", "")
    if email is None:
        rprint("[green]Clé d'API enregistrée.[/green]")
    else:
        rprint(f"[green]Clé d'API enregistrée[/green] (compte {escape(email)}).")


def _verifier_cle(cle: str) -> str | None:
    """`GET /v1/tenants/me` avec CETTE clé, hors de `client._request` (aucun
    rafraîchissement, aucune lecture de la configuration). Rend l'e-mail du
    compte, ou None si la clé est authentique mais sans droit de lire le
    profil (403 : clé sans scope `read`, jeton de compte de service sans
    `auth:GetProfile`). Sort en 1 sans rien écrire si la clé est refusée (401)
    ou si l'API est injoignable."""
    from cetic import __version__  # import local : évite le cycle cetic → commands

    url = config.get_api_url().rstrip("/") + "/v1/tenants/me"
    entetes = {"Authorization": f"Bearer {cle}", "X-CCP-Client": "cli",
               "User-Agent": f"cetic-cli/{__version__}"}
    try:
        with httpx.Client(timeout=30) as http:
            resp = http.get(url, headers=entetes)
    except httpx.HTTPError as exc:
        _err.print(f"[red]API injoignable ({type(exc).__name__}) : la clé n'a pas pu être "
                   "vérifiée. Rien n'a été enregistré.[/red]")
        raise typer.Exit(1) from None
    if resp.status_code == 403:
        detail = _detail_403(resp)
        if detail[0] == "email_non_verifie":
            # Toutes les commandes seraient refusées tant que l'adresse n'est
            # pas vérifiée : ne rien enregistrer, et dire quoi faire (revue).
            _err.print(f"[red]Clé refusée : {escape(detail[1])}[/red] Rien n'a été enregistré.")
            raise typer.Exit(1)
        # Authentifiée mais pas autorisée à lire le profil : la clé est
        # valide, `export CCP_API_KEY=…` fonctionnerait.
        _err.print("[yellow]⚠ La clé est authentique mais n'a pas le droit de lire le profil "
                   f"du compte ({escape(detail[1])}) : les commandes hors de ses droits "
                   "seront refusées.[/yellow]")
        return None
    if resp.status_code == 401:
        _err.print("[red]Clé refusée par l'API[/red] (inconnue, révoquée ou expirée). "
                   "Rien n'a été enregistré.")
        raise typer.Exit(1)
    if not resp.is_success:
        _err.print(f"[red]Vérification de la clé impossible (HTTP {resp.status_code}). "
                   "Rien n'a été enregistré.[/red]")
        raise typer.Exit(1)
    try:
        return str(resp.json().get("email") or "?")
    except ValueError:
        return "?"


def _detail_403(resp: httpx.Response) -> tuple[str | None, str]:
    """(code, message) du `detail` d'un 403 ; message par défaut si le corps
    n'est pas lisible."""
    from cetic.client import _normalize_detail  # noqa: PLC0415 — évite un cycle d'import

    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    code = detail.get("code") if isinstance(detail, dict) else None
    message = _normalize_detail(detail) if detail else "scope ou rôle restreint"
    return code, message


def _avertir_si_env() -> None:
    """`CCP_API_KEY` est lue avant le fichier de configuration : la signaler,
    sinon l'identifiant qui vient d'être enregistré serait ignoré en
    silence."""
    if os.environ.get("CCP_API_KEY"):
        _err.print("[yellow]⚠ La variable CCP_API_KEY est définie : elle prime sur "
                   "l'identifiant enregistré. `unset CCP_API_KEY` pour utiliser celui-ci.[/yellow]")


def _store_tokens(access_token: str, refresh_token: str | None) -> None:
    config.set_value("api_key", access_token)
    if refresh_token:
        config.set_value("refresh_token", refresh_token)


def _login_sso(provider: str) -> None:
    """Flux SSO loopback : navigateur → callback local → tokens stockés."""
    if provider not in _SSO_PROVIDERS:
        _err.print(
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
            _err.print(
                f"[red]Délai dépassé ({_SSO_TIMEOUT_SECONDS}s) sans réponse du "
                "fournisseur.[/red]"
            )
            raise typer.Exit(1)
    finally:
        server.shutdown()

    if result.get("error"):
        # Valeur reçue dans l'URL de rappel : un `[` serait lu comme du
        # balisage Rich.
        _err.print(f"[red]Erreur SSO : {escape(result['error'])}[/red]")
        raise typer.Exit(1)
    if not result.get("access_token"):
        _err.print("[red]Erreur : aucun jeton reçu du fournisseur.[/red]")
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
        _err.print(f"[yellow]Non connecté. Utilisez {CONNEXION}.[/yellow]")
        raise typer.Exit(1)

    try:
        me = client.get("/v1/tenants/me")
    except client.APIError as e:
        _err.print(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1) from None

    rprint(f"[bold]Email[/bold]      : {me['email']}")
    rprint(f"[bold]Prénom[/bold]     : {me['first_name']}")
    rprint(f"[bold]Nom[/bold]        : {me['last_name']}")
    if me.get("company_name"):
        rprint(f"[bold]Société[/bold]    : {me['company_name']}")
    rprint(f"[bold]Statut[/bold]     : {me['status']}")
    rprint(f"[bold]Région active[/bold]: {config.get_region()}")
