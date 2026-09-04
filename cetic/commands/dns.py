"""cetic dns — DNS privé : zones et enregistrements servis dans votre réseau.

Une zone DNS déclarée ici n'est servie QUE dans le réseau privé (VPC) du client :
aucun serveur public, aucune délégation depuis Internet. Les machines créées
dans ce réseau reçoivent automatiquement le résolveur comme serveur de noms.

    cetic dns zone list
    cetic dns zone get <ZONE>
    cetic dns zone create <NOM> --vpc <VPC> [--tier dev|prod] [--ttl 3600] [--dnssec]
    cetic dns zone verify <ZONE>          # domaine public uniquement
    cetic dns zone delete <ZONE>

    cetic dns record list <ZONE>
    cetic dns record set <ZONE> <NOM> <TYPE> <VALEUR>... [--ttl 3600]
    cetic dns record delete <ZONE> <NOM> <TYPE>

Trois points qui ne se devinent pas (cf. `DNS_CONTRACT.md` côté API) :

1. **`record set` REMPLACE** — l'unité d'édition est le rrset, c'est-à-dire le
   couple (nom, type) et TOUTES ses valeurs. Poser une valeur sur un nom qui en
   portait déjà supprime les précédentes. D'où `set` et non `add`, qui mentirait.
2. **La portée est le VPC, pas le sous-réseau** — le résolveur a une patte dans
   chaque sous-réseau du VPC et y répond les mêmes zones. D'où `--vpc`.
3. **Le niveau (`--tier`) est une propriété du RÉSEAU** — toutes les zones d'un
   même VPC partagent leur résolveur, donc son niveau.

Le résolveur est posé dans la configuration d'une machine **à sa création** :
activer le DNS privé sur un réseau déjà peuplé ne rend pas la zone visible
depuis les machines existantes.
"""

from __future__ import annotations

from typing import Any

import typer
from rich import print as rprint

from cetic import client, config
from cetic._resolve import looks_like_uuid, resolve_id
from cetic.commands._render import render_list, render_one

ZONES_PATH = "/v1/dns/zones"

#: Types ouverts au client (contrat d'API — `models/dns_record.ALLOWED_RECORD_TYPES`).
#: `NS` y figure : celui de l'apex est posé par la plateforme et rendu en lecture
#: seule ; une délégation sur un sous-nom est refusée (422) — une zone privée ne
#: délègue rien.
RECORD_TYPES: tuple[str, ...] = ("A", "AAAA", "CNAME", "MX", "TXT", "SRV", "CAA", "NS")

app = typer.Typer(help="DNS privé — zones et enregistrements servis dans votre réseau")
zone_app = typer.Typer(help="Zones DNS privées")
record_app = typer.Typer(help="Enregistrements d'une zone (rrsets)")
app.add_typer(zone_app, name="zone")
app.add_typer(record_app, name="record")


# ---------------------------------------------------------------------------
# Helpers purs (testables unitairement)
# ---------------------------------------------------------------------------


def qualify(name: str, zone_name: str) -> str:
    """Rend la forme pleinement qualifiée d'un nom d'enregistrement.

    Accepte le relatif (`www`), l'absolu (`www.corp.internal`) et `@` pour
    l'apex — les trois formes que l'API accepte. C'est cette forme qui sert à
    retrouver le rrset dans le listing, dont les noms sont toujours qualifiés.
    """
    n = (name or "").strip().lower().rstrip(".")
    z = (zone_name or "").strip().lower().rstrip(".")
    if n in ("", "@"):
        return z
    if n == z or n.endswith(f".{z}"):
        return n
    return f"{n}.{z}"


def find_record_set(
    rrsets: list[dict[str, Any]], *, fqdn: str, record_type: str
) -> dict[str, Any] | None:
    """Retrouve le rrset par son couple (nom, type) — jamais par UUID.

    En ligne de commande on pense « le A de www », pas un identifiant : l'UUID
    du rrset n'est exposé que dans la sortie JSON/YAML.
    """
    for r in rrsets:
        if (
            str(r.get("name", "")).lower().rstrip(".") == fqdn
            and str(r.get("record_type", "")).upper() == record_type
        ):
            return r
    return None


def normalize_record_type(value: str) -> str:
    """Normalise et valide un type d'enregistrement contre le catalogue ouvert."""
    rtype = (value or "").strip().upper()
    if rtype not in RECORD_TYPES:
        raise typer.BadParameter(
            f"Type d'enregistrement invalide : {value!r}. "
            f"Attendu un de {', '.join(RECORD_TYPES)}."
        )
    return rtype


