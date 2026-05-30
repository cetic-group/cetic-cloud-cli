"""cetic registry — CETIC Container Registry (CCR).

Gère les registries de conteneurs managées : création, comptes utilisateurs,
ACL granulaires (repo × user × actions), navigation des dépôts/tags, login
docker, garbage collection.

Le mot de passe admin est stocké dans le trousseau système (keyring) et n'est
jamais persisté en clair dans la config.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from typing import Any

import typer
from rich import print as rprint

from cetic import client
from cetic._resolve import resolve_id
from cetic._secrets import (
    delete_admin_password,
    get_admin_password,
    offer_save_password,
    prompt_password,
    save_admin_password,
)
from cetic.commands._render import render_list, render_one

REGISTRIES_PATH = "/v1/registries"

app = typer.Typer(help="CETIC Container Registry — registries Docker/OCI managées")
user_app = typer.Typer(help="Comptes utilisateurs additionnels (CI, robots, équipiers)")
acl_app = typer.Typer(help="Listes de contrôle d'accès (user × repo × actions)")
tag_app = typer.Typer(help="Tags d'images (manifests OCI)")
app.add_typer(user_app, name="user")
app.add_typer(acl_app, name="acl")
app.add_typer(tag_app, name="tag")


# ---------------------------------------------------------------------------
# Helpers internes (pas exportés)
# ---------------------------------------------------------------------------

# Clés dont la valeur est considérée sensible et masquée par défaut dans `get`.
SENSITIVE_FIELDS = (
    "admin_password",
    "admin_secret_ref",
    "s3_access_key",
    "s3_secret_key",
    "s3_secret_ref",
    "jwt_signing_key",
    "jwt_secret_ref",
)


def _redact(item: dict[str, Any]) -> dict[str, Any]:
    """Masque les clés sensibles d'une ressource pour affichage."""
    redacted = dict(item)
    for k in list(redacted.keys()):
        if k in SENSITIVE_FIELDS or k.endswith("_secret_ref"):
            if redacted[k]:
                redacted[k] = "***"
    return redacted


def _format_api_error(e: client.APIError) -> str:
    """Localise les erreurs API en messages français lisibles."""
    if e.status_code == 401:
        return "Non authentifié — vérifiez `cetic auth login` ou `CCP_API_KEY`."
    if e.status_code == 403:
        return "Accès refusé — droits insuffisants pour cette opération."
    if e.status_code == 404:
        return "Ressource introuvable."
    if e.status_code == 409:
        # Conflict : peut être un doublon ou un quota max_registries=2 atteint.
        detail = (e.detail or "").lower()
        if "max_registries" in detail or "quota" in detail or "limit" in detail:
            return (
                "Quota atteint — limite de registries dépassée pour ce tenant. "
                "Demandez une augmentation via `cetic quota request`."
            )
        return f"Conflit : {e.detail}"
    if e.status_code >= 500:
        return f"Erreur serveur ({e.status_code}). Réessayez plus tard."
    return e.detail or f"Erreur HTTP {e.status_code}"


def _bail(e: client.APIError) -> typer.Exit:
    """Affiche le message localisé puis renvoie un Exit(1) à propager."""
    rprint(f"[red]Erreur : {_format_api_error(e)}[/red]")
    return typer.Exit(1)


def _resolve_registry(id_or_name: str) -> str:
    return resolve_id(REGISTRIES_PATH, id_or_name)


def _parse_link_header(link: str | None) -> str | None:
    """Extrait l'URL `rel="next"` d'un header Link standard Distribution."""
    if not link:
        return None
    # Link: </v2/_catalog?n=100&last=foo>; rel="next"
    m = re.search(r"<([^>]+)>;\s*rel=\"next\"", link)
    return m.group(1) if m else None


def _parse_kv_tags(values: list[str]) -> dict[str, str]:
    """Parse `--tag KEY=VALUE` répétés en dict."""
    out: dict[str, str] = {}
    for v in values:
        if "=" not in v:
            rprint(f"[red]Tag invalide '{v}' — format attendu KEY=VALUE.[/red]")
            raise typer.Exit(1)
        k, val = v.split("=", 1)
        out[k.strip()] = val.strip()
    return out


