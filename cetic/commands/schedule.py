"""cetic schedule — Planificateur marche/arrêt des ressources CETIC Cloud.

Programme l'arrêt et le redémarrage automatiques de vos ressources selon des
fenêtres hebdomadaires (par exemple : éteindre la nuit et le week-end). Une
ressource planifiée est **arrêtée**, jamais détruite : le stockage est conservé
et la ressource redémarre à l'identique en dehors des fenêtres.

Cibles supportées (une seule par planning, exclusives) :
    --vm ID|NOM               une machine virtuelle
    --container ID|NOM        un container
    --scale-set ID|NOM        un container scale set (tous ses membres)
    --vm-scale-set ID|NOM     un VM scale set (tous ses membres)
    --ccks-node-pool CLUSTER POOL   un pool de nœuds Kubernetes managé
    --db-instance ID          une instance de base de données managée

Fenêtres OFF (option `--off`, répétable) — syntaxe `JOUR:HEURE-JOUR:HEURE` :
    --off "fri:20-mon:08"     éteint du vendredi 20h au lundi 08h (week-end)
    --off "mon:22-tue:07"     éteint chaque nuit 22h → 07h (répéter par jour)

    - JOUR  : mon|tue|wed|thu|fri|sat|sun  (ou 0=lundi … 6=dimanche)
    - HEURE : entier 0..24, aligné à l'heure pleine (pas de minutes)
    - L'intervalle [début → fin) peut enjamber le dimanche→lundi (wrap-around) :
      la ressource est ÉTEINTE pendant la fenêtre, ALLUMÉE en dehors.

Sous-commandes :
    cetic schedule list
    cetic schedule get ID|NOM
    cetic schedule create NOM --vm <id> --off "fri:20-mon:08" [--timezone Europe/Paris]
    cetic schedule update ID|NOM [--name ...] [--off ...] [--timezone ...]
    cetic schedule delete ID|NOM [--yes]
    cetic schedule enable ID|NOM
    cetic schedule disable ID|NOM

Un arrêt de moins d'une heure ou plus de 2 cycles marche/arrêt par jour est
refusé (aucun gain : l'économie est facturée à l'heure).
"""

from __future__ import annotations

import re
from typing import Any

import typer
from rich import print as rprint

from cetic import client
from cetic._resolve import looks_like_uuid, resolve_id
from cetic.commands._render import render_list, render_one

SCHEDULE_PATH = "/v1/schedules"

app = typer.Typer(help="Planificateur marche/arrêt des ressources CETIC Cloud")


# ---------------------------------------------------------------------------
# Jours / fenêtres
# ---------------------------------------------------------------------------

# 0 = lundi … 6 = dimanche (aligné sur le contrat API).
_DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_TO_INT = {name: i for i, name in enumerate(_DAY_NAMES)}
_INT_TO_DAY = {i: name for i, name in enumerate(_DAY_NAMES)}

# JOUR:HEURE-JOUR:HEURE  (JOUR = nom ou 0..6, HEURE = 0..24)
_OFF_RE = re.compile(
    r"^\s*(?P<sd>[a-zA-Z]+|\d)\s*:\s*(?P<sh>\d{1,2})\s*-\s*"
    r"(?P<ed>[a-zA-Z]+|\d)\s*:\s*(?P<eh>\d{1,2})\s*$"
)


def _parse_day(token: str) -> int:
    """Convertit un jour (`mon`..`sun` ou `0`..`6`) en entier 0=lundi..6=dimanche."""
    tok = token.strip().lower()
    if tok in _DAY_TO_INT:
        return _DAY_TO_INT[tok]
    if tok.isdigit():
        val = int(tok)
        if 0 <= val <= 6:
            return val
    raise typer.BadParameter(
        f"Jour invalide : {token!r}. Attendu mon|tue|wed|thu|fri|sat|sun ou 0..6 "
        "(0=lundi)."
    )


def _parse_hour(token: str) -> int:
    if not token.isdigit():
        raise typer.BadParameter(f"Heure invalide : {token!r}. Attendu un entier 0..24.")
    val = int(token)
    if not 0 <= val <= 24:
        raise typer.BadParameter(f"Heure hors bornes : {val}. Attendu 0..24 (heure pleine).")
    return val


