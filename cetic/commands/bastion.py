"""cetic bastion — Bastion SSH CETIC Cloud (accès SSH sécurisé).

Le Bastion CETIC Cloud fournit un point d'entrée SSH unique et auditable vers
vos ressources privées (VM, instances), sans exposer celles-ci sur Internet.
L'accès repose sur des certificats SSH éphémères signés à la demande par
l'autorité de certification (CA) de la plateforme : aucune clé statique à
déployer, révocation immédiate via KRL.

Sous-commandes :
    cetic bastion list
    cetic bastion get ID
    cetic bastion create --name N --region R --vpc VPC_ID
    cetic bastion delete ID [--yes]
    cetic bastion ca [--kind user|host]
    cetic bastion revoke [--serial N] [--key-id K] [--reason R]
    cetic bastion krl

Pour ouvrir une session interactive vers une cible privée, utilisez la
commande de premier niveau `cetic ssh <CIBLE>`.
"""
from __future__ import annotations

from typing import Any

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one


BASTION_PATH = "/v1/bastions"

VALID_CA_KINDS = ("user", "host")


app = typer.Typer(help="Bastion SSH CETIC Cloud (accès SSH sécurisé)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_api_error(e: client.APIError) -> str:
    if e.status_code == 401:
        return "Non authentifié — vérifiez `cetic auth login` ou `CCP_API_KEY`."
    if e.status_code == 403:
        return "Accès refusé — droits insuffisants pour gérer le bastion."
    if e.status_code == 404:
        return "Bastion introuvable."
    if e.status_code == 409:
        return f"Conflit : {e.detail}"
    if e.status_code == 422:
        return f"Données invalides : {e.detail}"
    if e.status_code == 429:
        return "Trop de requêtes — rate limit dépassé. Réessayez dans quelques secondes."
    if e.status_code >= 500:
        return f"Erreur serveur ({e.status_code}). Réessayez plus tard."
    return e.detail or f"Erreur HTTP {e.status_code}"


def _bail(e: client.APIError) -> typer.Exit:
    rprint(f"[red]Erreur : {_format_api_error(e)}[/red]")
    return typer.Exit(1)


# ---------------------------------------------------------------------------
# Commandes — bastions
# ---------------------------------------------------------------------------


@app.command(name="list")
def list_bastions() -> None:
    """Liste les bastions SSH de l'organisation courante."""
    try:
        items = client.get(BASTION_PATH)
    except client.APIError as e:
        raise _bail(e) from e

    render_list(
        items,
        title=f"Bastions SSH ({len(items)})",
        columns=[
            ("id", "ID"),
            ("name", "Nom"),
            ("region", "Région"),
            ("status", "Statut"),
            ("endpoint_host", "Hôte"),
        ],
    )


@app.command()
def get(bastion_id: str = typer.Argument(..., metavar="ID", help="ID du bastion")) -> None:
    """Détail d'un bastion SSH."""
    try:
        bastion = client.get(f"{BASTION_PATH}/{bastion_id}")
    except client.APIError as e:
        raise _bail(e) from e
    render_one(bastion, title=f"Bastion {bastion.get('name', bastion_id)}")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n", help="Nom du bastion"),
    region: str = typer.Option(..., "--region", "-r", help="Région (ex : RNN, PAR, ABJ)"),
    vpc: str = typer.Option(..., "--vpc", help="ID du VPC où déployer le bastion"),
) -> None:
    """Crée un bastion SSH dans un VPC.

    Le bastion expose un point d'entrée SSH unique et auditable vers les
    ressources privées du VPC. Une fois `running`, ouvrez une session avec
    `cetic ssh <CIBLE> --bastion <hôte>`.
    """
    body: dict[str, Any] = {"name": name, "region": region, "vpc_id": vpc}
    try:
        bastion = client.post(BASTION_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Bastion créé : [bold]{bastion['name']}[/bold] "
        f"([dim]{bastion['id']}[/dim], statut {bastion.get('status', '?')})"
    )
    endpoint = bastion.get("endpoint_host")
    if endpoint:
        rprint(
            f"  Hôte : [cyan]{endpoint}[/cyan]:"
            f"{bastion.get('endpoint_port', 22)}"
        )


