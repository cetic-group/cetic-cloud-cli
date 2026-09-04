"""cetic email — messagerie hébergée : domaines, adresses, alias, jetons.

    cetic email domain list
    cetic email domain create <FQDN>
    cetic email domain show <FQDN>        # enregistrements DNS à poser + leur état
    cetic email domain verify <FQDN>      # constate la preuve de possession
    cetic email domain recheck <FQDN>     # re-constate l'état des enregistrements
    cetic email domain delete <FQDN>

    cetic email account list [--domain <FQDN>]
    cetic email account create <ADRESSE> [--quota-gb N] [--comment ...] [--imap/--no-imap]
    cetic email account show <ADRESSE>    # + bloc « Configuration client »
    cetic email account update <ADRESSE> [--enable/--disable] [--forward <DEST>]...
    cetic email account password <ADRESSE>
    cetic email account delete <ADRESSE>
    cetic email account token list|create|revoke <ADRESSE>

    cetic email alias list [--domain <FQDN>]
    cetic email alias create <ADRESSE> --to <DEST> [--to <DEST>]... [--wildcard]
    cetic email alias update <ADRESSE> [--to <DEST>]... [--wildcard/--no-wildcard]
    cetic email alias delete <ADRESSE>

Points qui ne se devinent pas :

- **Le mot de passe ne passe jamais en argument** : il est demandé
  interactivement (saisie masquée), ou lu sur l'entrée standard quand celle-ci
  n'est pas un terminal. Un mot de passe en argument finit dans l'historique du
  shell et dans la liste des processus.
- **Un domaine naît en attente** : le nom est réservé, rien n'est routé tant que
  le TXT de possession n'est pas publié et constaté (`domain verify`).
- **`--to` et `--forward` REMPLACENT** la liste existante : envoyer une seule
  adresse retire toutes les autres.
- Les réglages antispam et « Envoyer en tant que » ne s'écrivent pas ici : les
  premiers sont forcés côté plateforme, le second est une élévation de privilège
  qui se délègue par l'IAM et se pilote depuis la console.
"""

from __future__ import annotations

import sys
from typing import Any

import typer
from rich import print as rprint

from cetic import client, config
from cetic._resolve import looks_like_uuid
from cetic.commands._render import render_list, render_one

DOMAINS_PATH = "/v1/email/domains"
ACCOUNTS_PATH = "/v1/email/accounts"
ALIASES_PATH = "/v1/email/aliases"

#: Plancher du mot de passe de boîte, aligné sur le contrat d'API. Un mot de
#: passe de boîte s'éprouve directement sur IMAP/SMTP, exposés à Internet et
#: sans limitation applicative — d'où un plancher plus haut que celui de la
#: console.
PASSWORD_MIN_LENGTH = 12

app = typer.Typer(help="Messagerie hébergée — domaines, adresses et alias")
domain_app = typer.Typer(help="Domaines de messagerie")
account_app = typer.Typer(help="Adresses (boîtes aux lettres)")
token_app = typer.Typer(help="Jetons d'application d'une adresse")
alias_app = typer.Typer(help="Alias (redirections sans boîte)")
app.add_typer(domain_app, name="domain")
app.add_typer(account_app, name="account")
app.add_typer(alias_app, name="alias")
account_app.add_typer(token_app, name="token")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bail(e: client.APIError) -> typer.Exit:
    rprint(f"[red]Erreur : {e.detail}[/red]")
    return typer.Exit(1)


def _fmt_bytes(value: Any) -> str:
    """Rend une taille en octets sous forme lisible (Go/Mo), « — » si absente."""
    if value is None:
        return "—"
    try:
        size = float(value)
    except (TypeError, ValueError):
        return str(value)
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} Go"
    if size >= 1024**2:
        return f"{size / 1024**2:.1f} Mo"
    return f"{int(size)} o"


def _fmt_list(values: Any) -> str:
    if isinstance(values, list):
        return ", ".join(str(v) for v in values) if values else "—"
    return str(values) if values else "—"


