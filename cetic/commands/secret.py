"""cetic secret — Secret Manager CETIC Cloud.

Coffre-fort centralisé pour vos données sensibles (mots de passe, clés API,
certificats, fichiers de configuration). Chiffrement AES-256-GCM au repos.

Un secret est un container agnostic de paires clé/valeur. Il peut être
consommé par n'importe quel outil : CLI, Terraform, application, CRD
CCPSecret côté Kubernetes (qui décide alors du type K8s natif à
synchroniser : Opaque, TLS, dockerconfigjson, basic-auth, ssh-auth).

Sous-commandes :
    cetic secret list
    cetic secret get ID|NAME
    cetic secret create NAME --data key=value --data "cert=@/path/to/file"
    cetic secret update ID|NAME --description "..." --label key=value
    cetic secret rotate ID|NAME --data key=value
    cetic secret value ID|NAME [--key KEY] [--yes]
    cetic secret delete ID|NAME [--yes]

Support du préfixe `@` côté `--data` : la valeur est lue depuis le fichier
(UTF-8 si possible, sinon base64). Exemple :
    cetic secret create my-cert \\
        --data "tls.crt=@/etc/ssl/fullchain.pem" \\
        --data "tls.key=@/etc/ssl/privkey.pem"

Cf. spec figée SPEC_SM.md section 13 (CLI).
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

import typer
from rich import print as rprint

from cetic import client
from cetic._resolve import resolve_id
from cetic.commands._render import render_list, render_one


SECRET_PATH = "/v1/secrets"

# Validation alignée backend / console
MAX_ENTRIES = 100
MAX_PAYLOAD_BYTES = 1024 * 1024  # 1 MiB
KEY_REGEX = re.compile(r"^[a-zA-Z0-9._-]+$")
KEY_MAX_LEN = 253


app = typer.Typer(help="Gestion des secrets CETIC Cloud (Secret Manager)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_api_error(e: client.APIError) -> str:
    if e.status_code == 401:
        return "Non authentifié — vérifiez `cetic auth login` ou `CCP_API_KEY`."
    if e.status_code == 403:
        return "Accès refusé — droits insuffisants (action secrets:*)."
    if e.status_code == 404:
        return "Secret introuvable."
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


def _resolve_secret(id_or_name: str) -> str:
    return resolve_id(SECRET_PATH, id_or_name)


def _read_file_value(path: str) -> str:
    """Lit un fichier et renvoie son contenu en UTF-8 si possible, sinon base64.

    Lève BadParameter si le fichier est introuvable ou illisible.
    """
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        raise typer.BadParameter(f"Fichier introuvable : {path!r}.")
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise typer.BadParameter(f"Impossible de lire {path!r} : {exc}.") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return base64.b64encode(raw).decode("ascii")


def _parse_kv(items: list[str], *, allow_file_ref: bool = False) -> dict[str, str]:
    """Parse une liste de tokens `key=value`.

    Si `allow_file_ref=True`, les valeurs préfixées par `@` sont lues depuis
    le fichier correspondant (UTF-8 ou base64 selon le contenu).

    Lève BadParameter sur format invalide.
    """
    result: dict[str, str] = {}
    for raw in items:
        if "=" not in raw:
            raise typer.BadParameter(
                f"Format invalide '{raw}' — attendu 'key=value'."
            )
        key, _, value = raw.partition("=")
        key = key.strip()
        if not key:
            raise typer.BadParameter(
                f"Clé vide dans '{raw}' — attendu 'key=value' avec une clé non-vide."
            )
        if allow_file_ref and value.startswith("@"):
            file_path = value[1:]
            value = _read_file_value(file_path)
        result[key] = value
    return result


def _validate_data_dict(data: dict[str, str]) -> None:
    """Validation client : limites et regex sur les clés."""
    if not data:
        raise typer.BadParameter("Au moins une paire --data 'key=value' requise.")
    if len(data) > MAX_ENTRIES:
        raise typer.BadParameter(
            f"Limite atteinte : {MAX_ENTRIES} entrées maximum par secret "
            f"(reçu {len(data)})."
        )
    for key in data:
        if not KEY_REGEX.match(key) or len(key) > KEY_MAX_LEN:
            raise typer.BadParameter(
                f"Clé invalide : {key!r}. Lettres, chiffres, points, tirets "
                f"et soulignés uniquement ({KEY_MAX_LEN} caractères max)."
            )
    total = sum(
        len(k.encode("utf-8")) + len(v.encode("utf-8"))
        for k, v in data.items()
    )
    if total > MAX_PAYLOAD_BYTES:
        raise typer.BadParameter(
            f"Taille totale dépassée : {total} octets, maximum "
            f"{MAX_PAYLOAD_BYTES} octets (1 MiB)."
        )


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------


@app.command(name="list")
def list_secrets(
    prefix: str | None = typer.Argument(
        None,
        metavar="[PREFIX]",
        help="Filtre clientside par préfixe path-based (ex : 'prod/db'). "
             "Affiche aussi un résumé des sous-dossiers en tête.",
    ),
) -> None:
    """Liste les secrets de l'organisation courante (sans les valeurs).

    Si un PREFIX est fourni, seuls les secrets dont le nom commence par
    `<prefix>/` (ou est exactement `<prefix>`) sont conservés. Les
    sous-dossiers directs sont résumés en tête (style Vault KV).

    Exemples :
        cetic secret list                  # tous les secrets
        cetic secret list prod             # tout ce qui est sous prod/
        cetic secret list prod/db          # tout ce qui est sous prod/db/
    """
    try:
        items = client.get(SECRET_PATH)
    except client.APIError as e:
        raise _bail(e) from e

    if prefix is not None:
        prefix = prefix.strip().strip("/")

    if prefix:
        prefix_segs = prefix.split("/")
        filtered: list[dict[str, Any]] = []
        folder_counts: dict[str, int] = {}
        for s in items:
            name = s.get("name", "")
            segs = name.split("/")
            # Doit matcher le préfixe (tous les segments du prefix doivent matcher)
            if len(segs) < len(prefix_segs):
                continue
            if any(segs[i] != prefix_segs[i] for i in range(len(prefix_segs))):
                continue
            # Feuille directe (segment unique après le prefix) → liste plate
            if len(segs) == len(prefix_segs) + 1:
                filtered.append(s)
            elif len(segs) > len(prefix_segs) + 1:
                # Sous-dossier → on compte
                folder = "/".join(segs[: len(prefix_segs) + 1])
                folder_counts[folder] = folder_counts.get(folder, 0) + 1
            elif len(segs) == len(prefix_segs):
                # Le secret a EXACTEMENT le même nom que le prefix → on l'affiche
                filtered.append(s)
        items = filtered

        if folder_counts:
            rprint(
                f"[dim]Sous-dossiers de [/dim][bold]{prefix}/[/bold] "
                f"[dim]({len(folder_counts)})[/dim]"
            )
            for folder in sorted(folder_counts):
                count = folder_counts[folder]
                label = "secret" if count == 1 else "secrets"
                rprint(f"  📁 [cyan]{folder}/[/cyan] [dim]({count} {label})[/dim]")
            rprint("")

    rows = [
        {
            "id": s["id"][:8],
            "name": s["name"],
            "version": str(s.get("version", 1)),
            "rotated": (s.get("last_rotated_at") or "—")[:10],
            "created": (s.get("created_at") or "—")[:10],
            "labels": ", ".join(f"{k}={v}" for k, v in (s.get("labels") or {}).items())
                       or "—",
        }
        for s in items
    ]
    title = (
        f"Secrets sous {prefix}/ ({len(rows)})"
        if prefix
        else f"Secrets ({len(rows)})"
    )
    render_list(
        rows,
        title=title,
        columns=[
            ("id", "ID"),
            ("name", "Nom"),
            ("version", "Version"),
            ("rotated", "Rotation"),
            ("created", "Créé"),
            ("labels", "Labels"),
        ],
    )


@app.command()
def get(id_or_name: str = typer.Argument(..., metavar="ID|NAME")) -> None:
    """Détail d'un secret (metadata, sans la valeur)."""
    sid = _resolve_secret(id_or_name)
    try:
        secret = client.get(f"{SECRET_PATH}/{sid}")
    except client.APIError as e:
        raise _bail(e) from e
    # Filtre tout champ legacy k8s_type éventuellement renvoyé par d'anciennes
    # versions backend — le secret CCP n'a plus de type côté producteur.
    secret.pop("k8s_type", None)
    render_one(secret, title=f"Secret {secret.get('name', sid)}")


