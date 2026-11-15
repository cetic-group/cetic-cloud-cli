"""cetic bucket versioning — versionnage d'un bucket (cetic-cloud-platform#1923).

Le versionnage est géré par la plateforme : `show` lit, `enable` active (ou
reprend), `suspend` suspend, `retention` change la durée de conservation des
versions anciennes. On ne désactive jamais : S3 ne revient pas à « jamais
versionné », un bucket versionné se suspend.

La durée n'est envoyée que par `enable --noncurrent-days` et `retention` ;
absente, la plateforme garde la valeur en place (30 jours sinon).

Deux cas effacent des versions anciennes, et la commande les fait confirmer
(`--yes` pour passer) : raccourcir la durée, et prendre en charge un
versionnage posé hors de la plateforme — souvent sans expiration, il reçoit
alors la durée demandée (30 jours sinon).

En sortie `json` / `yaml`, la réponse de l'API est rendue telle quelle et rien
d'autre n'est écrit sur stdout : un script doit pouvoir la lire.
"""
from __future__ import annotations

from typing import Any, NoReturn

import typer
from rich import print as rprint

from cetic import client, config
from cetic.commands._render import render_one

versioning_app = typer.Typer(help="Versionnage d'un bucket (suspendre, jamais désactiver)")

MIN_DAYS = 1
MAX_DAYS = 3650
DEFAULT_DAYS = 30

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


def _structure() -> bool:
    return config.get_output() in ("json", "yaml")


def _dire(message: str) -> None:
    """Message pour un humain : jamais en sortie json/yaml, stdout doit rester lisible par un script."""
    if not _structure():
        rprint(message)


def _lire(bucket_id: str) -> dict[str, Any]:
    try:
        return client.get(f"/v1/buckets/{bucket_id}/versioning")
    except client.APIError as e:
        _fail(e)


def _afficher(cfg: dict[str, Any]) -> None:
    if _structure():
        # Charge de l'API telle quelle : clés et valeurs stables pour les scripts.
        render_one(cfg, title="")
        return
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
        rprint(f"[dim]Versionnage posé hors de la plateforme. Sa prise en charge (premier `enable` ou "
               f"`suspend`) pose une conservation des versions anciennes ({DEFAULT_DAYS} jours par défaut) "
               f"et efface les plus anciennes.[/dim]")
    elif cfg.get("sync_status") == "pending":
        rprint("[dim]Application en cours : `cetic bucket versioning show` pour suivre.[/dim]")


def _confirmer(courant: dict[str, Any], days: int | None, yes: bool) -> None:
    """Fait confirmer les deux cas qui effacent des versions anciennes."""
    if yes:
        return
    if courant.get("sync_status") == "unmanaged":
        # Posé hors plateforme : en général sans expiration, les versions anciennes
        # sont gardées sans limite. La prise en charge pose une durée.
        pose = days if days is not None else DEFAULT_DAYS
        question = (
            "Ce versionnage a été posé hors de la plateforme : ses versions anciennes sont peut-être "
            f"gardées sans limite. Sa prise en charge pose une conservation de {pose} jours, et les "
            f"versions anciennes de plus de {pose} jours seront effacées. Continuer ?"
        )
    else:
        avant = courant.get("noncurrent_version_expiration_days")
        if days is None or not avant or days >= avant:
            return
        question = (
            f"Raccourcir la conservation de {avant} à {days} jours ? "
            f"Les versions anciennes de plus de {days} jours seront effacées."
        )
    # Sur stderr : la sortie json/yaml reste lisible même si la question est posée.
    if not typer.confirm(question, err=True):
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
            _apres_502(bucket_id)
        raise typer.Exit(1)


def _apres_502(bucket_id: str) -> None:
    """Un 502 a plusieurs causes : réglage enregistré mais pas appliqué (la
    plateforme réessaie), rien d'enregistré (état du stockage illisible), ou
    passerelle en échec avant ou après l'enregistrement. Seul le premier cas se
    reconnaît à l'état relu ; dans les autres, on ne sait pas : on le dit, et
    relancer reste sans risque (l'écriture est idempotente)."""
    try:
        cfg = client.get(f"/v1/buckets/{bucket_id}/versioning")
    except client.APIError:
        cfg = {}
    if cfg.get("sync_status") == "error":
        rprint("[dim]Le réglage est enregistré mais pas encore appliqué : la plateforme réessaie "
               "d'elle-même. `cetic bucket versioning show` pour suivre.[/dim]")
    else:
        rprint("[dim]Le réglage n'est pas confirmé : vérifiez avec `cetic bucket versioning show` "
               "avant de relancer.[/dim]")


@versioning_app.command("show")
def show(bucket_id: str = typer.Argument(..., help="UUID du bucket")) -> None:
    """Affiche l'état du versionnage du bucket."""
    _afficher(_lire(bucket_id))


OPTION_YES = "Ne demande pas de confirmer une durée raccourcie ou la prise en charge d'un versionnage posé hors plateforme"


@versioning_app.command("enable")
def enable(
    bucket_id: str = typer.Argument(..., help="UUID du bucket"),
    noncurrent_days: int | None = typer.Option(
        None, "--noncurrent-days", "-d", min=MIN_DAYS, max=MAX_DAYS,
        help="Conservation des versions anciennes, en jours (défaut : valeur en place, sinon 30)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help=OPTION_YES),
) -> None:
    """Active le versionnage (ou le reprend s'il est suspendu)."""
    _confirmer(_lire(bucket_id), noncurrent_days, yes)
    cfg = _ecrire(bucket_id, "Enabled", noncurrent_days)
    _dire("[green]✓[/green] Versionnage activé.")
    _afficher(cfg)


@versioning_app.command("suspend")
def suspend(
    bucket_id: str = typer.Argument(..., help="UUID du bucket"),
    yes: bool = typer.Option(False, "--yes", "-y", help=OPTION_YES),
) -> None:
    """Suspend le versionnage : plus de nouvelles versions, les anciennes restent.

    La durée de conservation n'est pas modifiée.
    """
    _confirmer(_lire(bucket_id), None, yes)
    cfg = _ecrire(bucket_id, "Suspended", None)
    _dire("[green]✓[/green] Versionnage suspendu.")
    _afficher(cfg)


@versioning_app.command("retention")
def retention(
    bucket_id: str = typer.Argument(..., help="UUID du bucket"),
    days: int = typer.Option(
        ..., "--days", "-d", min=MIN_DAYS, max=MAX_DAYS,
        help="Conservation des versions anciennes, en jours",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help=OPTION_YES),
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
    _confirmer(courant, days, yes)
    cfg = _ecrire(bucket_id, status, days)
    _dire(f"[green]✓[/green] Conservation des versions anciennes : {days} jours.")
    _afficher(cfg)