def _yes_no(value: Any) -> str:
    return "oui" if value else "non"


def read_password(label: str) -> str:
    """Obtient un mot de passe SANS jamais le prendre en argument de commande.

    Terminal → saisie masquée avec confirmation. Entrée standard redirigée
    (script, tube) → première ligne lue telle quelle : c'est le seul moyen
    d'automatiser sans écrire le secret dans l'historique du shell ni dans la
    liste des processus.
    """
    if not sys.stdin.isatty():
        password = sys.stdin.readline().rstrip("\n")
        if not password:
            rprint(
                "[red]Aucun mot de passe reçu sur l'entrée standard.[/red]\n"
                "[dim]Exemple : printf '%s' \"$MDP\" | cetic email account create …[/dim]"
            )
            raise typer.Exit(1)
        return password
    return typer.prompt(label, hide_input=True, confirmation_prompt=True)


def _find_by(path: str, field: str, value: str, *, what: str) -> str:
    """UUID de la ressource dont `field` vaut `value` — ou l'UUID passé tel quel."""
    if looks_like_uuid(value):
        return value
    wanted = value.strip().lower()
    try:
        items = client.get(path)
    except client.APIError as e:
        raise _bail(e) from e
    matches = [it for it in items if str(it.get(field, "")).lower() == wanted]
    if not matches:
        rprint(f"[red]Aucun {what} '{value}'.[/red]")
        raise typer.Exit(1)
    return matches[0]["id"]


def _domain_id(ref: str) -> str:
    return _find_by(DOMAINS_PATH, "name", ref, what="domaine de messagerie")


def _account_id(ref: str) -> str:
    return _find_by(ACCOUNTS_PATH, "address", ref, what="adresse")


def _alias_id(ref: str) -> str:
    return _find_by(ALIASES_PATH, "address", ref, what="alias")


def _render_dns_records(domain: dict[str, Any]) -> None:
    """Affiche les enregistrements DNS attendus, un par ligne, copiables tels quels.

    La valeur est rendue ENTIÈRE (un MX porte sa priorité) ; `hostname` et
    `priority` sont donnés à côté parce que les interfaces DNS demandent presque
    toujours deux champs pour un MX — y coller la ligne complète invalide
    l'enregistrement, et plus aucun courrier n'arrive.
    """
    records: list[dict[str, Any]] = []
    verification = domain.get("verification")
    if verification:
        records.append(verification)
    records.extend(domain.get("records") or [])
    if not records:
        return
    rows = [
        {
            "type": r.get("type"),
            "name": r.get("name"),
            "value": r.get("value"),
            "priority": r.get("priority") if r.get("priority") is not None else "—",
            "status": r.get("status"),
            "purpose": r.get("purpose"),
        }
        for r in records
    ]
    render_list(
        rows,
        title="Enregistrements DNS attendus dans la zone du domaine",
        columns=[
            ("type", "Type"),
            ("name", "Nom"),
            ("value", "Valeur"),
            ("priority", "Priorité"),
            ("status", "État"),
            ("purpose", "À quoi ça sert"),
        ],
    )
    if any(r.get("exceeds_lookup_limit") for r in records):
        rprint(
            "[yellow]⚠[/yellow] La zone dépasse les 10 recherches DNS de SPF : "
            "la valeur est bonne, mais SPF répond en erreur pour tout le domaine."
        )


def _render_client_config(config_block: dict[str, Any] | None) -> None:
    """Affiche le bloc « Configuration client » (IMAP/POP3 + SMTP)."""
    if not config_block:
        return
    rows = []
    for key in ("incoming", "outgoing"):
        ep = config_block.get(key)
        if not ep:
            continue
        rows.append(
            {
                "role": "Réception" if key == "incoming" else "Envoi",
                "protocol": ep.get("protocol"),
                "hostname": ep.get("hostname"),
                "port": ep.get("port"),
                "security": ep.get("security"),
            }
        )
    if not rows:
        return
    render_list(
        rows,
        title="Configuration client",
        columns=[
            ("role", "Rôle"),
            ("protocol", "Protocole"),
            ("hostname", "Serveur"),
            ("port", "Port"),
            ("security", "Sécurité"),
        ],
    )
    hint = config_block.get("username_hint")
    if hint:
        rprint(f"[dim]{hint}[/dim]")