# ---------------------------------------------------------------------------
# Top-level CRUD
# ---------------------------------------------------------------------------


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n", help="Nom de la registry (slug)"),
    region: str = typer.Option(..., "--region", "-r"),
    public: bool = typer.Option(
        False, "--public/--no-public",
        help="Expose la registry sur Internet via le Gateway public (défaut: désactivé)",
    ),
    private: bool = typer.Option(
        True, "--private/--no-private",
        help="Expose la registry sur le LAN privé via le Gateway privé (défaut: activé)",
    ),
    image_tag: str | None = typer.Option(
        None, "--image-tag",
        help="Tag de l'image Distribution à déployer (default: laisser le serveur choisir)",
    ),
    tags: list[str] = typer.Option([], "--tag", help="Tag KEY=VALUE (répéter)"),
) -> None:
    """Crée une nouvelle registry (Distribution + auth JWT cesanta).

    L'exposition se choisit via `--public` / `--private` (au moins l'un des
    deux doit être activé). Aucune ressource réseau (VPC/VNet/IP) n'est
    requise — le routage s'appuie sur les Gateways de la plateforme et un
    hostname unique `<slug>-<id8>.registry-<region>.cloud.cetic-group.com`.

    Le mot de passe admin auto-généré est affiché UNE SEULE FOIS et peut être
    sauvegardé dans le trousseau système.
    """
    if not public and not private:
        rprint(
            "[red]Au moins une exposition doit être activée : "
            "utilisez `--public` et/ou `--private`.[/red]"
        )
        raise typer.Exit(1)
    body: dict[str, Any] = {
        "name": name,
        "region": region,
        "expose_public": public,
        "expose_private": private,
    }
    if image_tag:
        body["image_tag"] = image_tag
    if tags:
        body["tags"] = _parse_kv_tags(tags)
    try:
        reg = client.post(REGISTRIES_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Registry créée : [bold]{reg['id']}[/bold] "
        f"({reg.get('url') or '— url en cours de provisionnement'})"
    )
    pwd = reg.get("admin_password")
    admin_user = reg.get("admin_username", "admin")
    if pwd:
        rprint(
            "\n[bold yellow]Mot de passe admin (affiché une seule fois) :[/bold yellow]"
        )
        rprint(f"  utilisateur : [bold]{admin_user}[/bold]")
        rprint(f"  mot de passe: [bold]{pwd}[/bold]")
        offer_save_password(reg["id"], admin_user, pwd)


