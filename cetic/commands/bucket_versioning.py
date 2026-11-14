"""cetic bucket versioning — versionnage d'un bucket (cetic-cloud-platform#1923).

Le versionnage est géré par la plateforme : `show` lit, `enable` active (ou
reprend), `suspend` suspend, `retention` change la durée de conservation des
versions anciennes. On ne désactive jamais : S3 ne revient pas à « jamais
versionné », un bucket versionné se suspend.

La durée n'est envoyée que par `enable --noncurrent-days` et `retention` ;
absente, la plateforme garde la valeur en place (30 jours sinon). La raccourcir
efface plus tôt les versions anciennes : la commande le fait confirmer.
"""
from __future__ import annotations

from typing import Any, NoReturn

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_one

versioning_app = typer.Typer(help="Versionnage d'un bucket (suspendre, jamais désactiver)")

MIN_DAYS = 1
MAX_DAYS = 3650

ETATS = {"Enabled": "Activé", "Suspended": "Suspendu", None: "Non activé"}
SYNCHRO = {
    "none": "—",
    "unmanaged": "Posé hors plateforme",
    "pending": "En attente",
    "applied": "Appliqué",
    "error": "Non appliqué",
}


def _fail(e: client.APIError) -> NoReturn:
    rprint(f"[red]Erreur : {e.detail}[/red]")
    raise typer.Exit(1)


def _lire(bucket_id: str) -> dict[str, Any]:
    try:
        return client.get(f"/v1/buckets/{bucket_id}/versioning")
    except client.APIError as e:
        _fail(e)


def _afficher(cfg: dict[str, Any]) -> None:
    days = cfg.get("noncurrent_version_expiration_days")
    render_one(
        {
            "Versionnage": ETATS.get(cfg.get("status"), cfg.get("status")),
            "Conservation des versions anciennes": f"{days} jours" if days else "—",
            "Synchronisation": SYNCHRO.get(cfg.get("sync_status"), cfg.get("sync_status")),
        },
        title="Versionnage",
    )
    if cfg.get("error_message"):
        rprint(f"[yellow]⚠ {cfg['error_message']}[/yellow]")
    if cfg.get("sync_status") == "unmanaged":
        rprint("[dim]Versionnage posé hors de la plateforme : un premier `enable` ou `suspend` le prend en charge.[/dim]")
    elif cfg.get("sync_status") == "pending":
        rprint("[dim]Application en cours : `cetic bucket versioning show` pour suivre.[/dim]")


def _confirmer_raccourci(courant: dict[str, Any], days: int, yes: bool) -> None:
    """Raccourcir la durée efface plus tôt les versions anciennes : on le fait confirmer."""
    avant = courant.get("noncurrent_version_expiration_days")
    if yes or not avant or days >= avant:
        return
    if not typer.confirm(
        f"Raccourcir la conservation de {avant} à {days} jours ? "
        f"Les versions anciennes de plus de {days} jours seront effacées."
    ):
        raise typer.Abort()


def _ecrire(bucket_id: str, status: str, days: int | None) -> dict[str, Any]:
    body: dict[str, Any] = {"status": status}
    if days is not None:
        body["noncurrent_version_expiration_days"] = days
    try:
        return client.put(f"/v1/buckets/{bucket_id}/versioning", json=body)
    except client.APIError as e:
        rprint(f"[red]{e.detail}[/red]")
        if e.status_code == 502:
            # Le réglage est enregistré : la plateforme réessaie d'elle-même.
            rprint("[dim]La plateforme réessaie d'elle-même : `cetic bucket versioning show` pour suivre.[/dim]")
        raise typer.Exit(1)


@versioning_app.command("show")
def show(bucket_id: str = typer.Argument(..., help="UUID du bucket")) -> None:
    """Affiche l'état du versionnage du bucket."""
    _afficher(_lire(bucket_id))


@versioning_app.command("enable")
def enable(
    bucket_id: str = typer.Argument(..., help="UUID du bucket"),
    noncurrent_days: int | None = typer.Option(
        None, "--noncurrent-days", "-d", min=MIN_DAYS, max=MAX_DAYS,
        help="Conservation des versions anciennes, en jours (défaut : valeur en place, sinon 30)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ne demande pas de confirmer une durée raccourcie"),
) -> None:
    """Active le versionnage (ou le reprend s'il est suspendu)."""
    if noncurrent_days is not None:
        _confirmer_raccourci(_lire(bucket_id), noncurrent_days, yes)
    cfg = _ecrire(bucket_id, "Enabled", noncurrent_days)
    rprint("[green]✓[/green] Versionnage activé.")
    _afficher(cfg)


@versioning_app.command("suspend")
def suspend(bucket_id: str = typer.Argument(..., help="UUID du bucket")) -> None:
    """Suspend le versionnage : plus de nouvelles versions, les anciennes restent.

    La durée de conservation n'est pas modifiée.
    """
    cfg = _ecrire(bucket_id, "Suspended", None)
    rprint("[green]✓[/green] Versionnage suspendu.")
    _afficher(cfg)


@versioning_app.command("retention")
def retention(
    bucket_id: str = typer.Argument(..., help="UUID du bucket"),
    days: int = typer.Option(
        ..., "--days", "-d", min=MIN_DAYS, max=MAX_DAYS,
        help="Conservation des versions anciennes, en jours",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ne demande pas de confirmer une durée raccourcie"),
) -> None:
    """Change la durée de conservation des versions anciennes, sans changer l'état.

    Un bucket suspendu reste suspendu.
    """
    courant = _lire(bucket_id)
    status = courant.get("status")
    if status not in ("Enabled", "Suspended"):
        rprint("[red]Ce bucket n'a jamais été versionné : activez-le avec "
               "`cetic bucket versioning enable --noncurrent-days N`.[/red]")
        raise typer.Exit(1)
    _confirmer_raccourci(courant, days, yes)
    cfg = _ecrire(bucket_id, status, days)
    rprint(f"[green]✓[/green] Conservation des versions anciennes : {days} jours.")
    _afficher(cfg)