# ---------------------------------------------------------------------------
# Domaines
# ---------------------------------------------------------------------------


@domain_app.command(name="list")
def list_domains() -> None:
    """Liste les domaines de messagerie de l'organisation."""
    try:
        items = client.get(DOMAINS_PATH)
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": d.get("id"),
            "name": d.get("name"),
            "status": d.get("status"),
            "accounts_count": d.get("accounts_count"),
            "aliases_count": d.get("aliases_count"),
            "verified_at": d.get("verified_at"),
            "externally_managed": _yes_no(d.get("externally_managed")),
        }
        for d in items
    ]
    render_list(
        rows,
        title=f"Domaines de messagerie ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("name", "Domaine"),
            ("status", "Statut"),
            ("accounts_count", "Adresses"),
            ("aliases_count", "Alias"),
            ("verified_at", "Vérifié le"),
            ("externally_managed", "Piloté par Terraform"),
        ],
    )


@domain_app.command(name="create")
def create_domain(
    fqdn: str = typer.Argument(..., help="Nom de domaine, ex. exemple.com"),
) -> None:
    """Déclare un domaine. Le nom est réservé, rien n'est routé tout de suite.

    Il faut ensuite publier le TXT de possession (`cetic email domain show`),
    puis lancer `cetic email domain verify`.
    """
    try:
        d = client.post(DOMAINS_PATH, json={"name": fqdn})
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Domaine déclaré : [bold]{d['id']}[/bold] (statut : {d.get('status', '?')})")
    rprint(
        f"[dim]Enregistrements à publier : cetic email domain show {d.get('name', fqdn)}[/dim]"
    )


@domain_app.command(name="show")
def show_domain(fqdn: str = typer.Argument(..., help="UUID ou nom du domaine")) -> None:
    """Fiche d'un domaine : enregistrements DNS à poser, leur état, et la
    configuration à recopier dans un logiciel de messagerie."""
    domain_id = _domain_id(fqdn)
    try:
        d = client.get(f"{DOMAINS_PATH}/{domain_id}")
    except client.APIError as e:
        raise _bail(e) from e

    if config.get_output() in ("json", "yaml"):
        render_one(d, title=f"Domaine {d.get('name', fqdn)}")
        return

    render_one(
        {
            "id": d.get("id"),
            "name": d.get("name"),
            "status": d.get("status"),
            "verified_at": d.get("verified_at"),
            "dkim_generated_at": d.get("dkim_generated_at"),
            "externally_managed": d.get("externally_managed"),
            "accounts_count": d.get("accounts_count"),
            "aliases_count": d.get("aliases_count"),
            "created_at": d.get("created_at"),
        },
        title=f"Domaine {d.get('name', fqdn)}",
    )
    _render_dns_records(d)
    _render_client_config(d.get("client_config"))


@domain_app.command(name="verify")
def verify_domain(fqdn: str = typer.Argument(..., help="UUID ou nom du domaine")) -> None:
    """Constate le TXT de possession et active le domaine. Rejouable sans risque."""
    domain_id = _domain_id(fqdn)
    try:
        d = client.post(f"{DOMAINS_PATH}/{domain_id}/verify")
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Domaine [bold]{d.get('name', fqdn)}[/bold] "
        f"— statut : {d.get('status', '?')}"
    )


@domain_app.command(name="recheck")
def recheck_domain(fqdn: str = typer.Argument(..., help="UUID ou nom du domaine")) -> None:
    """Re-constate l'état des enregistrements de la zone et rend la fiche à jour.

    À utiliser juste après avoir publié les enregistrements chez l'hébergeur DNS.
    """
    domain_id = _domain_id(fqdn)
    try:
        d = client.post(f"{DOMAINS_PATH}/{domain_id}/recheck")
    except client.APIError as e:
        raise _bail(e) from e

    if config.get_output() in ("json", "yaml"):
        render_one(d, title=f"Domaine {d.get('name', fqdn)}")
        return
    rprint(f"[green]✓[/green] État relu — statut : {d.get('status', '?')}")
    _render_dns_records(d)