@app.command()
def update(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    public: bool | None = typer.Option(
        None, "--public/--no-public",
        help="Active/désactive l'exposition publique (toggle à chaud)",
    ),
    private: bool | None = typer.Option(
        None, "--private/--no-private",
        help="Active/désactive l'exposition privée (toggle à chaud)",
    ),
    tags: str | None = typer.Option(
        None, "--tags",
        help="Liste de tags CSV `KEY=VALUE,KEY2=VALUE2` (remplace les tags existants)",
    ),
) -> None:
    """Modifie une registry existante : toggle expose ou édite les tags.

    Au moins un de `--public/--no-public`, `--private/--no-private`, `--tags`
    doit être fourni. Remplace les anciens endpoints `attach-ip` / `detach-ip`.
    """
    if public is None and private is None and tags is None:
        rprint(
            "[red]Aucune modification demandée. Fournissez `--public`/"
            "`--no-public`, `--private`/`--no-private`, ou `--tags`.[/red]"
        )
        raise typer.Exit(1)
    body: dict[str, Any] = {}
    if public is not None:
        body["expose_public"] = public
    if private is not None:
        body["expose_private"] = private
    if tags is not None:
        if tags.strip() == "":
            body["tags"] = {}
        else:
            body["tags"] = _parse_kv_tags(
                [item.strip() for item in tags.split(",") if item.strip()]
            )
    rid = _resolve_registry(id_or_name)
    try:
        reg = client.patch(f"{REGISTRIES_PATH}/{rid}", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Registry mise à jour : [bold]{reg.get('name', rid)}[/bold]")
    render_one(_redact(reg), title=f"Registry {reg.get('name', rid)}")


@app.command(name="attach-ip")
def attach_ip_deprecated(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
) -> None:
    """[DÉPRÉCIÉ] Utilisez `cetic registry update <id> --public`."""
    _ = id_or_name
    rprint(
        "[red]La commande `attach-ip` a été supprimée dans v0.7.0.[/red]\n"
        "Utilisez plutôt :\n"
        f"  [bold]cetic registry update {id_or_name} --public[/bold]"
    )
    raise typer.Exit(2)


@app.command(name="detach-ip")
def detach_ip_deprecated(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
) -> None:
    """[DÉPRÉCIÉ] Utilisez `cetic registry update <id> --no-public`."""
    _ = id_or_name
    rprint(
        "[red]La commande `detach-ip` a été supprimée dans v0.7.0.[/red]\n"
        "Utilisez plutôt :\n"
        f"  [bold]cetic registry update {id_or_name} --no-public[/bold]"
    )
    raise typer.Exit(2)


def _format_expose(reg: dict[str, Any]) -> str:
    """Représentation courte de l'exposition pour les tables."""
    pub = bool(reg.get("expose_public"))
    priv = bool(reg.get("expose_private"))
    if pub and priv:
        return "Internet+Privé"
    if pub:
        return "Internet"
    if priv:
        return "Privé"
    return "—"


@app.command(name="list")
def list_registries(
    region: str | None = typer.Option(None, "--region", "-r"),
) -> None:
    """Liste les registries du tenant."""
    try:
        items = client.get(
            REGISTRIES_PATH, params={"region": region} if region else None
        )
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": r["id"],
            "name": r["name"],
            "region": r.get("region", "—"),
            "expose": _format_expose(r),
            "status": r.get("status", "—"),
            "url": r.get("url") or "—",
            "storage": (
                f"{r['storage_used_gb']} Go" if r.get("storage_used_gb") is not None else "—"
            ),
            "last_push": (r.get("last_push_at") or "—")[:10],
        }
        for r in items
    ]
    render_list(
        rows,
        title=f"Registries ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("name", "Nom"),
            ("region", "Région"),
            ("expose", "Exposition"),
            ("status", "Statut"),
            ("url", "URL"),
            ("storage", "Stockage"),
            ("last_push", "Dernier push"),
        ],
    )


@app.command()
def get(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    reveal_secrets: bool = typer.Option(
        False, "--reveal-secrets",
        help="Révèle les secrets (password admin, clés S3) — masqués par défaut",
    ),
) -> None:
    """Détails d'une registry. Secrets masqués sauf `--reveal-secrets`."""
    rid = _resolve_registry(id_or_name)
    try:
        reg = client.get(f"{REGISTRIES_PATH}/{rid}")
    except client.APIError as e:
        raise _bail(e) from e
    payload = reg if reveal_secrets else _redact(reg)
    render_one(payload, title=f"Registry {reg.get('name', rid)}")