def _fmt_values(values: Any) -> str:
    if isinstance(values, list):
        return ", ".join(str(v) for v in values) if values else "—"
    return str(values) if values else "—"


# ---------------------------------------------------------------------------
# Erreurs
# ---------------------------------------------------------------------------


def _bail(e: client.APIError) -> typer.Exit:
    """Affiche l'erreur API telle qu'elle vient. Rien de plus, et c'est voulu.

    Les messages de l'API portent déjà le geste à faire : autre nom, niveau
    effectif du réseau, enregistrements à retirer d'abord.

    ⚠️ **Ne pas ré-ajouter de glose sur le 503.** Une version antérieure de ce
    fichier ajoutait « le service DNS n'est pas déployé ici : réessayer n'y
    changera rien » — la ligne du tableau d'erreurs de `DNS_CONTRACT.md`, qui
    n'est plus vraie du code. Les deux seuls 503 que le domaine DNS émet disent
    l'inverse et demandent de réessayer : `powerdns.py` (« Votre serveur DNS
    n'est pas encore prêt ») et `dns_zones.py` (résolveur de vérification en
    panne, « votre enregistrement TXT n'est pas en cause »). Le client lisait
    donc deux phrases contradictoires — et sur `zone verify`, celui qui renonce
    perd sa zone : `_expire_unverified_zones` la supprime 7 jours après sa
    CRÉATION, pas après la dernière tentative.
    """
    rprint(f"[red]Erreur : {e.detail}[/red]")
    return typer.Exit(1)


# ---------------------------------------------------------------------------
# Résolution d'une zone
# ---------------------------------------------------------------------------


def _zone_by_name(name: str) -> str:
    """UUID de la zone portant ce nom (unicité par organisation)."""
    wanted = name.strip().lower().rstrip(".")
    try:
        zones = client.get(ZONES_PATH)
    except client.APIError as e:
        raise _bail(e) from e
    matches = [z for z in zones if str(z.get("name", "")).lower() == wanted]
    if not matches:
        rprint(f"[red]Aucune zone DNS nommée '{name}'.[/red]")
        raise typer.Exit(1)
    return matches[0]["id"]


def _fetch_zone(zone_ref: str) -> dict[str, Any]:
    """Fiche complète de la zone désignée par son UUID ou son nom.

    La fiche — et pas seulement l'identifiant : le nom de la zone est ce qui
    permet de qualifier un nom d'enregistrement saisi en relatif.
    """
    zone_id = zone_ref if looks_like_uuid(zone_ref) else _zone_by_name(zone_ref)
    try:
        return client.get(f"{ZONES_PATH}/{zone_id}")
    except client.APIError as e:
        raise _bail(e) from e


def _print_challenge(zone: dict[str, Any]) -> None:
    """Affiche la preuve de possession à publier, s'il y en a une.

    Rendue uniquement sur un **domaine public** en attente : personne ne possède
    `.local`, une zone à suffixe interne part directement en provisionnement.
    """
    challenge = zone.get("ownership_challenge")
    if not challenge:
        return
    rprint(
        "\n[yellow]Ce nom est un domaine public : publiez cet enregistrement "
        "dans son DNS public, puis relancez la vérification.[/yellow]"
    )
    render_list(
        [
            {
                "type": challenge.get("record_type", "TXT"),
                "name": challenge.get("record_name"),
                "value": challenge.get("record_value"),
            }
        ],
        title="Enregistrement de possession à publier",
        columns=[("type", "Type"), ("name", "Nom"), ("value", "Valeur")],
    )
    rprint(f"[dim]Puis : cetic dns zone verify {zone.get('name') or zone.get('id')}[/dim]")


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