def _parse_off_window(spec: str) -> dict[str, int]:
    """Parse une fenêtre OFF `JOUR:HEURE-JOUR:HEURE` en dict du contrat.

    Renvoie `{start_day, start_hour, end_day, end_hour}`.
    """
    m = _OFF_RE.match(spec)
    if not m:
        raise typer.BadParameter(
            f"Fenêtre invalide : {spec!r}. Format attendu 'JOUR:HEURE-JOUR:HEURE' "
            "(ex : 'fri:20-mon:08')."
        )
    return {
        "start_day": _parse_day(m.group("sd")),
        "start_hour": _parse_hour(m.group("sh")),
        "end_day": _parse_day(m.group("ed")),
        "end_hour": _parse_hour(m.group("eh")),
    }


def _parse_off_windows(specs: list[str]) -> list[dict[str, int]]:
    return [_parse_off_window(s) for s in specs]


def _fmt_window(w: dict[str, Any]) -> str:
    """Rend une fenêtre du contrat en forme lisible `fri:20→mon:08`."""
    sd = _INT_TO_DAY.get(int(w.get("start_day", 0)), str(w.get("start_day")))
    ed = _INT_TO_DAY.get(int(w.get("end_day", 0)), str(w.get("end_day")))
    return f"{sd}:{int(w.get('start_hour', 0)):02d}→{ed}:{int(w.get('end_hour', 0)):02d}"


def _fmt_windows(windows: list[dict[str, Any]] | None) -> str:
    if not windows:
        return "—"
    return ", ".join(_fmt_window(w) for w in windows)


# ---------------------------------------------------------------------------
# Cible polymorphe
# ---------------------------------------------------------------------------


def _resolve_pool(cluster: str, pool: str) -> str:
    """Résout un node pool CCKS en UUID (par UUID direct ou par nom dans le cluster)."""
    if looks_like_uuid(pool):
        return pool
    cluster_id = resolve_id("/v1/k8s/clusters", cluster)
    try:
        pools = client.get(f"/v1/k8s/clusters/{cluster_id}/node-pools")
    except client.APIError as e:
        raise _bail(e) from e
    matches = [p for p in pools if p.get("name") == pool]
    if not matches:
        rprint(f"[red]Aucun node pool nommé '{pool}' dans le cluster {cluster}.[/red]")
        raise typer.Exit(1)
    if len(matches) > 1:
        rprint(
            f"[red]Plusieurs node pools nommés '{pool}' ({len(matches)}). "
            "Utilisez l'UUID.[/red]"
        )
        raise typer.Exit(1)
    return matches[0]["id"]


def _resolve_target(
    *,
    vm: str | None,
    container: str | None,
    scale_set: str | None,
    vm_scale_set: str | None,
    ccks_node_pool: tuple[str | None, str | None],
    db_instance: str | None,
) -> tuple[str, str]:
    """Mappe les flags de cible exclusifs vers `(resource_type, resource_id)`.

    Exactement une cible doit être fournie.
    """
    cluster, pool = ccks_node_pool
    candidates: list[tuple[str, str]] = []
    if vm is not None:
        candidates.append(("vm", resolve_id("/v1/vm-instances", vm)))
    if container is not None:
        candidates.append(("container", resolve_id("/v1/containers", container)))
    if scale_set is not None:
        candidates.append(
            ("container_scale_set", resolve_id("/v1/container-scale-sets", scale_set))
        )
    if vm_scale_set is not None:
        candidates.append(
            ("vm_scale_set", resolve_id("/v1/vm-scale-sets", vm_scale_set))
        )
    if pool is not None:
        candidates.append(("ccks_node_pool", _resolve_pool(cluster, pool)))
    if db_instance is not None:
        # Les DBaaS sont adressées par UUID (chemin API scindé par moteur).
        candidates.append(("db_instance", db_instance))

    if not candidates:
        rprint(
            "[red]Aucune cible : précisez exactement une de --vm / --container / "
            "--scale-set / --vm-scale-set / --ccks-node-pool / --db-instance.[/red]"
        )
        raise typer.Exit(1)
    if len(candidates) > 1:
        rprint(
            "[red]Cibles multiples : une seule ressource par planning "
            "(flags mutuellement exclusifs).[/red]"
        )
        raise typer.Exit(1)
    return candidates[0]


# ---------------------------------------------------------------------------
# Erreurs API
# ---------------------------------------------------------------------------


def _detail_message(detail: Any) -> str:
    """Extrait le message métier d'un `detail` API (dict {code,message} ou str)."""
    if isinstance(detail, dict):
        return detail.get("message") or detail.get("code") or str(detail)
    return str(detail)