@domain_app.command(name="delete")
def delete_domain(
    fqdn: str = typer.Argument(..., help="UUID ou nom du domaine"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un domaine. Refusé tant qu'il porte des adresses ou des alias."""
    domain_id = _domain_id(fqdn)
    if not yes and not typer.confirm(f"Supprimer le domaine {fqdn} ?"):
        raise typer.Abort()
    try:
        client.delete(f"{DOMAINS_PATH}/{domain_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Domaine supprimé.")


# ---------------------------------------------------------------------------
# Adresses
# ---------------------------------------------------------------------------


@account_app.command(name="list")
def list_accounts(
    domain: str | None = typer.Option(
        None, "--domain", help="Ne rendre que les adresses de ce domaine (UUID ou nom)."
    ),
) -> None:
    """Liste les adresses de l'organisation."""
    params: dict[str, str] = {}
    if domain:
        params["domain_id"] = _domain_id(domain)
    try:
        items = client.get(ACCOUNTS_PATH, params=params or None)
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": a.get("id"),
            "address": a.get("address"),
            "quota": _fmt_bytes(a.get("quota_bytes")),
            "usage": _fmt_bytes(a.get("usage_bytes")),
            "enabled": _yes_no(a.get("enabled")),
            "imap": _yes_no(a.get("enable_imap")),
            "pop3": _yes_no(a.get("enable_pop")),
            "forward": _fmt_list(a.get("forward_destination"))
            if a.get("forward_enabled")
            else "—",
            "managed": "plateforme" if a.get("is_system_managed") else "—",
        }
        for a in items
    ]
    render_list(
        rows,
        title=f"Adresses ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("address", "Adresse"),
            ("quota", "Quota"),
            ("usage", "Occupation"),
            ("enabled", "Active"),
            ("imap", "IMAP"),
            ("pop3", "POP3"),
            ("forward", "Renvoi"),
            ("managed", "Gérée par"),
        ],
    )