@zone_app.command(name="list")
def list_zones() -> None:
    """Liste les zones DNS privées de l'organisation."""
    try:
        items = client.get(ZONES_PATH)
    except client.APIError as e:
        raise _bail(e) from e

    if config.get_output() in ("json", "yaml"):
        # Charge de l'API telle quelle : `dnssec_enabled`, `error_message` et
        # `created_at` ne sont pas dans la table, ils ne doivent pas disparaître
        # de la sortie machine pour autant.
        render_list(items, title="", columns=[])
        return

    rows = [
        {
            "id": z.get("id"),
            "name": z.get("name"),
            "vpc_id": z.get("vpc_id"),
            "region": z.get("region"),
            "status": z.get("status"),
            "default_ttl": z.get("default_ttl"),
            "record_sets_count": z.get("record_sets_count"),
        }
        for z in items
    ]
    render_list(
        rows,
        title=f"Zones DNS ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("name", "Zone"),
            ("vpc_id", "VPC"),
            ("region", "Région"),
            ("status", "Statut"),
            ("default_ttl", "TTL"),
            ("record_sets_count", "Enregistrements"),
        ],
    )


@zone_app.command(name="get")
def get_zone(zone: str = typer.Argument(..., help="UUID ou nom de la zone")) -> None:
    """Fiche d'une zone : état, résolveur à interroger, preuve de possession."""
    z = _fetch_zone(zone)

    if config.get_output() in ("json", "yaml"):
        # Fiche brute — `resolver.endpoints` et `ownership_challenge` compris.
        render_one(z, title=f"Zone {z.get('name', zone)}")
        return

    resolver = z.get("resolver") or {}
    render_one(
        {
            "id": z.get("id"),
            "name": z.get("name"),
            "vpc_id": z.get("vpc_id"),
            "region": z.get("region"),
            "status": z.get("status"),
            "default_ttl": z.get("default_ttl"),
            "dnssec_enabled": z.get("dnssec_enabled"),
            "record_sets_count": z.get("record_sets_count"),
            "error_message": z.get("error_message"),
            "created_at": z.get("created_at"),
            "resolver_tier": resolver.get("tier"),
            "resolver_status": resolver.get("status"),
            "resolver_ns": resolver.get("ns_hostname"),
        },
        title=f"Zone {z.get('name', zone)}",
    )

    # Une adresse PAR SOUS-RÉSEAU : depuis une machine, il faut celle de SON
    # sous-réseau — toutes répondent la même chose, mais chacune n'est joignable
    # que depuis le sien. `endpoints` est le seul champ qui dit laquelle.
    endpoints = resolver.get("endpoints") or []
    if endpoints:
        render_list(
            [
                {
                    "address": ep.get("address"),
                    "vnet_name": ep.get("vnet_name"),
                    "vnet_cidr": ep.get("vnet_cidr"),
                    "vnet_id": ep.get("vnet_id"),
                }
                for ep in endpoints
            ],
            title="Résolveur — une adresse par sous-réseau",
            columns=[
                ("address", "Adresse"),
                ("vnet_name", "Sous-réseau"),
                ("vnet_cidr", "CIDR"),
                ("vnet_id", "ID sous-réseau"),
            ],
        )
        rprint(
            "[dim]Depuis une machine, utilisez l'adresse de SON sous-réseau : "
            "une adresse prise dans un autre réseau ne répond pas.[/dim]"
        )
    elif resolver.get("addresses"):
        # Repli : une API antérieure au champ `endpoints` ne rend que les adresses.
        rprint(f"Résolveur : {', '.join(resolver['addresses'])}")
    else:
        rprint("[dim]Résolveur pas encore debout : aucune adresse à interroger.[/dim]")

    if resolver.get("applies_to_new_guests_only"):
        rprint(
            "[yellow]⚠[/yellow] Les machines reçoivent ce résolveur [bold]à leur "
            "création[/bold] : celles qui existaient déjà dans ce réseau gardent "
            "le leur et ne verront pas la zone."
        )

    _print_challenge(z)