@app.command()
def delete(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime une registry (et tous ses repos/tags). Action irréversible."""
    rid = _resolve_registry(id_or_name)
    if not yes and not typer.confirm(
        f"Supprimer la registry {id_or_name} ? Cette action est irréversible."
    ):
        raise typer.Abort()
    try:
        client.delete(f"{REGISTRIES_PATH}/{rid}")
    except client.APIError as e:
        raise _bail(e) from e
    # Best-effort : supprime aussi le mot de passe stocké localement.
    delete_admin_password(rid, "admin")
    rprint("[green]✓[/green] Registry supprimée.")


# ---------------------------------------------------------------------------
# `registry login` — délègue à docker login via subprocess
# ---------------------------------------------------------------------------


@app.command()
def login(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    username: str = typer.Option(
        "admin", "--username", "-u",
        help="Nom d'utilisateur (default: admin)",
    ),
) -> None:
    """`docker login` sur la registry — utilise le trousseau ou prompt."""
    if shutil.which("docker") is None:
        rprint(
            "[red]docker non trouvé sur le PATH. "
            "Installez Docker, ou définissez DOCKER_HOST pour Podman.[/red]"
        )
        raise typer.Exit(1)
    rid = _resolve_registry(id_or_name)
    try:
        reg = client.get(f"{REGISTRIES_PATH}/{rid}")
    except client.APIError as e:
        raise _bail(e) from e
    # L'URL est de la forme `https://<host>` ; on extrait juste le hostname
    # pour `docker login`.
    url = reg.get("url")
    hostname = url.replace("https://", "").replace("http://", "").rstrip("/") if url else None
    if not hostname:
        rprint("[red]Registry sans URL — provisioning peut-être en cours.[/red]")
        raise typer.Exit(1)
    pwd = get_admin_password(rid, username)
    if pwd is None:
        rprint(f"[yellow]Aucun mot de passe enregistré pour {username}@{hostname}.[/yellow]")
        pwd = prompt_password(f"Mot de passe pour {username}")
    try:
        proc = subprocess.run(
            ["docker", "login", hostname, "-u", username, "--password-stdin"],
            input=pwd,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        rprint("[red]docker introuvable — installation manquante.[/red]")
        raise typer.Exit(1) from None
    if proc.returncode != 0:
        rprint(f"[red]Échec docker login : {proc.stderr.strip() or proc.stdout.strip()}[/red]")
        raise typer.Exit(proc.returncode)
    rprint(f"[green]✓[/green] Connecté à [bold]{hostname}[/bold] en tant que [bold]{username}[/bold].")


# ---------------------------------------------------------------------------
# `registry gc` — déclenche garbage collection
# ---------------------------------------------------------------------------


@app.command()
def gc(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    wait: bool = typer.Option(False, "--wait", help="Attend la fin du GC"),
    poll_interval: float = typer.Option(3.0, "--poll-interval", help="Seconde entre 2 polls"),
    timeout: float = typer.Option(600.0, "--timeout", help="Timeout total (secondes)"),
) -> None:
    """Déclenche un garbage collection manuel (downtime 30-60s typique)."""
    rid = _resolve_registry(id_or_name)
    try:
        job = client.post(f"{REGISTRIES_PATH}/{rid}/garbage-collect")
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] GC déclenché : [bold]{job.get('job_id', '—')}[/bold]")
    if not wait:
        return
    deadline = time.monotonic() + timeout
    job_id = job.get("job_id")
    while time.monotonic() < deadline:
        try:
            status = client.get(f"{REGISTRIES_PATH}/{rid}/garbage-collect/{job_id}")
        except client.APIError as e:
            raise _bail(e) from e
        state = status.get("status", "running")
        if state in ("succeeded", "failed", "completed"):
            color = "green" if state in ("succeeded", "completed") else "red"
            rprint(f"[{color}]GC {state}[/]. Durée: {status.get('duration_seconds', '—')}s")
            return
        time.sleep(poll_interval)
    rprint("[yellow]Timeout — le GC est peut-être encore en cours.[/yellow]")


# ---------------------------------------------------------------------------
# Sub-app `user`
# ---------------------------------------------------------------------------


@user_app.command(name="add")
def user_add(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    username: str = typer.Option(..., "--username", help="Nom d'utilisateur à créer"),
) -> None:
    """Crée un compte utilisateur additionnel. Mot de passe affiché 1×."""
    rid = _resolve_registry(id_or_name)
    try:
        u = client.post(f"{REGISTRIES_PATH}/{rid}/users", json={"username": username})
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Utilisateur créé : [bold]{u['username']}[/bold]")
    pwd = u.get("password")
    if pwd:
        rprint("\n[bold yellow]Mot de passe (à copier maintenant) :[/bold yellow]")
        rprint(f"  [bold]{pwd}[/bold]")
        if typer.confirm("Sauvegarder dans le trousseau système ?", default=False):
            save_admin_password(rid, username, pwd)
            rprint("[green]✓[/green] Mot de passe enregistré.")


@user_app.command(name="list")
def user_list(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
) -> None:
    """Liste les utilisateurs d'une registry."""
    rid = _resolve_registry(id_or_name)
    try:
        items = client.get(f"{REGISTRIES_PATH}/{rid}/users")
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "username": u["username"],
            "kind": u.get("kind", "—"),
            "created": (u.get("created_at") or "—")[:10],
            "last_used": (u.get("last_used_at") or "—")[:10],
        }
        for u in items
    ]
    render_list(
        rows,
        title=f"Utilisateurs ({len(rows)})",
        columns=[
            ("username", "Utilisateur"),
            ("kind", "Type"),
            ("created", "Créé le"),
            ("last_used", "Dernière utilisation"),
        ],
    )