@app.command()
def create(
    name: str = typer.Argument(
        ...,
        help="Nom du secret. Supporte les chemins path-based style Vault KV "
             "(ex : 'prod/db/credentials'). Chaque segment doit être DNS-friendly "
             "(1-63 chars, lettres minuscules, chiffres, tirets).",
    ),
    data: list[str] = typer.Option(
        ..., "--data", "-d",
        help="Paire 'key=value' (répétable, au moins une). Préfixez la valeur "
             "par '@' pour la lire depuis un fichier "
             "(ex : --data 'cert=@/path/to/cert.pem').",
    ),
    description: str | None = typer.Option(None, "--description", help="Description courte"),
    label: list[str] = typer.Option(
        [], "--label", "-l",
        help="Label 'key=value' (répétable, optionnel).",
    ),
) -> None:
    """Crée un nouveau secret (coffre-fort de paires clé/valeur).

    Les valeurs doivent être passées en clair via `--data`. Elles seront
    chiffrées au repos côté API (AES-256-GCM). Pour charger une valeur
    depuis un fichier, préfixez-la par `@`.

    Exemples :
        cetic secret create db-prod \\
            --data password=p@ssw0rd \\
            --data username=admin \\
            --description "Base de données prod" \\
            --label env=prod

        cetic secret create my-cert \\
            --data "tls.crt=@/etc/ssl/fullchain.pem" \\
            --data "tls.key=@/etc/ssl/privkey.pem" \\
            --label app=web
    """
    data_dict = _parse_kv(data, allow_file_ref=True)
    _validate_data_dict(data_dict)
    labels = _parse_kv(label) if label else {}

    body: dict[str, Any] = {
        "name": name,
        "data": data_dict,
        "labels": labels,
    }
    if description is not None:
        body["description"] = description

    try:
        secret = client.post(SECRET_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Secret créé : [bold]{secret['name']}[/bold] "
        f"([dim]{secret['id']}[/dim], v{secret.get('version', 1)})"
    )


