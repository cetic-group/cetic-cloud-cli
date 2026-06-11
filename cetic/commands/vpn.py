"""cetic vpn — VPN CETIC Cloud (accès privé client → ressources internes).

Le VPN CETIC Cloud fournit un accès privé chiffré depuis votre poste vers les
ressources de vos VPC (VM, instances, services), sans les exposer sur Internet.
Une passerelle VPN (« gateway ») est déployée par organisation et couvre un ou
plusieurs VPC ; vous y déclarez des « peers » (un par poste/utilisateur).

Deux modèles de clé pour les peers :

* **Souverain (défaut)** — la CLI génère la paire de clés *localement* ; la clé
  privée ne quitte jamais votre poste. Seule la clé publique est envoyée à la
  plateforme. Le fichier ``<nom>.conf`` est écrit avec votre clé privée locale.
* **Géré (`--managed`)** — la plateforme génère la paire et vous renvoie la
  configuration complète (one-click). Pratique pour démarrer vite ; la clé
  privée est alors connue de la plateforme (sauf `--no-store`).

Un peer peut aussi être un site-à-site (`peer add --site CIDR[,CIDR...]`) : la
config produite est destinée au routeur/pare-feu distant d'un site, qui relie un
réseau entier au VPN (au lieu d'un poste isolé).

Sous-commandes :
    cetic vpn gateway create/list/get/delete
    cetic vpn peer add/list/rm
    cetic vpn config GATEWAY PEER_ID
    cetic vpn rotate GATEWAY PEER_ID
    cetic vpn policy get/set GATEWAY
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one


VPN_PATH = "/v1/vpn/gateways"

# Placeholder posé par l'API dans la config Model A (clé privée jamais transmise).
LOCAL_KEY_PLACEHOLDER = "__INJECT_LOCAL_PRIVATE_KEY__"


app = typer.Typer(help="VPN CETIC Cloud (accès privé chiffré)")
gateway_app = typer.Typer(help="Passerelles VPN (gateways)")
peer_app = typer.Typer(help="Peers VPN (un par poste/utilisateur)")
policy_app = typer.Typer(help="Politique d'accès de la passerelle VPN")

app.add_typer(gateway_app, name="gateway")
app.add_typer(peer_app, name="peer")
app.add_typer(policy_app, name="policy")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_api_error(e: client.APIError) -> str:
    if e.status_code == 401:
        return "Non authentifié — vérifiez `cetic auth login` ou `CCP_API_KEY`."
    if e.status_code == 403:
        return "Accès refusé — droits insuffisants pour gérer le VPN."
    if e.status_code == 404:
        return "Ressource VPN introuvable."
    if e.status_code == 409:
        return f"Conflit : {e.detail}"
    if e.status_code == 410:
        return f"Indisponible : {e.detail}"
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


def _generate_keypair() -> tuple[str, str]:
    """Génère une paire de clés locale (private_b64, public_b64).

    Clamping Curve25519 standard — identique au comportement de la plateforme
    (cf. backend vpn_keys.generate_keypair).
    """
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    raw = bytearray(os.urandom(32))
    raw[0] &= 248
    raw[31] &= 127
    raw[31] |= 64
    priv = X25519PrivateKey.from_private_bytes(bytes(raw))
    pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(bytes(raw)).decode(), base64.b64encode(pub).decode()


def _write_conf(name: str, config: str) -> Path:
    """Écrit la config VPN dans <name>.conf (mode 0600) et retourne le chemin."""
    path = Path(f"{name}.conf")
    # Crée le fichier avec des permissions restrictives dès l'ouverture.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(config)
    finally:
        # Garantit 0600 même si le fichier préexistait avec d'autres droits.
        os.chmod(str(path), 0o600)
    return path


def _parse_site_cidrs(values: list[str]) -> list[str]:
    """Aplatit une liste d'options --site (répétables ou séparées par virgule)."""
    cidrs: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                cidrs.append(part)
    return cidrs