@user_app.command(name="reset")
def user_reset(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    username: str = typer.Argument(..., help="Utilisateur dont le mot de passe est réinitialisé"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Réinitialise le mot de passe d'un utilisateur. Nouveau mdp affiché 1×."""
    rid = _resolve_registry(id_or_name)
    if not yes and not typer.confirm(
        f"Réinitialiser le mot de passe de {username} ?"
    ):
        raise typer.Abort()
    try:
        u = client.post(f"{REGISTRIES_PATH}/{rid}/users/{username}/reset-password")
    except client.APIError as e:
        raise _bail(e) from e
    pwd = u.get("password")
    if pwd:
        rprint(f"[green]✓[/green] Mot de passe réinitialisé pour [bold]{username}[/bold].")
        rprint("\n[bold yellow]Nouveau mot de passe (1×) :[/bold yellow]")
        rprint(f"  [bold]{pwd}[/bold]")
        if typer.confirm("Mettre à jour le trousseau système ?", default=True):
            save_admin_password(rid, username, pwd)


@user_app.command(name="delete")
def user_delete(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    username: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un compte utilisateur."""
    rid = _resolve_registry(id_or_name)
    if not yes and not typer.confirm(f"Supprimer l'utilisateur {username} ?"):
        raise typer.Abort()
    try:
        client.delete(f"{REGISTRIES_PATH}/{rid}/users/{username}")
    except client.APIError as e:
        raise _bail(e) from e
    delete_admin_password(rid, username)
    rprint("[green]✓[/green] Utilisateur supprimé.")


# ---------------------------------------------------------------------------
# Sub-app `acl`
# ---------------------------------------------------------------------------


@acl_app.command(name="set")
def acl_set(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    repo: str = typer.Option(..., "--repo", help="Pattern repo (ex: myapp/*)"),
    actions: str = typer.Option(
        ..., "--actions",
        help="Actions séparées par virgule : pull,push,delete",
    ),
    user: str | None = typer.Option(
        None, "--user", help="Utilisateur ciblé (default: admin)"
    ),
) -> None:
    """Crée ou met à jour une règle ACL (PUT idempotent)."""
    rid = _resolve_registry(id_or_name)
    actions_list = [a.strip() for a in actions.split(",") if a.strip()]
    if not actions_list:
        rprint("[red]--actions ne peut pas être vide.[/red]")
        raise typer.Exit(1)
    body: dict[str, Any] = {"repo": repo, "actions": actions_list}
    if user:
        body["username"] = user
    try:
        a = client.put(f"{REGISTRIES_PATH}/{rid}/acls", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] ACL appliquée : "
        f"[bold]{a.get('username', '—')}[/bold] → {repo} ({','.join(actions_list)})"
    )


@acl_app.command(name="list")
def acl_list(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
) -> None:
    """Liste les ACL d'une registry."""
    rid = _resolve_registry(id_or_name)
    try:
        items = client.get(f"{REGISTRIES_PATH}/{rid}/acls")
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": a["id"],
            "username": a.get("username", "—"),
            "repo": a.get("repo", "—"),
            "actions": ",".join(a.get("actions", [])),
        }
        for a in items
    ]
    render_list(
        rows,
        title=f"ACL ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("username", "Utilisateur"),
            ("repo", "Repo"),
            ("actions", "Actions"),
        ],
    )