def _format_api_error(e: client.APIError) -> str:
    if e.status_code == 401:
        return "Non authentifié — vérifiez `cetic auth login` ou `CCP_API_KEY`."
    if e.status_code == 403:
        return "Accès refusé — droits insuffisants (action schedules:*)."
    if e.status_code == 404:
        return "Planning ou ressource cible introuvable."
    if e.status_code == 422:
        # Message métier anti-flapping renvoyé tel quel (identique API/CLI/TF).
        return _detail_message(e.detail)
    if e.status_code == 429:
        msg = _detail_message(e.detail)
        return msg or "Quota de plannings atteint. Supprimez un planning ou augmentez le quota."
    if e.status_code >= 500:
        return f"Erreur serveur ({e.status_code}). Réessayez plus tard."
    return _detail_message(e.detail) or f"Erreur HTTP {e.status_code}"


def _bail(e: client.APIError) -> typer.Exit:
    rprint(f"[red]Erreur : {_format_api_error(e)}[/red]")
    return typer.Exit(1)


def _resolve_schedule(id_or_name: str) -> str:
    return resolve_id(SCHEDULE_PATH, id_or_name)


def _fee(cents: Any) -> str:
    if cents is None:
        return "—"
    try:
        return f"{int(cents) / 100:.2f} €/mois"
    except (TypeError, ValueError):
        return "—"


def _state(value: Any) -> str:
    if value == "off":
        return "OFF"
    if value == "on":
        return "ON"
    return "—"


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------


@app.command(name="list")
def list_schedules() -> None:
    """Liste les plannings de l'organisation courante (état ON/OFF + coût estimé)."""
    try:
        items = client.get(SCHEDULE_PATH)
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": s["id"],
            "name": s.get("name"),
            "type": s.get("resource_type"),
            "target": s.get("resource_id"),
            "state": _state(s.get("current_state")),
            "enabled": "oui" if s.get("enabled", True) else "non",
            "windows": _fmt_windows(s.get("windows")),
            "fee": _fee(s.get("estimated_monthly_fee_cents")),
        }
        for s in items
    ]
    render_list(
        rows,
        title=f"Plannings ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("name", "Nom"),
            ("type", "Type"),
            ("target", "Cible"),
            ("state", "État"),
            ("enabled", "Actif"),
            ("windows", "Fenêtres OFF"),
            ("fee", "Coût estimé"),
        ],
    )


@app.command()
def get(id_or_name: str = typer.Argument(..., metavar="ID|NOM")) -> None:
    """Détail d'un planning (état courant ON/OFF, fenêtres, coût estimé)."""
    sid = _resolve_schedule(id_or_name)
    try:
        s = client.get(f"{SCHEDULE_PATH}/{sid}")
    except client.APIError as e:
        raise _bail(e) from e
    view = dict(s)
    view["current_state"] = _state(s.get("current_state"))
    view["windows"] = _fmt_windows(s.get("windows"))
    view["estimated_monthly_fee"] = _fee(s.get("estimated_monthly_fee_cents"))
    render_one(view, title=f"Planning {s.get('name', sid)}")


