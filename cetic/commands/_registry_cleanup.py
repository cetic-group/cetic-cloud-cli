"""`cetic registry cleanup` — nettoyage planifié des images CCR
(cetic-cloud-platform#1984).

Une planification (4 au plus par registry) désigne des images par motifs de
dépôt × motifs de tag et un critère (`all`, `older_than_days`,
`keep_last_n`). Ses passages et ses condamnations sont ceux de la rétention :
`…/retention/runs` et `…/retention/deletions` (corbeille, délai de grâce de
la registry), filtrés par `origine`.

Toute la validation (motifs, cron, garde-fou des motifs universels, limite de
4, cohérence `critere`/`valeur`, simulation exigée avant `run`) est faite par
l'API : la CLI n'en duplique aucune et affiche ses messages d'erreur TELS
QUELS (jamais de reformulation qui pourrait affirmer autre chose que l'API).

Il n'existe pas de `GET` d'une planification seule : `get` et la résolution
par nom lisent la liste.
"""

from __future__ import annotations

from typing import Any, NoReturn

import typer
from rich import print as rprint

from cetic import client, config
from cetic._resolve import resolve_id
from cetic.commands._render import render_list, render_one

REGISTRIES_PATH = "/v1/registries"
ORIGINES = ("retention", "nettoyage", "tout")