def _print_usage_hint(path: Path, peer_type: str) -> None:
    """Explique à l'utilisateur quoi faire du fichier .conf selon le type de peer.

    * client : à importer dans l'application WireGuard officielle (poste/mobile).
    * site   : à déployer sur le routeur/pare-feu distant (site-à-site).

    NB : « WireGuard » est nommé ici à dessein — c'est le format de l'artefact
    et le nom de l'application cliente à utiliser.
    """
    if peer_type == "site":
        rprint(
            f"  [bold]Site-à-site :[/bold] déployez [cyan]{path}[/cyan] sur votre "
            "routeur/pare-feu distant compatible WireGuard (l'extrémité du site)."
        )
        rprint(
            "  Sur cet équipement, activez le routage IP (IP forwarding) et "
            "routez votre LAN / VNet à travers le tunnel."
        )
    else:
        rprint(
            f"  Importez [cyan]{path}[/cyan] dans l'application [bold]WireGuard[/bold] "
            "(Windows / macOS / Linux / iOS / Android) pour vous connecter à votre VPN privé."
        )
        rprint(
            "  Téléchargez l'application : "
            "[link=https://www.wireguard.com/install/]https://www.wireguard.com/install/[/link]"
        )


# ---------------------------------------------------------------------------
# Gateways
# ---------------------------------------------------------------------------


@gateway_app.command(name="create")
def gateway_create(
    name: str = typer.Option(..., "--name", "-n", help="Nom de la passerelle VPN."),
    region: str = typer.Option(..., "--region", "-r", help="Région (ex : RNN, PAR, ABJ)."),
    vpc: list[str] = typer.Option(
        ..., "--vpc", help="ID de VPC à couvrir (répétable, 1 à 5)."
    ),
    plan: str = typer.Option(
        "small", "--plan", help="Plan de dimensionnement : small | medium | large."
    ),
    public_ip: str | None = typer.Option(
        None, "--public-ip", help="ID d'une IP publique existante à utiliser (sinon auto)."
    ),
    dns: str | None = typer.Option(
        None, "--dns", help="Serveur DNS interne servi aux clients (split-DNS)."
    ),
    pool_cidr: str | None = typer.Option(
        None, "--pool-cidr", help="Plage d'adresses privées allouée aux peers (RFC1918)."
    ),
    tags: list[str] = typer.Option(
        None, "--tags", help="Étiquette (répétable)."
    ),
) -> None:
    """Crée une passerelle VPN couvrant un ou plusieurs VPC."""
    body: dict[str, Any] = {
        "name": name,
        "region": region,
        "vpc_ids": vpc,
        "plan": plan,
    }
    if public_ip is not None:
        body["public_ip_id"] = public_ip
    if dns is not None:
        body["dns"] = dns
    if pool_cidr is not None:
        body["peer_pool_cidr"] = pool_cidr
    if tags:
        body["tags"] = tags

    try:
        gw = client.post(VPN_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Passerelle VPN créée : [bold]{gw['name']}[/bold] "
        f"([dim]{gw['id']}[/dim], statut {gw.get('status', '?')})"
    )
    endpoint = gw.get("endpoint_host")
    if endpoint:
        rprint(f"  Point d'accès : [cyan]{endpoint}[/cyan]:{gw.get('endpoint_port', 51820)}")


@gateway_app.command(name="list")
def gateway_list() -> None:
    """Liste les passerelles VPN de l'organisation courante."""
    try:
        items = client.get(VPN_PATH)
    except client.APIError as e:
        raise _bail(e) from e
    render_list(
        items,
        title=f"Passerelles VPN ({len(items)})",
        columns=[
            ("id", "ID"),
            ("name", "Nom"),
            ("region", "Région"),
            ("plan", "Plan"),
            ("status", "Statut"),
            ("endpoint_host", "Point d'accès"),
        ],
    )


