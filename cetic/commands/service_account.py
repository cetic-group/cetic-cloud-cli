"""cetic service-account — Service Accounts CETIC Cloud (identités machine).

Token format : `ccp_sa_<43 urlsafe chars>`, distinct des API keys
(`ccp_live_*`). Le token complet est révélé UNE SEULE FOIS — à la
création et à la rotation. Stockage optionnel dans le trousseau système
via `--save-keyring`.

Sous-commandes :
    cetic service-account list
    cetic service-account get ID|NAME
    cetic service-account create --name NAME [--expires-at ISO] [--save-keyring]
    cetic service-account rotate ID|NAME [--save-keyring]
    cetic service-account revoke ID|NAME [--yes]

Cf. apps/api/IAM_CONTRACT_FROZEN.md (Phase A1 livrée 2026-05-10).
"""
from __future__ import annotations

from typing import Any

import typer
from rich import print as rprint

from cetic import client
from cetic._resolve import resolve_id
from cetic._secrets import (
    delete_sa_token,
    offer_save_sa_token,
    save_sa_token,
)
from cetic.commands._render import render_list, render_one


SA_PATH = "/v1/service-accounts"

app = typer.Typer(help="Service Accounts CETIC Cloud (tokens ccp_sa_)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_api_error(e: client.APIError) -> str:
    if e.status_code == 401:
        return "Non authentifié — vérifiez `cetic auth login` ou `CCP_API_KEY`."
    if e.status_code == 403:
        return "Accès refusé — droits insuffisants (admin requis)."
    if e.status_code == 404:
        return "Service account introuvable."
    if e.status_code == 409:
        return f"Conflit : {e.detail}"
    if e.status_code == 422:
        return f"Données invalides : {e.detail}"
    if e.status_code >= 500:
        return f"Erreur serveur ({e.status_code}). Réessayez plus tard."
    return e.detail or f"Erreur HTTP {e.status_code}"


def _bail(e: client.APIError) -> typer.Exit:
    rprint(f"[red]Erreur : {_format_api_error(e)}[/red]")
    return typer.Exit(1)


def _resolve_sa(id_or_name: str) -> str:
    return resolve_id(SA_PATH, id_or_name)


def _reveal_token(sa: dict[str, Any], save_keyring: bool) -> None:
    """Affiche le token UNE SEULE FOIS + propose stockage trousseau.

    Le token n'est jamais loggé hors de cette fonction.
    """
    token = sa.get("token")
    if not token:
        return
    rprint(
        "\n[bold yellow]Token (à copier maintenant — ne sera plus jamais affiché) :[/bold yellow]"
    )
    rprint(f"[bold]{token}[/bold]")
    rprint(
        f"[dim]Préfixe visible : {sa.get('token_prefix', '—')}[/dim]"
    )
    if save_keyring:
        if save_sa_token(sa["id"], token):
            rprint("[green]✓[/green] Token enregistré dans le trousseau.")
    else:
        offer_save_sa_token(sa["id"], token)


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------


@app.command(name="list")
def list_sas() -> None:
    """Liste les service accounts de l'organisation courante."""
    try:
        items = client.get(SA_PATH)
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": s["id"],
            "name": s["name"],
            "prefix": s.get("token_prefix", "—"),
            "created": (s.get("created_at") or "—")[:10],
            "last_used": (s.get("last_used_at") or "—")[:10],
            "expires": (s.get("expires_at") or "—")[:10],
            "rotated": (s.get("rotated_at") or "—")[:10],
        }
        for s in items
    ]
    render_list(
        rows,
        title=f"Service accounts ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("name", "Nom"),
            ("prefix", "Préfixe"),
            ("created", "Créé"),
            ("last_used", "Utilisé"),
            ("expires", "Expire"),
            ("rotated", "Rotation"),
        ],
    )


@app.command()
def get(id_or_name: str = typer.Argument(..., metavar="ID|NAME")) -> None:
    """Détails d'un service account (jamais le token — seul le préfixe est exposé)."""
    sid = _resolve_sa(id_or_name)
    try:
        sa = client.get(f"{SA_PATH}/{sid}")
    except client.APIError as e:
        raise _bail(e) from e
    render_one(sa, title=f"Service account {sa.get('name', sid)}")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n", help="Nom du service account"),
    description: str | None = typer.Option(None, "--description", "-d"),
    expires_in_days: int | None = typer.Option(
        None, "--expires-in-days",
        help="Durée de validité en jours (1-3650)",
    ),
    expires_at: str | None = typer.Option(
        None, "--expires-at",
        help="(Alias informatif) Le champ canonique côté API est --expires-in-days ; "
        "passez une date ISO si votre version d'API la supporte.",
    ),
    save_keyring: bool = typer.Option(
        False, "--save-keyring",
        help="Stocke le token dans le trousseau système (sans prompt)",
    ),
) -> None:
    """Crée un service account. Token complet retourné UNE SEULE FOIS.

    Optionnellement, `--save-keyring` stocke le token dans le trousseau
    système (Keychain macOS, libsecret Linux, Credential Manager Windows).
    """
    body: dict[str, Any] = {"name": name}
    if description is not None:
        body["description"] = description
    if expires_in_days is not None:
        body["expires_in_days"] = expires_in_days
    elif expires_at:
        # Le contrat API expose `expires_in_days` — on passe `expires_at` brut au
        # cas où le serveur l'accepte (rétro-compat) ; sinon 422 explicite.
        body["expires_at"] = expires_at
    try:
        sa = client.post(SA_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Service account créé : [bold]{sa['name']}[/bold] "
        f"({sa['id']})"
    )
    _reveal_token(sa, save_keyring)


@app.command()
def rotate(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    save_keyring: bool = typer.Option(
        False, "--save-keyring",
        help="Stocke le nouveau token dans le trousseau système",
    ),
) -> None:
    """Régénère le token (ancien invalide immédiatement, pas de période de grâce)."""
    sid = _resolve_sa(id_or_name)
    try:
        sa = client.post(f"{SA_PATH}/{sid}/rotate")
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Token rotaté pour [bold]{sa.get('name', sid)}[/bold]. "
        f"L'ancien token est désormais invalide."
    )
    _reveal_token(sa, save_keyring)


@app.command()
def revoke(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un service account (irréversible)."""
    sid = _resolve_sa(id_or_name)
    if not yes and not typer.confirm(
        f"Supprimer le service account {id_or_name} ? Cette action est irréversible."
    ):
        raise typer.Abort()
    try:
        client.delete(f"{SA_PATH}/{sid}")
    except client.APIError as e:
        raise _bail(e) from e
    delete_sa_token(sid)
    rprint("[green]✓[/green] Service account supprimé.")