@zone_app.command(name="create")
def create_zone(
    name: str = typer.Argument(..., help="Nom de la zone, ex. corp.internal"),
    vpc: str = typer.Option(
        ...,
        "--vpc",
        help=(
            "UUID ou nom du VPC servi. C'est le RÉSEAU PRIVÉ, pas le sous-réseau : "
            "le résolveur a une patte dans chacun d'eux et y répond les mêmes zones."
        ),
    ),
    tier: str = typer.Option(
        "dev",
        "--tier",
        case_sensitive=False,
        help=(
            "dev = un serveur, prod = paire redondante (bascule auto). Propriété du "
            "RÉSEAU : toutes les zones du VPC la partagent. Un niveau différent de "
            "celui déjà en place est refusé (409), avec le niveau effectif."
        ),
    ),
    ttl: int | None = typer.Option(
        None,
        "--ttl",
        help="TTL par défaut des enregistrements (60 à 604800 s). Omis = réglage plateforme.",
    ),
    dnssec: bool = typer.Option(
        False,
        "--dnssec",
        help="Signer la zone. Sans objet sur une zone privée (aucune chaîne de confiance).",
    ),
) -> None:
    """Crée une zone DNS privée servie dans un VPC.

    Un suffixe interne (`corp.internal`, `home.arpa`, `lan`) part directement en
    provisionnement. Un **domaine public** (`exemple.com`) naît en attente de
    preuve : rien n'est créé tant que le TXT rendu ici n'est pas publié dans son
    DNS public et constaté par `cetic dns zone verify`.
    """
    tier_norm = tier.lower()
    if tier_norm not in ("dev", "prod"):
        rprint(f"[red]--tier invalide : '{tier}'. Valeurs autorisées : dev, prod.[/red]")
        raise typer.Exit(1)

    vpc_id = resolve_id("/v1/vpcs", vpc)
    body: dict[str, Any] = {
        "name": name,
        "vpc_id": vpc_id,
        "tier": tier_norm,
        "dnssec_enabled": dnssec,
    }
    # Omis = « prendre le réglage de la plateforme ». Envoyer un défaut en dur
    # rendrait ce réglage mort.
    if ttl is not None:
        body["default_ttl"] = ttl

    try:
        z = client.post(ZONES_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e

    rprint(f"[green]✓[/green] Zone créée : [bold]{z['id']}[/bold] (statut : {z.get('status', '?')})")
    if z.get("status") == "pending_verification":
        _print_challenge(z)
    else:
        rprint(
            "[dim]Le résolveur est provisionné en quelques minutes. "
            f"Suivre avec : cetic dns zone get {z.get('name') or z['id']}[/dim]"
        )


@zone_app.command(name="verify")
def verify_zone(zone: str = typer.Argument(..., help="UUID ou nom de la zone")) -> None:
    """Constate la preuve de possession d'un domaine public, puis provisionne.

    Sans objet sur un suffixe interne : personne ne possède `.local`, il n'y a
    rien à prouver.
    """
    z = _fetch_zone(zone)
    try:
        z = client.post(f"{ZONES_PATH}/{z['id']}/verify")
    except client.APIError as e:
        raise _bail(e) from e

    status = z.get("status", "?")
    if status == "pending_verification":
        rprint("[yellow]Preuve non constatée : la zone reste en attente.[/yellow]")
        _print_challenge(z)
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Zone vérifiée : [bold]{z.get('name')}[/bold] (statut : {status})")


@zone_app.command(name="delete")
def delete_zone(
    zone: str = typer.Argument(..., help="UUID ou nom de la zone"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime une zone. Refusée tant qu'elle porte des enregistrements.

    ⚠️ Si c'est la dernière zone du réseau, le résolveur est démonté — et les
    machines déjà créées continuent de l'interroger en premier : chaque
    résolution non mise en cache attend son délai d'expiration avant de basculer
    sur le relais de la plateforme, jusqu'à leur recréation.
    """
    z = _fetch_zone(zone)
    if not yes and not typer.confirm(
        f"Supprimer la zone {z.get('name')} ? "
        "Si c'est la dernière du réseau, son résolveur sera démonté."
    ):
        raise typer.Abort()
    try:
        client.delete(f"{ZONES_PATH}/{z['id']}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Zone supprimée.")


# ---------------------------------------------------------------------------
# Enregistrements (rrsets)
# ---------------------------------------------------------------------------


@record_app.command(name="list")
def list_records(zone: str = typer.Argument(..., help="UUID ou nom de la zone")) -> None:
    """Liste les enregistrements de la zone, valeurs comprises.

    Ceux posés par la plateforme (le NS de l'apex) apparaissent en lecture seule :
    ils décrivent la zone, ils ne se modifient pas.
    """
    z = _fetch_zone(zone)
    try:
        items = client.get(f"{ZONES_PATH}/{z['id']}/records")
    except client.APIError as e:
        raise _bail(e) from e

    if config.get_output() in ("json", "yaml"):
        # Brut : l'UUID du rrset et la liste de valeurs n'ont de sens que là.
        render_list(items, title="", columns=[])
        return

    rows = [
        {
            "name": r.get("name"),
            "record_type": r.get("record_type"),
            "ttl": r.get("ttl"),
            "records": _fmt_values(r.get("records")),
            "managed": "plateforme" if r.get("is_system_managed") else "—",
        }
        for r in items
    ]
    render_list(
        rows,
        title=f"Enregistrements de {z.get('name')} ({len(rows)})",
        columns=[
            ("name", "Nom"),
            ("record_type", "Type"),
            ("ttl", "TTL"),
            ("records", "Valeurs"),
            ("managed", "Géré par"),
        ],
    )


@record_app.command(name="set")
def set_record(
    zone: str = typer.Argument(..., help="UUID ou nom de la zone"),
    name: str = typer.Argument(
        ...,
        help="Nom relatif (www), absolu, ou @ pour l'apex "
             "(253 caractères au plus, nom de la zone compris)",
    ),
    record_type: str = typer.Argument(..., help=f"Type : {', '.join(RECORD_TYPES)}"),
    values: list[str] = typer.Argument(
        ...,
        help="Valeurs, dans la syntaxe de présentation ('10 mail.exemple.com.', '\"v=spf1 -all\"')",
    ),
    ttl: int | None = typer.Option(
        None, "--ttl", help="TTL en secondes (60 à 604800). Défaut API : 3600."
    ),
) -> None:
    """Pose le couple (nom, type) et TOUTES ses valeurs.

    ⚠️ [bold]REMPLACE, n'ajoute pas.[/bold] L'unité d'édition est le rrset :
    `cetic dns record set corp.internal www A 10.0.0.11` sur un nom qui portait
    déjà `10.0.0.10` [bold]supprime[/bold] `10.0.0.10`. Pour ajouter une valeur,
    listez d'abord (`cetic dns record list`) et renvoyez la liste complète.

    C'est pour cela que la commande s'appelle `set` : un `add` mentirait.
    """
    rtype = normalize_record_type(record_type)
    z = _fetch_zone(zone)
    fqdn = qualify(name, z.get("name", ""))

    # Le rrset s'adresse par (nom, type) — on résout l'UUID depuis le listing,
    # jamais en le demandant à l'utilisateur.
    try:
        existing = client.get(f"{ZONES_PATH}/{z['id']}/records")
    except client.APIError as e:
        raise _bail(e) from e
    current = find_record_set(existing, fqdn=fqdn, record_type=rtype)

    try:
        if current is None:
            body: dict[str, Any] = {
                "name": name,
                "record_type": rtype,
                "records": list(values),
            }
            if ttl is not None:
                body["ttl"] = ttl
            r = client.post(f"{ZONES_PATH}/{z['id']}/records", json=body)
            action = "créé"
        else:
            patch: dict[str, Any] = {"records": list(values)}
            if ttl is not None:
                patch["ttl"] = ttl
            r = client.patch(
                f"{ZONES_PATH}/{z['id']}/records/{current['id']}", json=patch
            )
            action = "remplacé"
    except client.APIError as e:
        raise _bail(e) from e

    rprint(
        f"[green]✓[/green] Enregistrement {action} : "
        f"[bold]{r.get('name', fqdn)} {rtype}[/bold] → {_fmt_values(r.get('records'))}"
    )


@record_app.command(name="delete")
def delete_record(
    zone: str = typer.Argument(..., help="UUID ou nom de la zone"),
    name: str = typer.Argument(
        ...,
        help="Nom relatif (www), absolu, ou @ pour l'apex "
             "(253 caractères au plus, nom de la zone compris)",
    ),
    record_type: str = typer.Argument(..., help=f"Type : {', '.join(RECORD_TYPES)}"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime le couple (nom, type) et toutes ses valeurs."""
    rtype = normalize_record_type(record_type)
    z = _fetch_zone(zone)
    fqdn = qualify(name, z.get("name", ""))

    try:
        existing = client.get(f"{ZONES_PATH}/{z['id']}/records")
    except client.APIError as e:
        raise _bail(e) from e
    current = find_record_set(existing, fqdn=fqdn, record_type=rtype)
    if current is None:
        rprint(f"[red]Aucun enregistrement {rtype} sur '{fqdn}'.[/red]")
        raise typer.Exit(1)

    if not yes and not typer.confirm(
        f"Supprimer {fqdn} {rtype} ({_fmt_values(current.get('records'))}) ?"
    ):
        raise typer.Abort()

    try:
        client.delete(f"{ZONES_PATH}/{z['id']}/records/{current['id']}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Enregistrement supprimé : [bold]{fqdn} {rtype}[/bold].")
