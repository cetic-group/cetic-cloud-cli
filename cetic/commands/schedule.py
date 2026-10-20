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

Période de validité (facultative, dates incluses, fuseau du planning) :
    --starts-on 2026-10-20    commence ce jour-là (avant : ressource allumée)
    --ends-on 2027-02-19      s'arrête après ce jour (ressource rallumée,
                              planning terminé)
    --for 4m | --for 6w       durée en mois (m) ou semaines (w) à partir de
                              --starts-on (à défaut : date de début déjà
                              enregistrée, sinon aujourd'hui dans le fuseau
                              du planning)

    Exemple : tous les lundis 8h-20h pendant 4 mois
        cetic schedule create lundis --vm web-01 --off "mon:08-mon:20" --for 4m

Sous-commandes :
    cetic schedule list
    cetic schedule get ID|NOM
    cetic schedule create NOM --vm <id> --off "fri:20-mon:08" [--timezone Europe/Paris]
    cetic schedule update ID|NOM [--name ...] [--off ...] [--timezone ...]
                              [--starts-on ...] [--ends-on ...] [--for ...]
                              [--no-start] [--no-end]
    cetic schedule delete ID|NOM [--yes]
    cetic schedule enable ID|NOM
    cetic schedule disable ID|NOM

Un arrêt de moins d'une heure ou plus de 2 cycles marche/arrêt par jour est
refusé (aucun gain : l'économie est facturée à l'heure).
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
# Période de validité
# ---------------------------------------------------------------------------

_FOR_RE = re.compile(r"^\s*(?P<n>\d{1,3})\s*(?P<u>[wm])\s*$", re.IGNORECASE)


