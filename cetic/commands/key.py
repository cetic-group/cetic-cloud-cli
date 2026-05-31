"""cetic key — add, list, delete (avec scoping tenant/org/user)."""

from pathlib import Path

import typer
from rich import print as rprint
from rich.prompt import Confirm
from rich.table import Table

from cetic import client, config

app = typer.Typer(help="Clés SSH")


VALID_SCOPES = ("user", "org", "tenant")

# Rich styling par scope — aligné console / docs.
_SCOPE_STYLE = {
    "user": "cyan",
    "org": "blue",
    "tenant": "magenta",
}


def _format_api_error(e: client.APIError) -> str:
    """Mapping uniforme des erreurs API → messages FR humains."""
    if e.status_code == 401:
        return "Non authentifié — vérifiez `cetic auth login` ou `CCP_API_KEY`."
    if e.status_code == 403:
        return (
            "Accès refusé — droits insuffisants. Une clé `user` ne peut être "
            "supprimée que par son créateur ; les clés `org`/`tenant` "
            "requièrent un rôle admin/owner."
        )
    if e.status_code == 404:
        return "Clé SSH introuvable."
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
    scope: str = typer.Option(
        "user",
        "--scope",
        help=(
            "Visibilité de la clé (user=personnelle, org=organisation, "
            "tenant=tout le compte)"
        ),
        case_sensitive=False,
    ),
) -> None:
    """Ajoute une clé SSH publique au compte."""
    scope_norm = scope.lower()
    if scope_norm not in VALID_SCOPES:
        rprint(
            f"[red]--scope invalide : '{scope}'. "
            f"Valeurs autorisées : {', '.join(VALID_SCOPES)}.[/red]"
        )
        raise typer.Exit(1)

    public_key = public_key_file.read_text(encoding="utf-8").strip()

    body = {"name": name, "public_key": public_key, "scope": scope_norm}
    try:
        key = client.post("/v1/ssh-keys", json=body)
    except client.APIError as e:
        raise _bail(e) from e

    rprint(
        f"[green]Clé ajoutée[/green] : {key['name']} ({key['fingerprint']}) "
        f"[[{_SCOPE_STYLE.get(key.get('scope', scope_norm), 'white')}]"
        f"{key.get('scope', scope_norm)}[/]]"
    )


@app.command(name="list")
def list_keys() -> None:
    """Liste les clés SSH visibles (user + org + tenant selon vos droits)."""
    try:
        keys = client.get("/v1/ssh-keys")
    except client.APIError as e:
        raise _bail(e) from e

    fmt = config.get_output()

    if fmt == "json":
        import json
        rprint(json.dumps(keys, ensure_ascii=False, indent=2))
        return

    if not keys:
        rprint("[dim]Aucune clé SSH enregistrée.[/dim]")
        return

    table = Table(title="Clés SSH")
    table.add_column("ID", style="dim", overflow="fold", no_wrap=False)
    table.add_column("Nom", style="cyan")
    table.add_column("Fingerprint", style="white")
    table.add_column("Scope", no_wrap=True)
    table.add_column("Ajoutée le", style="dim")

    for k in keys:
        scope_val = k.get("scope") or "user"
        style = _SCOPE_STYLE.get(scope_val, "white")
        table.add_row(
            k["id"],
            k["name"],
            k["fingerprint"],
            f"[{style}]{scope_val}[/{style}]",
            k["created_at"][:10],
        )

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
        raise _bail(e) from e

    rprint("[green]Clé supprimée.[/green]")