@app.command()
def update(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    description: str | None = typer.Option(None, "--description", help="Nouvelle description"),
    label: list[str] = typer.Option(
        [], "--label", "-l",
        help="Remplace les labels (répétable). Sans cet argument, labels inchangés.",
    ),
) -> None:
    """Modifie les metadata d'un secret (description, labels).

    Pour rotater la valeur, utilisez `cetic secret rotate`.
    """
    sid = _resolve_secret(id_or_name)
    body: dict[str, Any] = {}
    if description is not None:
        body["description"] = description if description else None
    if label:
        body["labels"] = _parse_kv(label)
    if not body:
        rprint("[yellow]Rien à modifier (passez --description ou --label).[/yellow]")
        raise typer.Exit(0)
    try:
        secret = client.patch(f"{SECRET_PATH}/{sid}", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Secret [bold]{secret.get('name', sid)}[/bold] mis à jour.")


@app.command()
def rotate(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    data: list[str] = typer.Option(
        ..., "--data", "-d",
        help="Nouvelle valeur sous forme de paires 'key=value' (répétable). "
             "Le préfixe '@' charge la valeur depuis un fichier.",
    ),
) -> None:
    """Rotate la valeur d'un secret (bump version, ancienne valeur perdue).

    L'opération est enregistrée dans le journal d'audit IAM.
    """
    sid = _resolve_secret(id_or_name)
    data_dict = _parse_kv(data, allow_file_ref=True)
    _validate_data_dict(data_dict)
    try:
        secret = client.post(f"{SECRET_PATH}/{sid}/rotate", json={"data": data_dict})
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Secret [bold]{secret.get('name', sid)}[/bold] rotaté "
        f"(version v{secret.get('version', '?')})."
    )