@acl_app.command(name="remove")
def acl_remove(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    acl_id: str = typer.Argument(..., help="UUID de la règle ACL"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime une règle ACL."""
    rid = _resolve_registry(id_or_name)
    if not yes and not typer.confirm(f"Supprimer l'ACL {acl_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"{REGISTRIES_PATH}/{rid}/acls/{acl_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] ACL supprimée.")


# ---------------------------------------------------------------------------
# `repos`, `tags`, `tag delete`
# ---------------------------------------------------------------------------


def _fetch_repos_page(rid: str, params: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Récupère une page de repos. Renvoie (items, next_url_path)."""
    # On utilise httpx directement pour récupérer le header `Link`.
    import httpx

    from cetic import config

    url = config.get_api_url().rstrip("/") + f"{REGISTRIES_PATH}/{rid}/repositories"
    headers: dict[str, str] = {}
    token = config.get("api_key")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=30) as c:
        resp = c.get(url, headers=headers, params=params)
    if not resp.is_success:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:  # noqa: BLE001
            detail = resp.text
        raise client.APIError(resp.status_code, detail)
    payload = resp.json()
    items = payload if isinstance(payload, list) else payload.get("repositories", [])
    next_url = _parse_link_header(resp.headers.get("Link"))
    return items, next_url


@app.command()
def repos(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    limit: int = typer.Option(100, "--limit", help="Taille de page (default 100)"),
    all_pages: bool = typer.Option(False, "--all", help="Boucle sur toutes les pages"),
) -> None:
    """Liste les dépôts d'images de la registry (paginé Distribution v2)."""
    rid = _resolve_registry(id_or_name)
    collected: list[dict[str, Any]] = []
    params: dict[str, Any] = {"n": limit}
    try:
        while True:
            items, next_url = _fetch_repos_page(rid, params)
            collected.extend(items)
            if not all_pages or not next_url:
                break
            # Distribution renvoie /v2/_catalog?n=...&last=...; on extrait `last`.
            m = re.search(r"[?&]last=([^&]+)", next_url)
            if not m:
                break
            params = {"n": limit, "last": m.group(1)}
    except client.APIError as e:
        raise _bail(e) from e
    # Normalize : Distribution renvoie souvent juste {"repositories": ["foo/bar"]}
    rows: list[dict[str, Any]] = []
    for it in collected:
        if isinstance(it, str):
            rows.append({"name": it, "tags": "—", "size": "—"})
        else:
            rows.append({
                "name": it.get("name", "—"),
                "tags": str(it.get("tag_count", "—")),
                "size": it.get("size_human") or it.get("size", "—"),
            })
    render_list(
        rows,
        title=f"Repositories ({len(rows)})",
        columns=[("name", "Repo"), ("tags", "Tags"), ("size", "Taille")],
    )


@app.command()
def tags(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    repo: str = typer.Argument(..., help="Nom du dépôt (ex: myapp/api)"),
) -> None:
    """Liste les tags d'un dépôt d'images."""
    rid = _resolve_registry(id_or_name)
    try:
        data = client.get(f"{REGISTRIES_PATH}/{rid}/repositories/{repo}/tags")
    except client.APIError as e:
        raise _bail(e) from e
    items = data if isinstance(data, list) else data.get("tags", [])
    rows: list[dict[str, Any]] = []
    for t in items:
        if isinstance(t, str):
            rows.append({"tag": t, "digest": "—", "pushed": "—", "size": "—"})
        else:
            rows.append({
                "tag": t.get("name", "—"),
                "digest": (t.get("digest") or "—")[:19],
                "pushed": (t.get("pushed_at") or "—")[:10],
                "size": t.get("size_human") or t.get("size", "—"),
            })
    render_list(
        rows,
        title=f"Tags de {repo} ({len(rows)})",
        columns=[
            ("tag", "Tag"),
            ("digest", "Digest"),
            ("pushed", "Poussé le"),
            ("size", "Taille"),
        ],
    )


@tag_app.command(name="delete")
def tag_delete(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    repo: str = typer.Argument(..., help="Nom du dépôt"),
    tag: str = typer.Argument(..., help="Tag à supprimer"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un tag (le blob est récupéré au prochain GC)."""
    rid = _resolve_registry(id_or_name)
    if not yes and not typer.confirm(f"Supprimer le tag {repo}:{tag} ?"):
        raise typer.Abort()
    try:
        client.delete(f"{REGISTRIES_PATH}/{rid}/repositories/{repo}/tags/{tag}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Tag supprimé. Le blob sera recyclé au prochain GC.")