@app.command()
def create(
    name: str = typer.Argument(..., help="Nom du planning (unique dans l'organisation)."),
    vm: str | None = typer.Option(None, "--vm", help="Cible : machine virtuelle (ID ou nom)."),
    container: str | None = typer.Option(
        None, "--container", help="Cible : container (ID ou nom)."
    ),
    scale_set: str | None = typer.Option(
        None, "--scale-set", help="Cible : container scale set (ID ou nom)."
    ),
    vm_scale_set: str | None = typer.Option(
        None, "--vm-scale-set", help="Cible : VM scale set (ID ou nom)."
    ),
    ccks_node_pool: tuple[str, str] = typer.Option(
        (None, None),
        "--ccks-node-pool",
        metavar="CLUSTER POOL",
        help="Cible : pool de nœuds Kubernetes (cluster puis pool, ID ou nom).",
    ),
    db_instance: str | None = typer.Option(
        None, "--db-instance", help="Cible : instance de base de données managée (UUID)."
    ),
    off: list[str] = typer.Option(
        ...,
        "--off",
        help="Fenêtre OFF hebdomadaire 'JOUR:HEURE-JOUR:HEURE' (répétable, au moins une). "
        "Ex : --off 'fri:20-mon:08'.",
    ),
    timezone: str | None = typer.Option(
        None,
        "--timezone",
        "--tz",
        help="Fuseau IANA (ex : Europe/Paris). Par défaut : fuseau de l'organisation.",
    ),
    disabled: bool = typer.Option(
        False, "--disabled", help="Crée le planning inactif (aucune transition appliquée)."
    ),
) -> None:
    """Crée un planning marche/arrêt pour une ressource.

    La cible est fixée à la création (une seule ressource, flags exclusifs). Les
    fenêtres décrivent les intervalles où la ressource est ÉTEINTE ; elle est
    ALLUMÉE en dehors.

    Exemples :
        cetic schedule create nuit-vm --vm web-01 --off "mon:22-tue:07" \\
            --off "tue:22-wed:07"
        cetic schedule create weekend --vm-scale-set fleet \\
            --off "fri:20-mon:08" --timezone Europe/Paris
        cetic schedule create nuit-pool --ccks-node-pool prod gpu --off "mon:20-tue:08"
    """
    resource_type, resource_id = _resolve_target(
        vm=vm,
        container=container,
        scale_set=scale_set,
        vm_scale_set=vm_scale_set,
        ccks_node_pool=ccks_node_pool,
        db_instance=db_instance,
    )
    windows = _parse_off_windows(off)

    body: dict[str, Any] = {
        "name": name,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "windows": windows,
        "enabled": not disabled,
    }
    if timezone is not None:
        body["timezone"] = timezone

    try:
        s = client.post(SCHEDULE_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Planning créé : [bold]{s.get('name', name)}[/bold] "
        f"([dim]{s['id']}[/dim]) — cible {resource_type} {resource_id}"
    )


@app.command()
def update(
    id_or_name: str = typer.Argument(..., metavar="ID|NOM"),
    name: str | None = typer.Option(None, "--name", "-n", help="Nouveau nom."),
    off: list[str] = typer.Option(
        None,
        "--off",
        help="Remplace TOUTES les fenêtres OFF (répétable). "
        "Sans cet argument, fenêtres inchangées.",
    ),
    timezone: str | None = typer.Option(
        None, "--timezone", "--tz", help="Nouveau fuseau IANA."
    ),
) -> None:
    """Modifie un planning (nom, fenêtres, fuseau).

    Pour (dés)activer un planning, utilisez `cetic schedule enable|disable`. La
    cible d'un planning ne peut pas être changée (créez-en un nouveau).
    """
    sid = _resolve_schedule(id_or_name)
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if off:
        body["windows"] = _parse_off_windows(off)
    if timezone is not None:
        body["timezone"] = timezone
    if not body:
        rprint("[yellow]Rien à modifier (utilisez --name, --off et/ou --timezone).[/yellow]")
        raise typer.Exit(0)
    try:
        s = client.patch(f"{SCHEDULE_PATH}/{sid}", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Planning [bold]{s.get('name', sid)}[/bold] mis à jour.")


@app.command()
def delete(
    id_or_name: str = typer.Argument(..., metavar="ID|NOM"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip la confirmation."),
) -> None:
    """Supprime un planning (la ressource cible est rallumée)."""
    sid = _resolve_schedule(id_or_name)
    if not yes and not typer.confirm(
        f"Supprimer le planning {id_or_name} ? La ressource cible sera rallumée."
    ):
        raise typer.Abort()
    try:
        client.delete(f"{SCHEDULE_PATH}/{sid}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Planning supprimé.")


@app.command()
def enable(id_or_name: str = typer.Argument(..., metavar="ID|NOM")) -> None:
    """Active un planning (les transitions marche/arrêt reprennent)."""
    sid = _resolve_schedule(id_or_name)
    try:
        s = client.post(f"{SCHEDULE_PATH}/{sid}/enable")
    except client.APIError as e:
        raise _bail(e) from e
    name = s.get("name", sid) if isinstance(s, dict) else sid
    rprint(f"[green]✓[/green] Planning [bold]{name}[/bold] activé.")


@app.command()
def disable(id_or_name: str = typer.Argument(..., metavar="ID|NOM")) -> None:
    """Désactive un planning (aucune transition appliquée ; état de la ressource figé)."""
    sid = _resolve_schedule(id_or_name)
    try:
        s = client.post(f"{SCHEDULE_PATH}/{sid}/disable")
    except client.APIError as e:
        raise _bail(e) from e
    name = s.get("name", sid) if isinstance(s, dict) else sid
    rprint(f"[green]✓[/green] Planning [bold]{name}[/bold] désactivé.")