@app.command()
def value(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    key: str | None = typer.Option(
        None, "--key", "-k",
        help="Affiche uniquement la valeur de cette clé (utile pour les scripts).",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip la confirmation."),
) -> None:
    """Révèle la valeur d'un secret en clair.

    ⚠ Cette commande affiche le secret en clair. L'opération est enregistrée
    dans le journal d'audit IAM côté serveur.

    Sans `--key`, toutes les clés sont affichées (format respecte CCP_OUTPUT).
    Avec `--key FOO`, seule la valeur de FOO est imprimée sur stdout — pratique
    pour des scripts :
        export DB_PASSWORD=$(cetic secret value db-prod --key password --yes)
    """
    sid = _resolve_secret(id_or_name)
    if not yes:
        rprint(
            "[yellow]⚠ Cette commande affiche le secret en clair.[/yellow]"
        )
        if not typer.confirm("Continuer ?"):
            raise typer.Abort()
    try:
        secret = client.get(f"{SECRET_PATH}/{sid}/value")
    except client.APIError as e:
        raise _bail(e) from e
    data: dict[str, str] = secret.get("data", {})

    if key is not None:
        if key not in data:
            rprint(
                f"[red]Clé '{key}' absente du secret. "
                f"Clés disponibles : {sorted(data)}[/red]"
            )
            raise typer.Exit(1)
        # Output brut sur stdout pour usage scripté — pas de rich, pas de wrapping.
        print(data[key])  # noqa: T201
        return

    render_one(
        {
            "id": secret.get("id"),
            "name": secret.get("name"),
            "version": secret.get("version"),
            "data": data,
        },
        title=f"Valeur du secret {secret.get('name', sid)}",
    )


@app.command()
def delete(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip la confirmation."),
) -> None:
    """Supprime un secret (irréversible).

    Tous les consommateurs (CLI, Terraform, applications, CRD CCPSecret côté
    Kubernetes) échoueront à la prochaine lecture.
    """
    sid = _resolve_secret(id_or_name)
    if not yes and not typer.confirm(
        f"Supprimer le secret {id_or_name} ? Cette action est irréversible."
    ):
        raise typer.Abort()
    try:
        client.delete(f"{SECRET_PATH}/{sid}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Secret supprimé.")


@app.command(name="delete-folder")
def delete_folder(
    prefix: str = typer.Argument(
        ...,
        help=(
            "Préfixe du dossier (ex : 'prod/db'). Supprime la feuille "
            "`prefix` (si elle existe) ET tout le sous-arbre `prefix/...`."
        ),
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip la confirmation."),
) -> None:
    """Supprime un dossier KV et tous les secrets qu'il contient (récursif).

    Sémantique alignée sur `vault kv delete -recurse <prefix>` : la feuille
    exacte `<prefix>` ET tous les secrets sous `<prefix>/...` sont
    supprimés en une seule transaction côté serveur (atomique). Chaque
    suppression individuelle est enregistrée dans le journal d'audit IAM
    avec `bulk_prefix=<prefix>` en métadonnée.

    Cette action est IRRÉVERSIBLE. Toutes les applications consommatrices
    verront leur prochaine lecture échouer.

    Exemples :
        cetic secret delete-folder prod/db
        cetic secret delete-folder staging --yes
    """
    normalized = prefix.strip().strip("/")
    if not normalized:
        rprint("[red]Erreur : préfixe vide.[/red]")
        raise typer.Exit(1)

    # Étape 1 — preview clientside (liste les secrets matchés)
    try:
        all_secrets = client.get(SECRET_PATH)
    except client.APIError as e:
        raise _bail(e) from e

    matched = [
        s for s in all_secrets
        if s.get("name") == normalized
        or s.get("name", "").startswith(f"{normalized}/")
    ]

    if not matched:
        rprint(f"[yellow]Aucun secret sous '{normalized}'.[/yellow]")
        raise typer.Exit(0)

    rprint(
        f"[yellow]⚠ {len(matched)} secret(s) vont être supprimés "
        f"sous [bold]{normalized}/[/bold] :[/yellow]"
    )
    for s in matched[:10]:
        rprint(f"  [red]-[/red] [cyan]{s['name']}[/cyan]")
    if len(matched) > 10:
        rprint(f"  [dim]... et {len(matched) - 10} autres[/dim]")

    if not yes and not typer.confirm(
        "Confirmer la suppression du dossier ? Cette action est irréversible.",
    ):
        raise typer.Abort()

    # Étape 2 — appel API (atomique côté backend)
    from urllib.parse import quote
    try:
        result = client.delete(
            f"{SECRET_PATH}?prefix={quote(normalized, safe='/')}",
        )
    except client.APIError as e:
        raise _bail(e) from e

    deleted = result.get("deleted_count", 0)
    rprint(
        f"[green]✓[/green] [bold]{deleted}[/bold] secret(s) supprimé(s) "
        f"sous '{normalized}/'."
    )
    if result.get("truncated"):
        rprint(
            "[dim]Note : la réponse a tronqué la liste des noms à 100 "
            "entrées (suppression bien complète).[/dim]"
        )