@app.command()
def delete(
    bastion_id: str = typer.Argument(..., metavar="ID", help="ID du bastion"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip la confirmation."),
) -> None:
    """Supprime un bastion SSH (irréversible)."""
    if not yes and not typer.confirm(
        f"Supprimer le bastion {bastion_id} ? Cette action est irréversible."
    ):
        raise typer.Abort()
    try:
        client.delete(f"{BASTION_PATH}/{bastion_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Bastion supprimé.")


# ---------------------------------------------------------------------------
# Commandes — CA / révocation
# ---------------------------------------------------------------------------


@app.command()
def ca(
    kind: str = typer.Option(
        "user",
        "--kind",
        help="Type de CA SSH à afficher (user = signe les certificats clients, "
             "host = signe les certificats des hôtes).",
        case_sensitive=False,
    ),
) -> None:
    """Affiche la clé publique de l'autorité de certification SSH.

    La CA `user` signe les certificats clients éphémères (cf. `cetic ssh`) ;
    la CA `host` signe les certificats d'hôtes. Ajoutez la clé `host` à votre
    `known_hosts` (directive `@cert-authority`) pour valider l'identité du
    bastion sans TOFU.
    """
    kind_norm = kind.lower()
    if kind_norm not in VALID_CA_KINDS:
        rprint(
            f"[red]--kind invalide : '{kind}'. "
            f"Valeurs autorisées : {', '.join(VALID_CA_KINDS)}.[/red]"
        )
        raise typer.Exit(1)

    try:
        resp = client.get(f"/v1/ssh/ca/{kind_norm}/public")
    except client.APIError as e:
        raise _bail(e) from e

    public_key = resp.get("public_key", "")
    # Sortie brute sur stdout — exploitable directement (>> known_hosts, etc.).
    print(public_key)  # noqa: T201


@app.command()
def revoke(
    serial: int | None = typer.Option(
        None, "--serial", help="Numéro de série du certificat à révoquer."
    ),
    key_id: str | None = typer.Option(
        None, "--key-id", help="Identifiant de clé (key_id) du certificat à révoquer."
    ),
    reason: str | None = typer.Option(
        None, "--reason", help="Motif de la révocation (journalisé)."
    ),
) -> None:
    """Révoque un certificat SSH (par numéro de série ou par key_id).

    Le certificat est ajouté à la liste de révocation (KRL) de la plateforme
    et refusé immédiatement par le bastion. Fournissez au moins `--serial`
    ou `--key-id`.
    """
    if serial is None and key_id is None:
        rprint("[red]Erreur : fournissez au moins --serial ou --key-id.[/red]")
        raise typer.Exit(1)

    body: dict[str, Any] = {}
    if serial is not None:
        body["serial"] = serial
    if key_id is not None:
        body["key_id"] = key_id
    if reason is not None:
        body["reason"] = reason

    try:
        client.post("/v1/ssh/revoke", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Certificat révoqué (ajouté à la KRL).")


@app.command()
def krl() -> None:
    """Affiche la liste de révocation des certificats SSH (KRL).

    Liste les certificats révoqués par numéro de série et par key_id.
    """
    try:
        resp = client.get("/v1/ssh/krl")
    except client.APIError as e:
        raise _bail(e) from e

    serials = resp.get("serials") or []
    key_ids = resp.get("key_ids") or []

    from cetic import config

    fmt = config.get_output()
    if fmt in ("json", "yaml"):
        # Délègue le rendu structuré au helper (respecte CCP_OUTPUT).
        render_one({"serials": serials, "key_ids": key_ids}, title="KRL")
        return

    if not serials and not key_ids:
        rprint("[dim]Aucun certificat révoqué.[/dim]")
        return

    rows: list[dict[str, Any]] = []
    for s in serials:
        rows.append({"type": "serial", "value": str(s)})
    for k in key_ids:
        rows.append({"type": "key_id", "value": str(k)})
    render_list(
        rows,
        title=f"Certificats révoqués ({len(rows)})",
        columns=[("type", "Type"), ("value", "Valeur")],
    )