@account_app.command(name="create")
def create_account(
    address: str = typer.Argument(..., help="Adresse complète, ex. contact@exemple.com"),
    quota_gb: int | None = typer.Option(
        None, "--quota-gb", help="Espace réservé, en Go (1 à 1024). Omis = défaut plateforme."
    ),
    comment: str | None = typer.Option(None, "--comment", help="À quoi sert cette boîte."),
    displayed_name: str | None = typer.Option(
        None, "--displayed-name", help="Nom affiché en expéditeur, ex. « Service commercial »."
    ),
    imap: bool = typer.Option(True, "--imap/--no-imap", help="Accès IMAP (activé par défaut)."),
    pop3: bool = typer.Option(
        False, "--pop3/--no-pop3",
        help="Accès POP3 (désactivé par défaut : POP3 rapatrie et efface).",
    ),
) -> None:
    """Crée une boîte aux lettres sur un domaine vérifié.

    Le mot de passe est demandé interactivement (saisie masquée) ou lu sur
    l'entrée standard — jamais passé en argument. Minimum 12 caractères.
    """
    password = read_password("Mot de passe de la boîte")
    body: dict[str, Any] = {
        "address": address,
        "password": password,
        "enable_imap": imap,
        "enable_pop": pop3,
    }
    # Le quota par défaut vient de la plateforme : ne rien envoyer quand il n'est
    # pas demandé, plutôt que de figer une valeur ici.
    if quota_gb is not None:
        body["quota_gb"] = quota_gb
    if comment is not None:
        body["comment"] = comment
    if displayed_name is not None:
        body["displayed_name"] = displayed_name

    try:
        a = client.post(ACCOUNTS_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Adresse créée : [bold]{a.get('address', address)}[/bold]")
    rprint(f"[dim]Paramètres du logiciel de messagerie : cetic email account show {address}[/dim]")


@account_app.command(name="show")
def show_account(address: str = typer.Argument(..., help="UUID ou adresse")) -> None:
    """Fiche d'une adresse, avec le bloc « Configuration client »."""
    account_id = _account_id(address)
    try:
        a = client.get(f"{ACCOUNTS_PATH}/{account_id}")
    except client.APIError as e:
        raise _bail(e) from e

    if config.get_output() in ("json", "yaml"):
        render_one(a, title=f"Adresse {a.get('address', address)}")
        return

    render_one(
        {
            "id": a.get("id"),
            "address": a.get("address"),
            "quota": _fmt_bytes(a.get("quota_bytes")),
            "usage": _fmt_bytes(a.get("usage_bytes")),
            "usage_updated_at": a.get("usage_updated_at"),
            "enabled": a.get("enabled"),
            "enable_imap": a.get("enable_imap"),
            "enable_pop": a.get("enable_pop"),
            "forward_enabled": a.get("forward_enabled"),
            "forward_destination": _fmt_list(a.get("forward_destination")),
            "forward_keep": a.get("forward_keep"),
            # Lecture seule ici : cette capacité est une élévation de privilège
            # dans le domaine, elle a sa propre action IAM et ne s'active pas
            # depuis la ligne de commande.
            "send_as_any_address": a.get("send_as_any_address"),
            "send_as_pending": a.get("send_as_pending"),
            "is_system_managed": a.get("is_system_managed"),
            "displayed_name": a.get("displayed_name"),
            "comment": a.get("comment"),
            "created_at": a.get("created_at"),
        },
        title=f"Adresse {a.get('address', address)}",
    )
    if a.get("usage_bytes") is not None and not a.get("usage_updated_at"):
        rprint("[dim]L'occupation est un relevé périodique, pas une mesure en direct.[/dim]")
    _render_client_config(a.get("client_config"))


@account_app.command(name="update")
def update_account(
    address: str = typer.Argument(..., help="UUID ou adresse"),
    enabled: bool | None = typer.Option(
        None, "--enable/--disable",
        help="Active ou coupe la boîte. Coupée, elle reste facturée (l'espace reste réservé).",
    ),
    quota_gb: int | None = typer.Option(None, "--quota-gb", help="Espace réservé, en Go."),
    comment: str | None = typer.Option(None, "--comment"),
    displayed_name: str | None = typer.Option(None, "--displayed-name"),
    imap: bool | None = typer.Option(None, "--imap/--no-imap"),
    pop3: bool | None = typer.Option(None, "--pop3/--no-pop3"),
    forward: list[str] = typer.Option(
        [], "--forward",
        help="Destination de renvoi (répétable). ⚠️ REMPLACE la liste existante.",
    ),
    no_forward: bool = typer.Option(False, "--no-forward", help="Coupe le renvoi."),
    forward_keep: bool | None = typer.Option(
        None, "--forward-keep/--no-forward-keep",
        help="Conserver une copie dans la boîte (par défaut oui). Sans copie, "
             "une erreur de destination fait perdre le courrier définitivement.",
    ),
    force_password_change: bool = typer.Option(
        False, "--force-password-change",
        help="Impose le changement de mot de passe à la prochaine connexion.",
    ),
) -> None:
    """Modifie une boîte. Seuls les champs fournis sont envoyés."""
    body: dict[str, Any] = {}
    if enabled is not None:
        body["enabled"] = enabled
    if quota_gb is not None:
        body["quota_gb"] = quota_gb
    if comment is not None:
        body["comment"] = comment
    if displayed_name is not None:
        body["displayed_name"] = displayed_name
    if imap is not None:
        body["enable_imap"] = imap
    if pop3 is not None:
        body["enable_pop"] = pop3
    if forward and no_forward:
        rprint("[red]--forward et --no-forward sont exclusifs.[/red]")
        raise typer.Exit(1)
    if forward:
        body["forward_destination"] = list(forward)
        body["forward_enabled"] = True
    if no_forward:
        body["forward_enabled"] = False
    if forward_keep is not None:
        body["forward_keep"] = forward_keep
    if force_password_change:
        body["change_pw_next_login"] = True

    if not body:
        rprint("[yellow]Rien à modifier.[/yellow]")
        raise typer.Exit(1)

    account_id = _account_id(address)
    try:
        a = client.patch(f"{ACCOUNTS_PATH}/{account_id}", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Adresse mise à jour : [bold]{a.get('address', address)}[/bold]")


@account_app.command(name="password")
def reset_account_password(address: str = typer.Argument(..., help="UUID ou adresse")) -> None:
    """Remet le mot de passe d'une boîte.

    Remise et non changement : la plateforme ne connaît pas le mot de passe
    courant. La saisie est masquée, ou lue sur l'entrée standard — jamais en
    argument de commande.
    """
    account_id = _account_id(address)
    password = read_password("Nouveau mot de passe de la boîte")
    try:
        client.post(f"{ACCOUNTS_PATH}/{account_id}/password", json={"password": password})
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Mot de passe remis pour [bold]{address}[/bold].")


@account_app.command(name="delete")
def delete_account(
    address: str = typer.Argument(..., help="UUID ou adresse"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime une adresse ET le contenu de sa boîte. Irréversible."""
    account_id = _account_id(address)
    if not yes and not typer.confirm(
        f"Supprimer l'adresse {address} et tout le courrier qu'elle contient ? "
        "Il n'y a pas de corbeille."
    ):
        raise typer.Abort()
    try:
        client.delete(f"{ACCOUNTS_PATH}/{account_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Adresse supprimée.")


# ---------------------------------------------------------------------------
# Jetons d'application
# ---------------------------------------------------------------------------


@token_app.command(name="list")
def list_tokens(address: str = typer.Argument(..., help="UUID ou adresse")) -> None:
    """Liste les jetons d'application d'une adresse — jamais leur valeur."""
    account_id = _account_id(address)
    try:
        items = client.get(f"{ACCOUNTS_PATH}/{account_id}/tokens")
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": t.get("id"),
            "comment": t.get("comment"),
            "authorized_ips": _fmt_list(t.get("authorized_ips")),
            "created_at": t.get("created_at"),
        }
        for t in items
    ]
    render_list(
        rows,
        title=f"Jetons d'application de {address} ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("comment", "Usage"),
            ("authorized_ips", "IP autorisées"),
            ("created_at", "Créé le"),
        ],
    )


@token_app.command(name="create")
def create_token(
    address: str = typer.Argument(..., help="UUID ou adresse"),
    comment: str | None = typer.Option(
        None, "--comment", help="À quoi sert ce jeton — sans cela, personne n'osera le révoquer."
    ),
    ip: list[str] = typer.Option(
        [], "--ip",
        help="Restreint le jeton à cette plage (répétable), ex. 203.0.113.7/32.",
    ),
) -> None:
    """Crée un jeton d'application et affiche sa valeur — une seule fois.

    Un jeton authentifie un programme (sauvegarde, imprimante, application
    métier) en IMAP/SMTP sans lui confier le mot de passe de la boîte, et se
    révoque isolément. La valeur n'est jamais relisible : recopiez-la maintenant.
    """
    account_id = _account_id(address)
    body: dict[str, Any] = {}
    if comment is not None:
        body["comment"] = comment
    if ip:
        body["authorized_ips"] = list(ip)
    try:
        t = client.post(f"{ACCOUNTS_PATH}/{account_id}/tokens", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Jeton créé pour [bold]{address}[/bold] :")
    rprint(f"[bold cyan]{t.get('token')}[/bold cyan]")
    rprint(
        "[yellow]Recopiez-le maintenant : cette valeur n'est plus lisible ensuite.[/yellow]"
    )
    rprint(
        "[dim]L'identifiant rendu ici est indicatif — avant toute révocation, "
        f"listez : cetic email account token list {address}[/dim]"
    )


@token_app.command(name="revoke")
def revoke_token(
    address: str = typer.Argument(..., help="UUID ou adresse"),
    token_id: str = typer.Argument(..., help="UUID du jeton (cf. token list)"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Révoque un jeton. Le programme qui le porte perd l'accès au prochain appel."""
    account_id = _account_id(address)
    if not yes and not typer.confirm(f"Révoquer le jeton {token_id} de {address} ?"):
        raise typer.Abort()
    try:
        client.delete(f"{ACCOUNTS_PATH}/{account_id}/tokens/{token_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Jeton révoqué.")


# ---------------------------------------------------------------------------
# Alias
# ---------------------------------------------------------------------------


@alias_app.command(name="list")
def list_aliases(
    domain: str | None = typer.Option(
        None, "--domain", help="Ne rendre que les alias de ce domaine (UUID ou nom)."
    ),
) -> None:
    """Liste les alias de l'organisation."""
    params: dict[str, str] = {}
    if domain:
        params["domain_id"] = _domain_id(domain)
    try:
        items = client.get(ALIASES_PATH, params=params or None)
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": a.get("id"),
            "address": a.get("address"),
            "destinations": _fmt_list(a.get("destinations")),
            "wildcard": _yes_no(a.get("wildcard")),
            "comment": a.get("comment"),
        }
        for a in items
    ]
    render_list(
        rows,
        title=f"Alias ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("address", "Alias"),
            ("destinations", "Destinations"),
            ("wildcard", "Attrape-tout"),
            ("comment", "Commentaire"),
        ],
    )


@alias_app.command(name="create")
def create_alias(
    address: str = typer.Argument(
        ...,
        help="Adresse de l'alias, ex. contact@exemple.com. Un attrape-tout "
             "('*@exemple.com') doit être entre guillemets pour le shell.",
    ),
    to: list[str] = typer.Option(
        ..., "--to", help="Destination (répétable). Peut être externe (Gmail, partenaire)."
    ),
    wildcard: bool = typer.Option(
        False, "--wildcard", help="Attrape-tout : capte tout ce qui n'a pas d'adresse propre."
    ),
    comment: str | None = typer.Option(None, "--comment"),
) -> None:
    """Crée un alias — une adresse source, N destinations, aucun stockage."""
    body: dict[str, Any] = {"address": address, "destinations": list(to), "wildcard": wildcard}
    if comment is not None:
        body["comment"] = comment
    try:
        a = client.post(ALIASES_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Alias créé : [bold]{a.get('address', address)}[/bold] → "
        f"{_fmt_list(a.get('destinations'))}"
    )


@alias_app.command(name="update")
def update_alias(
    address: str = typer.Argument(..., help="UUID ou adresse de l'alias"),
    to: list[str] = typer.Option(
        [], "--to", help="Destination (répétable). ⚠️ REMPLACE la liste existante."
    ),
    wildcard: bool | None = typer.Option(None, "--wildcard/--no-wildcard"),
    comment: str | None = typer.Option(None, "--comment"),
) -> None:
    """Modifie un alias. Seuls les champs fournis sont envoyés.

    ⚠️ `--to` REMPLACE les destinations : n'en passer qu'une retire les autres.
    """
    body: dict[str, Any] = {}
    if to:
        body["destinations"] = list(to)
    if wildcard is not None:
        body["wildcard"] = wildcard
    if comment is not None:
        body["comment"] = comment
    if not body:
        rprint("[yellow]Rien à modifier.[/yellow]")
        raise typer.Exit(1)

    alias_id = _alias_id(address)
    try:
        a = client.patch(f"{ALIASES_PATH}/{alias_id}", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Alias mis à jour : [bold]{a.get('address', address)}[/bold]")


@alias_app.command(name="delete")
def delete_alias(
    address: str = typer.Argument(..., help="UUID ou adresse de l'alias"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un alias. Le courrier cesse aussitôt d'être redirigé."""
    alias_id = _alias_id(address)
    if not yes and not typer.confirm(f"Supprimer l'alias {address} ?"):
        raise typer.Abort()
    try:
        client.delete(f"{ALIASES_PATH}/{alias_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Alias supprimé.")