cleanup_app = typer.Typer(
    help="Nettoyage planifié des images (planifications, passages, corbeille)"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bail(e: client.APIError) -> NoReturn:
    """Message de l'API affiché tel quel, code HTTP en tête."""
    rprint(f"[red]Erreur {e.status_code} : {e.detail or 'réponse sans détail'}[/red]")
    raise typer.Exit(1)


def _fail(message: str) -> NoReturn:
    rprint(f"[red]Erreur : {message}[/red]")
    raise typer.Exit(1)


def _machine() -> bool:
    return config.get_output() in ("json", "yaml")


def _resolve_registry(id_or_name: str) -> str:
    return resolve_id(REGISTRIES_PATH, id_or_name)


def _schedules_path(rid: str) -> str:
    return f"{REGISTRIES_PATH}/{rid}/cleanup/schedules"


def _list_schedules(rid: str) -> list[dict[str, Any]]:
    try:
        return client.get(_schedules_path(rid))
    except client.APIError as e:
        _bail(e)


def _find_schedule(rid: str, ref: str) -> dict[str, Any]:
    """Planification désignée par son UUID ou son nom (lecture de la liste,
    4 planifications au plus). L'id est cherché d'abord, puis le nom : l'API
    accepte un NOM au format UUID, qui ne doit pas devenir introuvable."""
    items = _list_schedules(rid)
    matches = [s for s in items if str(s.get("id")) == ref] or \
        [s for s in items if str(s.get("name")) == ref]
    if not matches:
        _fail(f"Aucune planification de nettoyage « {ref} » sur cette registry.")
    return matches[0]


def _resolve_schedule(rid: str, ref: str) -> str:
    return _find_schedule(rid, ref)["id"]


def _critere_label(s: dict[str, Any]) -> str:
    critere, valeur = s.get("critere"), s.get("valeur")
    if critere == "all":
        return "tout ce qui correspond"
    if critere == "older_than_days":
        return f"plus ancien que {valeur} j"
    if critere == "keep_last_n":
        return f"garder les {valeur} plus récents"
    return str(critere)


def _dt(value: Any) -> str:
    from cetic.commands.registry import _short_dt

    return _short_dt(value)


def _size(value: Any) -> str:
    from cetic.commands.registry import _human_size

    return _human_size(value)


def _print_schedule(s: dict[str, Any], *, title: str) -> None:
    if _machine():
        render_one(s, title=title)
        return
    render_one(
        {
            "id": s.get("id"),
            "slot": s.get("slot"),
            "name": s.get("name"),
            "repo_patterns": s.get("repo_patterns") or [],
            "tag_patterns": s.get("tag_patterns") or [],
            "critere": _critere_label(s),
            "cron": s.get("cron") or "aucun (à la demande seulement)",
            "enabled": "oui" if s.get("enabled") else "non",
            "last_dispatched_at": _dt(s.get("last_dispatched_at")),
            "created_at": _dt(s.get("created_at")),
            "updated_at": _dt(s.get("updated_at")),
        },
        title=title,
    )


def _check_origine(origine: str) -> None:
    if origine not in ORIGINES:
        _fail(f"--origine attendu parmi {', '.join(ORIGINES)}.")


# ---------------------------------------------------------------------------
# Planifications
# ---------------------------------------------------------------------------


@cleanup_app.command(name="list")
def cleanup_list(registry: str = typer.Argument(..., metavar="REGISTRY")) -> None:
    """Liste les planifications de nettoyage d'une registry (4 au plus)."""
    rid = _resolve_registry(registry)
    items = _list_schedules(rid)
    if _machine():
        render_list(items, title="Planifications", columns=[])
        return
    rows = [
        {
            "id": s.get("id"),
            "slot": s.get("slot"),
            "name": s.get("name"),
            "repos": ", ".join(s.get("repo_patterns") or []),
            "tags": ", ".join(s.get("tag_patterns") or []),
            "critere": _critere_label(s),
            "cron": s.get("cron") or "à la demande",
            "enabled": "oui" if s.get("enabled") else "non",
            "last": _dt(s.get("last_dispatched_at")),
        }
        for s in items
    ]
    render_list(
        rows,
        title=f"Planifications de nettoyage ({len(rows)})",
        columns=[
            ("id", "ID"), ("slot", "Créneau"), ("name", "Nom"), ("repos", "Dépôts"),
            ("tags", "Tags"), ("critere", "Critère"), ("cron", "Cron (UTC)"),
            ("enabled", "Activée"), ("last", "Dernier envoi"),
        ],
    )


@cleanup_app.command(name="get")
def cleanup_get(
    registry: str = typer.Argument(..., metavar="REGISTRY"),
    schedule: str = typer.Argument(..., metavar="ID|NAME", help="Planification"),
) -> None:
    """Détail d'une planification."""
    rid = _resolve_registry(registry)
    s = _find_schedule(rid, schedule)
    _print_schedule(s, title=f"Planification {s.get('name')}")


@cleanup_app.command(name="create")
def cleanup_create(
    registry: str = typer.Argument(..., metavar="REGISTRY"),
    name: str = typer.Option(..., "--name", "-n", help="Nom (unique dans la registry)"),
    repos: list[str] = typer.Option(
        ..., "--repo", help="Motif de dépôt, jokers * ? (répéter, 10 au plus)"
    ),
    tags: list[str] = typer.Option(
        ..., "--tag", help="Motif de tag, jokers * ? (répéter, 10 au plus)"
    ),
    critere: str = typer.Option(
        ..., "--critere", help="all | older_than_days | keep_last_n"
    ),
    valeur: int | None = typer.Option(
        None, "--valeur",
        help="Jours (older_than_days) ou nombre d'images gardées (keep_last_n) ; absent pour all",
    ),
    cron: str | None = typer.Option(
        None, "--cron",
        help="Cron 5 champs en UTC ; absent : exécution à la demande seulement",
    ),
    enabled: bool = typer.Option(True, "--enabled/--disabled", help="État initial"),
) -> None:
    """Crée une planification de nettoyage.

    ⚠️ Une exécution planifiée (cron) condamne les images visées SANS
    simulation préalable : simulez la planification avant de lui donner un
    cron. Les images condamnées vont dans la corbeille (délai de grâce de la
    registry) et restent restaurables d'ici là.
    """
    body: dict[str, Any] = {
        "name": name,
        "repo_patterns": list(repos),
        "tag_patterns": list(tags),
        "critere": critere,
        "enabled": enabled,
    }
    if valeur is not None:
        body["valeur"] = valeur
    if cron is not None:
        body["cron"] = cron
    rid = _resolve_registry(registry)
    try:
        s = client.post(_schedules_path(rid), json=body)
    except client.APIError as e:
        _bail(e)
    if not _machine():
        rprint(f"[green]✓[/green] Planification créée : [bold]{s.get('name')}[/bold] "
               f"(créneau {s.get('slot')})")
    _print_schedule(s, title=f"Planification {s.get('name')}")


@cleanup_app.command(name="update")
def cleanup_update(
    registry: str = typer.Argument(..., metavar="REGISTRY"),
    schedule: str = typer.Argument(..., metavar="ID|NAME", help="Planification"),
    name: str | None = typer.Option(None, "--name", "-n", help="Nouveau nom"),
    repos: list[str] | None = typer.Option(
        None, "--repo", help="Motif de dépôt (répéter) — REMPLACE la liste"
    ),
    tags: list[str] | None = typer.Option(
        None, "--tag", help="Motif de tag (répéter) — REMPLACE la liste"
    ),
    critere: str | None = typer.Option(
        None, "--critere", help="all | older_than_days | keep_last_n"
    ),
    valeur: int | None = typer.Option(None, "--valeur", help="Nouvelle valeur du critère"),
    cron: str | None = typer.Option(None, "--cron", help="Nouveau cron (5 champs, UTC)"),
    no_cron: bool = typer.Option(
        False, "--no-cron", help="Retire le cron : exécution à la demande seulement"
    ),
    enabled: bool | None = typer.Option(None, "--enabled/--disabled", help="Active/désactive"),
) -> None:
    """Modifie une planification (seuls les champs fournis sont envoyés).

    Changer `--critere` (hors `all`) sans `--valeur` est refusé par l'API.
    Après un changement de motifs, de critère ou de valeur, `run` exige une
    nouvelle simulation.
    """
    if cron is not None and no_cron:
        _fail("--cron et --no-cron sont incompatibles.")
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if repos:
        body["repo_patterns"] = list(repos)
    if tags:
        body["tag_patterns"] = list(tags)
    if critere is not None:
        body["critere"] = critere
    if valeur is not None:
        body["valeur"] = valeur
    if no_cron:
        body["cron"] = None
    elif cron is not None:
        body["cron"] = cron
    if enabled is not None:
        body["enabled"] = enabled
    if not body:
        _fail("Aucune modification demandée.")
    rid = _resolve_registry(registry)
    sid = _resolve_schedule(rid, schedule)
    try:
        s = client.patch(f"{_schedules_path(rid)}/{sid}", json=body)
    except client.APIError as e:
        _bail(e)
    if not _machine():
        rprint(f"[green]✓[/green] Planification mise à jour : [bold]{s.get('name')}[/bold]")
    _print_schedule(s, title=f"Planification {s.get('name')}")


@cleanup_app.command(name="delete")
def cleanup_delete(
    registry: str = typer.Argument(..., metavar="REGISTRY"),
    schedule: str = typer.Argument(..., metavar="ID|NAME", help="Planification"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime une planification. Ses passages et la corbeille sont conservés :
    les images en attente dans la corbeille seront quand même supprimées à
    l'échéance du délai de grâce (les restaurer avant), et les exclusions des
    images restaurées par cette planification ne s'appliquent plus."""
    rid = _resolve_registry(registry)
    sid = _resolve_schedule(rid, schedule)
    if not yes and not typer.confirm(f"Supprimer la planification {schedule} ?"):
        raise typer.Abort()
    try:
        client.delete(f"{_schedules_path(rid)}/{sid}")
    except client.APIError as e:
        _bail(e)
    if not _machine():
        rprint("[green]✓[/green] Planification supprimée (historique et corbeille conservés).")
        rprint("[yellow]![/yellow] Les images encore en attente dans la corbeille seront "
               "supprimées à l'échéance du délai de grâce : restaurez-les avant "
               f"(`cetic registry cleanup trash {registry} --en-attente`).")


def _dispatch(registry: str, schedule: str, action: str) -> None:
    rid = _resolve_registry(registry)
    sid = _resolve_schedule(rid, schedule)
    try:
        out = client.post(f"{_schedules_path(rid)}/{sid}/{action}")
    except client.APIError as e:
        _bail(e)
    if _machine():
        render_one(out or {}, title=action)
        return
    libelle = "Simulation" if action == "simulate" else "Exécution"
    rprint(f"[green]✓[/green] {libelle} demandée (statut {out.get('status')}, "
           f"tâche {out.get('task_id')}).")
    rprint(f"  Résultat : [bold]cetic registry cleanup runs {registry} "
           f"--schedule {schedule}[/bold]")


@cleanup_app.command(name="simulate")
def cleanup_simulate(
    registry: str = typer.Argument(..., metavar="REGISTRY"),
    schedule: str = typer.Argument(..., metavar="ID|NAME", help="Planification"),
) -> None:
    """Simule la planification : compte les images visées, ne condamne rien."""
    _dispatch(registry, schedule, "simulate")


@cleanup_app.command(name="run")
def cleanup_run(
    registry: str = typer.Argument(..., metavar="REGISTRY"),
    schedule: str = typer.Argument(..., metavar="ID|NAME", help="Planification"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Exécute maintenant : les images visées vont dans la corbeille.

    L'API exige une simulation réussie de cette planification sur ses règles
    actuelles (motifs, critère, règles de conservation de la registry) et une
    planification activée (409 sinon).
    """
    if not yes and not typer.confirm(
        f"Exécuter la planification {schedule} ? Les images visées iront dans la corbeille."
    ):
        raise typer.Abort()
    _dispatch(registry, schedule, "run")


# ---------------------------------------------------------------------------
# Passages et corbeille (`…/retention/runs`, `…/retention/deletions`)
# ---------------------------------------------------------------------------


@cleanup_app.command(name="runs")
def cleanup_runs(
    registry: str = typer.Argument(..., metavar="REGISTRY"),
    schedule: str | None = typer.Option(
        None, "--schedule", "-s", metavar="ID|NAME",
        help="Passages d'une seule planification (ignore --origine)",
    ),
    origine: str = typer.Option(
        "nettoyage", "--origine",
        help="nettoyage (défaut ici) | retention | tout",
    ),
    mode: str | None = typer.Option(None, "--mode", help="simulation | application"),
    statut: str | None = typer.Option(None, "--statut", help="Filtre sur le statut"),
    limit: int = typer.Option(50, "--limit", help="200 au plus"),
    offset: int = typer.Option(0, "--offset"),
) -> None:
    """Passages (simulations, exécutions), du plus récent au plus ancien.

    `--origine` est toujours envoyé : sans lui, l'API ne rend que les
    passages des règles de rétention (`retention`).
    """
    _check_origine(origine)
    rid = _resolve_registry(registry)
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if schedule is not None:
        params["schedule_id"] = _resolve_schedule(rid, schedule)
    else:
        params["origine"] = origine
    if mode is not None:
        params["mode"] = mode
    if statut is not None:
        params["statut"] = statut
    try:
        items = client.get(f"{REGISTRIES_PATH}/{rid}/retention/runs", params=params)
    except client.APIError as e:
        _bail(e)
    if _machine():
        render_list(items, title="Passages", columns=[])
        return
    rows = [
        {
            "id": r.get("id"),
            "started": _dt(r.get("started_at")),
            "source": r.get("schedule_name") or r.get("origine"),
            "mode": r.get("mode"),
            "statut": r.get("statut"),
            "par": r.get("declenche_par"),
            "evalues": r.get("tags_evalues"),
            "selectionnes": r.get("tags_selectionnes"),
            "octets": _size(r.get("octets_liberables")),
            "erreur": r.get("message_erreur"),
        }
        for r in items
    ]
    render_list(
        rows,
        title=f"Passages ({len(rows)})",
        columns=[
            ("id", "ID"), ("started", "Début"), ("source", "Planification / origine"),
            ("mode", "Mode"), ("statut", "Statut"), ("par", "Déclenché par"),
            ("evalues", "Tags évalués"), ("selectionnes", "Tags visés"),
            ("octets", "Libérable"), ("erreur", "Erreur"),
        ],
    )


def _etat_condamnation(d: dict[str, Any]) -> str:
    if d.get("purged_at"):
        return f"supprimée le {_dt(d['purged_at'])}"
    if d.get("cancelled_at"):
        if d.get("exclusion_active"):
            return "restaurée (exclusion active)"
        if d.get("exclusion_levee_at"):
            return "restaurée (exclusion levée)"
        return "restaurée"
    return f"en attente (suppression après {_dt(d.get('purge_after'))})"


@cleanup_app.command(name="trash")
def cleanup_trash(
    registry: str = typer.Argument(..., metavar="REGISTRY"),
    origine: str = typer.Option(
        "nettoyage", "--origine",
        help="nettoyage (défaut ici) | retention | tout",
    ),
    en_attente: bool = typer.Option(
        False, "--en-attente", help="Seulement les images ni supprimées ni restaurées"
    ),
    limit: int = typer.Option(100, "--limit", help="500 au plus"),
    offset: int = typer.Option(0, "--offset"),
) -> None:
    """Corbeille : images condamnées, plus récentes d'abord.

    `--origine` est toujours envoyé : sans lui, l'API ne rend que les
    condamnations des règles de rétention (`retention`).
    """
    _check_origine(origine)
    rid = _resolve_registry(registry)
    params: dict[str, Any] = {"origine": origine, "limit": limit, "offset": offset}
    if en_attente:
        params["en_attente"] = "true"
    try:
        items = client.get(f"{REGISTRIES_PATH}/{rid}/retention/deletions", params=params)
    except client.APIError as e:
        _bail(e)
    if _machine():
        render_list(items, title="Corbeille", columns=[])
        return
    rows = [
        {
            "id": d.get("id"),
            "image": f"{d.get('repo')}:{d.get('tag')}",
            "digest": (d.get("manifest_digest") or "—")[:19],
            "taille": _size(d.get("taille")),
            "source": d.get("schedule_name") or d.get("origine"),
            "raison": d.get("raison"),
            "etat": _etat_condamnation(d),
        }
        for d in items
    ]
    render_list(
        rows,
        title=f"Corbeille ({len(rows)})",
        columns=[
            ("id", "ID"), ("image", "Image"), ("digest", "Digest"), ("taille", "Taille"),
            ("source", "Planification / origine"), ("raison", "Raison"), ("etat", "État"),
        ],
    )


@cleanup_app.command(name="restore")
def cleanup_restore(
    registry: str = typer.Argument(..., metavar="REGISTRY"),
    deletion_id: str = typer.Argument(..., metavar="DELETION_ID", help="Ligne de la corbeille"),
) -> None:
    """Restaure une image de la corbeille tant qu'elle n'est pas supprimée."""
    rid = _resolve_registry(registry)
    try:
        out = client.post(
            f"{REGISTRIES_PATH}/{rid}/retention/deletions/{deletion_id}/cancel"
        )
    except client.APIError as e:
        _bail(e)
    if _machine():
        render_one(out or {}, title="Restauration")
        return
    rprint(f"[green]✓[/green] Image restaurée ({_dt((out or {}).get('cancelled_at'))}).")


@cleanup_app.command(name="lift-exclusion")
def cleanup_lift_exclusion(
    registry: str = typer.Argument(..., metavar="REGISTRY"),
    deletion_id: str = typer.Argument(
        ..., metavar="DELETION_ID", help="Ligne restaurée de la corbeille"
    ),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Lève l'exclusion d'une image restaurée (de nouveau condamnable).

    Le mécanisme qui l'avait condamnée (sa planification, ou les règles de
    rétention) pourra de nouveau la condamner à son prochain passage, dans la
    corbeille avec le délai de grâce.
    """
    rid = _resolve_registry(registry)
    if not yes and not typer.confirm(
        "Lever l'exclusion ? L'image pourra de nouveau être condamnée au prochain passage."
    ):
        raise typer.Abort()
    try:
        out = client.delete(
            f"{REGISTRIES_PATH}/{rid}/retention/deletions/{deletion_id}/exclusion"
        )
    except client.APIError as e:
        _bail(e)
    if _machine():
        render_one(out or {}, title="Exclusion")
        return
    out = out or {}
    rprint(f"[green]✓[/green] Exclusion levée pour {out.get('repo')}:{out.get('tag')} "
           f"(exclusion_active={str(out.get('exclusion_active')).lower()}).")