def _parse_date(value: str, flag: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise typer.BadParameter(f"{flag} attend une date AAAA-MM-JJ (reçu : {value!r}).") from exc


def _period_end(start: date, count: int, unit: str) -> date:
    """Dernier jour inclus d'une durée de `count` semaines (`w`) ou mois (`m`).

    « 4 mois à partir du 20/10 » → 19/02. Un jour absent du mois d'arrivée est
    ramené au dernier jour de ce mois (même calcul que la console)."""
    if unit == "w":
        return start + timedelta(days=count * 7 - 1)
    month_index = start.month - 1 + count
    year, month = start.year + month_index // 12, month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) - timedelta(days=1)


def _parse_for(value: str, start: date) -> date:
    m = _FOR_RE.match(value)
    if not m or int(m["n"]) < 1:
        raise typer.BadParameter(
            f"--for attend un nombre suivi de w (semaines) ou m (mois), ex : 4m (reçu : {value!r})."
        )
    return _period_end(start, int(m["n"]), m["u"].lower())


# Fuseau appliqué par la plateforme quand aucun n'est précisé.
_DEFAULT_TZ = "Europe/Paris"


def _today_in(tz_name: str | None) -> date:
    """Date du jour dans le fuseau du planning : la plateforme y lit les bornes."""
    try:
        return datetime.now(ZoneInfo(tz_name or _DEFAULT_TZ)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return date.today()


def _check_period_options(
    *, starts_on: str | None, ends_on: str | None, for_: str | None,
    no_start: bool = False, no_end: bool = False,
) -> None:
    """Contrôles locaux, avant tout appel réseau."""
    if for_ and ends_on:
        raise typer.BadParameter("--for et --ends-on sont exclusifs.")
    if no_start and starts_on:
        raise typer.BadParameter("--no-start et --starts-on sont exclusifs.")
    if no_end and (ends_on or for_):
        raise typer.BadParameter("--no-end est exclusif de --ends-on et --for.")
    if starts_on:
        _parse_date(starts_on, "--starts-on")
    if ends_on:
        _parse_date(ends_on, "--ends-on")
    if for_:
        _parse_for(for_, date(2000, 1, 1))


def _period_body(
    *,
    starts_on: str | None,
    ends_on: str | None,
    for_: str | None,
    no_start: bool = False,
    no_end: bool = False,
    for_base: date | None = None,
) -> dict[str, Any]:
    """Champs `starts_on` / `ends_on` à envoyer (`None` = retirer la borne).

    `--for` part de `--starts-on`, à défaut de `for_base` (date de début déjà
    enregistrée, ou aujourd'hui dans le fuseau du planning)."""
    _check_period_options(
        starts_on=starts_on, ends_on=ends_on, for_=for_, no_start=no_start, no_end=no_end,
    )
    body: dict[str, Any] = {}
    start = _parse_date(starts_on, "--starts-on") if starts_on else None
    end = _parse_date(ends_on, "--ends-on") if ends_on else None
    if for_:
        base = start or for_base
        if base is None:
            raise typer.BadParameter("--for : date de départ inconnue.")
        end = _parse_for(for_, base)
    if start and end and end < start:
        raise typer.BadParameter("La date de fin doit suivre la date de début.")
    if start:
        body["starts_on"] = start.isoformat()
    if end:
        body["ends_on"] = end.isoformat()
    if no_start:
        body["starts_on"] = None
    if no_end:
        body["ends_on"] = None
    return body


def _period_ignored(sent: dict[str, Any], s: Any) -> bool:
    """Vrai si la plateforme n'a pas pris en compte la période envoyée.

    Une plateforme qui ne connaît pas encore la période ignore ces champs en
    silence : sans cette vérification, le planning s'appliquerait sans limite."""
    if not isinstance(s, dict):
        return False
    return any(key not in s or s.get(key) != value for key, value in sent.items())


_PERIOD_UNSUPPORTED = (
    "la plateforme n'a pas enregistré la période de validité "
    "(fonctionnalité pas encore disponible sur cette plateforme)."
)


def _fmt_period(s: dict[str, Any]) -> str:
    start, end = s.get("starts_on"), s.get("ends_on")
    if s.get("ended_at"):
        return f"terminé ({end})" if end else "terminé"
    if start and end:
        return f"{start} → {end}"
    if start:
        return f"à partir du {start}"
    if end:
        return f"jusqu'au {end}"
    return "—"


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
            "period": _fmt_period(s),
            "starts_on": s.get("starts_on"),
            "ends_on": s.get("ends_on"),
            "ended_at": s.get("ended_at"),
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
            ("period", "Période"),
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
    view["period"] = _fmt_period(s)
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
    starts_on: str | None = typer.Option(
        None, "--starts-on", metavar="AAAA-MM-JJ",
        help="Premier jour (inclus) où le planning s'applique. Avant : ressource allumée.",
    ),
    ends_on: str | None = typer.Option(
        None, "--ends-on", metavar="AAAA-MM-JJ",
        help="Dernier jour (inclus). Ensuite la ressource est rallumée et le planning terminé.",
    ),
    for_: str | None = typer.Option(
        None, "--for", metavar="DURÉE",
        help="Durée à partir de --starts-on (ou d'aujourd'hui) : 6w (semaines) ou 4m (mois).",
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
        cetic schedule create lundis --vm web-01 --off "mon:08-mon:20" --for 4m
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
    period = _period_body(
        starts_on=starts_on, ends_on=ends_on, for_=for_, for_base=_today_in(timezone),
    )

    body: dict[str, Any] = {
        "name": name,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "windows": windows,
        "enabled": not disabled,
    }
    if timezone is not None:
        body["timezone"] = timezone
    body.update(period)

    try:
        s = client.post(SCHEDULE_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    if period and _period_ignored(period, s):
        # Un planning sans sa borne s'appliquerait indéfiniment : on le retire.
        try:
            client.delete(f"{SCHEDULE_PATH}/{s['id']}")
            rprint(f"[red]Erreur : {_PERIOD_UNSUPPORTED} Le planning n'a pas été créé.[/red]")
        except client.APIError:
            rprint(
                f"[red]Erreur : {_PERIOD_UNSUPPORTED} Le planning {s['id']} a été créé SANS "
                "période : supprimez-le (`cetic schedule delete`).[/red]"
            )
        raise typer.Exit(1)
    rprint(
        f"[green]✓[/green] Planning créé : [bold]{s.get('name', name)}[/bold] "
        f"([dim]{s['id']}[/dim]) — cible {resource_type} {resource_id}"
    )
    if period:
        rprint(f"  Période : {_fmt_period(s)}")


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
    starts_on: str | None = typer.Option(
        None, "--starts-on", metavar="AAAA-MM-JJ", help="Nouveau premier jour (inclus)."
    ),
    ends_on: str | None = typer.Option(
        None, "--ends-on", metavar="AAAA-MM-JJ", help="Nouveau dernier jour (inclus)."
    ),
    for_: str | None = typer.Option(
        None, "--for", metavar="DURÉE",
        help="Nouvelle fin : durée à partir de --starts-on (ou d'aujourd'hui), ex : 4m, 6w.",
    ),
    no_start: bool = typer.Option(False, "--no-start", help="Retire la date de début."),
    no_end: bool = typer.Option(False, "--no-end", help="Retire la date de fin."),
) -> None:
    """Modifie un planning (nom, fenêtres, fuseau, période).

    Pour (dés)activer un planning, utilisez `cetic schedule enable|disable`. La
    cible d'un planning ne peut pas être changée (créez-en un nouveau). Pour
    relancer un planning terminé : repoussez sa fin (--ends-on, --for ou
    --no-end) puis `cetic schedule enable`.
    """
    _check_period_options(
        starts_on=starts_on, ends_on=ends_on, for_=for_, no_start=no_start, no_end=no_end,
    )
    sid = _resolve_schedule(id_or_name)
    for_base: date | None = None
    if for_ and not starts_on:
        # Même règle que la console : la durée part de la date de début déjà
        # enregistrée, sinon d'aujourd'hui dans le fuseau du planning.
        try:
            current = client.get(f"{SCHEDULE_PATH}/{sid}")
        except client.APIError as e:
            raise _bail(e) from e
        stored = current.get("starts_on") if isinstance(current, dict) else None
        tz_name = timezone or (current.get("timezone") if isinstance(current, dict) else None)
        for_base = (
            _parse_date(stored, "starts_on") if stored and not no_start else _today_in(tz_name)
        )
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if off:
        body["windows"] = _parse_off_windows(off)
    if timezone is not None:
        body["timezone"] = timezone
    period = _period_body(
        starts_on=starts_on, ends_on=ends_on, for_=for_, no_start=no_start, no_end=no_end,
        for_base=for_base,
    )
    body.update(period)
    if not body:
        rprint(
            "[yellow]Rien à modifier (utilisez --name, --off, --timezone ou une option "
            "de période).[/yellow]"
        )
        raise typer.Exit(0)
    try:
        s = client.patch(f"{SCHEDULE_PATH}/{sid}", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    if period and _period_ignored(period, s):
        others = "Les autres modifications ont été appliquées. " if len(body) > len(period) else ""
        rprint(f"[red]Erreur : {_PERIOD_UNSUPPORTED} {others}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Planning [bold]{s.get('name', sid)}[/bold] mis à jour.")
    if period and isinstance(s, dict):
        rprint(f"  Période : {_fmt_period(s)}")


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