@gateway_app.command(name="get")
def gateway_get(
    gateway_id: str = typer.Argument(..., metavar="ID", help="ID de la passerelle VPN."),
) -> None:
    """Détail d'une passerelle VPN."""
    try:
        gw = client.get(f"{VPN_PATH}/{gateway_id}")
    except client.APIError as e:
        raise _bail(e) from e
    render_one(gw, title=f"Passerelle VPN {gw.get('name', gateway_id)}")


@gateway_app.command(name="delete")
def gateway_delete(
    gateway_id: str = typer.Argument(..., metavar="ID", help="ID de la passerelle VPN."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip la confirmation."),
) -> None:
    """Supprime une passerelle VPN (irréversible)."""
    if not yes and not typer.confirm(
        f"Supprimer la passerelle VPN {gateway_id} ? Cette action est irréversible."
    ):
        raise typer.Abort()
    try:
        client.delete(f"{VPN_PATH}/{gateway_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Passerelle VPN supprimée.")


# ---------------------------------------------------------------------------
# Peers
# ---------------------------------------------------------------------------


@peer_app.command(name="add")
def peer_add(
    gateway_id: str = typer.Argument(..., metavar="GATEWAY", help="ID de la passerelle VPN."),
    name: str = typer.Argument(..., metavar="NAME", help="Nom du peer (ex : alice-laptop)."),
    managed: bool = typer.Option(
        False,
        "--managed",
        help="Mode géré : la plateforme génère la clé (one-click). "
        "Par défaut la clé est générée localement (souverain).",
    ),
    no_store: bool = typer.Option(
        False,
        "--no-store",
        help="(mode --managed) Ne pas conserver la clé privée côté plateforme.",
    ),
    one_time: bool = typer.Option(
        False,
        "--one-time",
        help="(mode --managed) Configuration téléchargeable une seule fois.",
    ),
    site: list[str] = typer.Option(
        None,
        "--site",
        help="Peer site-à-site : un ou plusieurs réseaux distants (CIDR) à "
        "rendre accessibles via ce peer. Répétable ou séparé par des virgules "
        "(ex : 192.168.10.0/24,192.168.20.0/24). La config produite est "
        "destinée au routeur/pare-feu distant, pas à un poste.",
    ),
) -> None:
    """Ajoute un peer à une passerelle VPN et écrit <NAME>.conf (mode 0600).

    En mode souverain (défaut), la clé privée est générée localement et injectée
    dans le fichier ; la plateforme ne la connaît jamais. En mode `--managed`,
    la plateforme génère la paire et renvoie la configuration complète.

    Avec `--site`, le peer est de type site-à-site : la config produite est
    destinée à un routeur/pare-feu distant qui relie un réseau entier au VPN.
    """
    body: dict[str, Any] = {"name": name}
    local_private_key: str | None = None

    site_cidrs = _parse_site_cidrs(site) if site else []
    if site_cidrs:
        body["peer_type"] = "site"
        body["site_cidrs"] = site_cidrs

    if managed:
        # Model B : la plateforme génère la paire. Pas de public_key envoyée.
        body["store_private_key"] = not no_store
        body["one_time"] = one_time
    else:
        # Model A : génération locale, on n'envoie que la clé publique.
        if no_store or one_time:
            rprint(
                "[red]Erreur : --no-store et --one-time ne s'appliquent qu'au mode "
                "--managed.[/red]"
            )
            raise typer.Exit(1)
        local_private_key, public_key = _generate_keypair()
        body["public_key"] = public_key

    try:
        peer = client.post(f"{VPN_PATH}/{gateway_id}/peers", json=body)
    except client.APIError as e:
        raise _bail(e) from e

    config = peer.get("config")
    if not config:
        rprint(
            f"[green]✓[/green] Peer créé : [bold]{peer['name']}[/bold] "
            f"([dim]{peer['id']}[/dim]) — aucune configuration retournée."
        )
        return

    if local_private_key is not None:
        config = config.replace(LOCAL_KEY_PLACEHOLDER, local_private_key)

    path = _write_conf(name, config)
    rprint(
        f"[green]✓[/green] Peer créé : [bold]{peer['name']}[/bold] "
        f"([dim]{peer['id']}[/dim], IP {peer.get('ip', '?')})"
    )
    rprint(f"  Configuration écrite : [cyan]{path}[/cyan] (mode 0600)")
    if managed and one_time:
        rprint(
            "  [yellow]Téléchargement unique : conservez ce fichier, il ne pourra "
            "plus être re-téléchargé.[/yellow]"
        )
    peer_type = peer.get("peer_type") or ("site" if site_cidrs else "client")
    _print_usage_hint(path, peer_type)


@peer_app.command(name="list")
def peer_list(
    gateway_id: str = typer.Argument(..., metavar="GATEWAY", help="ID de la passerelle VPN."),
) -> None:
    """Liste les peers d'une passerelle VPN."""
    try:
        items = client.get(f"{VPN_PATH}/{gateway_id}/peers")
    except client.APIError as e:
        raise _bail(e) from e
    render_list(
        items,
        title=f"Peers VPN ({len(items)})",
        columns=[
            ("id", "ID"),
            ("name", "Nom"),
            ("ip", "IP"),
            ("model", "Modèle"),
            ("last_handshake_at", "Dernière connexion"),
        ],
    )


@peer_app.command(name="rm")
def peer_rm(
    gateway_id: str = typer.Argument(..., metavar="GATEWAY", help="ID de la passerelle VPN."),
    peer_id: str = typer.Argument(..., metavar="PEER_ID", help="ID du peer."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip la confirmation."),
) -> None:
    """Retire un peer d'une passerelle VPN (irréversible)."""
    if not yes and not typer.confirm(
        f"Retirer le peer {peer_id} ? Cette action est irréversible."
    ):
        raise typer.Abort()
    try:
        client.delete(f"{VPN_PATH}/{gateway_id}/peers/{peer_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Peer retiré.")


# ---------------------------------------------------------------------------
# config (re-download) — commande de niveau vpn
# ---------------------------------------------------------------------------


@app.command(name="config")
def config_download(
    gateway_id: str = typer.Argument(..., metavar="GATEWAY", help="ID de la passerelle VPN."),
    peer_id: str = typer.Argument(..., metavar="PEER_ID", help="ID du peer."),
    name: str | None = typer.Option(
        None, "--name", help="Nom du fichier .conf (sinon <peer_id>)."
    ),
) -> None:
    """Re-télécharge la configuration d'un peer (mode géré conservé uniquement).

    En mode souverain (clé générée localement) ou `--no-store`, la plateforme
    ne connaît pas la clé privée : il faut alors utiliser `cetic vpn rotate`.
    """
    try:
        resp = client.get(f"{VPN_PATH}/{gateway_id}/peers/{peer_id}/config")
    except client.APIError as e:
        raise _bail(e) from e

    config = resp.get("config")
    if not config:
        rprint("[red]Erreur : aucune configuration retournée.[/red]")
        raise typer.Exit(1)

    out_name = name or peer_id
    path = _write_conf(out_name, config)
    rprint(f"[green]✓[/green] Configuration écrite : [cyan]{path}[/cyan] (mode 0600)")
    _print_usage_hint(path, resp.get("peer_type") or "client")


# ---------------------------------------------------------------------------
# rotate — commande de niveau vpn
# ---------------------------------------------------------------------------


@app.command(name="rotate")
def rotate(
    gateway_id: str = typer.Argument(..., metavar="GATEWAY", help="ID de la passerelle VPN."),
    peer_id: str = typer.Argument(..., metavar="PEER_ID", help="ID du peer."),
    managed: bool = typer.Option(
        False,
        "--managed",
        help="Le peer est en mode géré : la plateforme régénère la clé. "
        "Par défaut, rotation souveraine (clé régénérée localement).",
    ),
    name: str | None = typer.Option(
        None, "--name", help="Nom du fichier .conf (sinon <peer_id>)."
    ),
) -> None:
    """Renouvelle la clé d'un peer et réécrit sa configuration.

    Mode souverain (défaut) : régénère la paire localement et envoie la nouvelle
    clé publique. Mode `--managed` : la plateforme régénère la paire.
    """
    body: dict[str, Any] = {}
    local_private_key: str | None = None

    if not managed:
        local_private_key, public_key = _generate_keypair()
        body["public_key"] = public_key

    try:
        resp = client.post(f"{VPN_PATH}/{gateway_id}/peers/{peer_id}/rotate", json=body)
    except client.APIError as e:
        raise _bail(e) from e

    config = resp.get("config")
    if not config:
        rprint("[red]Erreur : aucune configuration retournée.[/red]")
        raise typer.Exit(1)

    if local_private_key is not None:
        config = config.replace(LOCAL_KEY_PLACEHOLDER, local_private_key)

    out_name = name or peer_id
    path = _write_conf(out_name, config)
    rprint(
        f"[green]✓[/green] Clé renouvelée. Configuration écrite : "
        f"[cyan]{path}[/cyan] (mode 0600)"
    )
    _print_usage_hint(path, resp.get("peer_type") or "client")


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------


@policy_app.command(name="get")
def policy_get(
    gateway_id: str = typer.Argument(..., metavar="GATEWAY", help="ID de la passerelle VPN."),
) -> None:
    """Affiche la politique d'accès d'une passerelle VPN (JSON)."""
    try:
        doc = client.get(f"{VPN_PATH}/{gateway_id}/policy")
    except client.APIError as e:
        raise _bail(e) from e
    import json

    # Sortie JSON brute exploitable (>> fichier, jq, etc.).
    print(json.dumps(doc, ensure_ascii=False, indent=2))  # noqa: T201


@policy_app.command(name="set")
def policy_set(
    gateway_id: str = typer.Argument(..., metavar="GATEWAY", help="ID de la passerelle VPN."),
    file: str | None = typer.Option(
        None, "--file", "-f", help="Fichier JSON de politique (sinon lecture sur stdin)."
    ),
) -> None:
    """Remplace la politique d'accès d'une passerelle VPN.

    La politique (groupes + règles, format JSON Rule Builder) est lue depuis un
    fichier (`--file`) ou sur l'entrée standard.
    """
    import json

    if file is not None:
        path = Path(file)
        if not path.is_file():
            rprint(f"[red]Fichier introuvable : {file}[/red]")
            raise typer.Exit(1)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            rprint(f"[red]Impossible de lire {file} : {e}[/red]")
            raise typer.Exit(1) from e
    else:
        raw = sys.stdin.read()

    if not raw.strip():
        rprint("[red]Erreur : aucune politique fournie (fichier vide ou stdin vide).[/red]")
        raise typer.Exit(1)

    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        rprint(f"[red]JSON invalide (ligne {e.lineno}) : {e.msg}[/red]")
        raise typer.Exit(1) from e

    if not isinstance(doc, dict):
        rprint(
            f"[red]La politique doit être un objet JSON "
            f"(reçu {type(doc).__name__}).[/red]"
        )
        raise typer.Exit(1)

    body: dict[str, Any] = {
        "groups": doc.get("groups", {}),
        "rules": doc.get("rules", []),
    }
    try:
        updated = client.put(f"{VPN_PATH}/{gateway_id}/policy", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rules = updated.get("rules", []) if isinstance(updated, dict) else []
    rprint(
        f"[green]✓[/green] Politique d'accès mise à jour ({len(rules)} règle(s))."
    )
