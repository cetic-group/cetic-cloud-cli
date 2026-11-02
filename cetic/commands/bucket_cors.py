"""cetic bucket cors — règles CORS d'un bucket (#1806).

Les règles sont gérées par la plateforme : `show` lit, `set` remplace
l'ensemble des règles, `delete` les retire, `test` simule une requête de
navigateur contre les règles enregistrées. Chaque écriture porte la révision lue
juste avant, pour ne jamais écraser une modification faite ailleurs
(console, Terraform, autre poste).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list

cors_app = typer.Typer(help="Règles CORS d'un bucket")

METHODES = ("GET", "PUT", "POST", "DELETE", "HEAD")

# Préréglages : de simples modèles de règle (le moins de privilèges d'abord).
PRESETS: dict[str, dict[str, Any]] = {
    "upload": {"allowed_methods": ["PUT", "POST"], "allowed_headers": ["Content-Type"],
               "expose_headers": ["ETag"], "max_age_seconds": 3600},
    "read": {"allowed_methods": ["GET", "HEAD"], "allowed_headers": [],
             "expose_headers": [], "max_age_seconds": 3600},
    "media": {"allowed_methods": ["GET", "HEAD"], "allowed_headers": ["Range"],
              "expose_headers": ["Content-Range", "Accept-Ranges"], "max_age_seconds": 3600},
    "tool": {"allowed_methods": list(METHODES), "allowed_headers": ["*"],
             "expose_headers": ["ETag"], "max_age_seconds": 3600},
}


def _fail(e: client.APIError) -> None:
    rprint(f"[red]Erreur : {e.detail}[/red]")
    raise typer.Exit(1)


def _split(values: list[str] | None) -> list[str]:
    """Accepte `--x a --x b` comme `--x a,b`."""
    out: list[str] = []
    for v in values or []:
        out.extend(p.strip() for p in v.split(",") if p.strip())
    return out


def _lire_fichier(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        rprint(f"[red]Fichier illisible ({path}) : {e}[/red]")
        raise typer.Exit(1)
    rules = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
        rprint("[red]Le fichier doit contenir une liste de règles (ou {\"rules\": [...]}).[/red]")
        raise typer.Exit(1)
    return rules


def _regle(preset: str | None, origins: list[str], methods: list[str], headers: list[str],
           expose: list[str], max_age: int | None) -> dict[str, Any]:
    if not origins:
        rprint("[red]Précisez au moins une origine (--origin) ou un fichier (--file).[/red]")
        raise typer.Exit(1)
    if preset is not None and preset not in PRESETS:
        rprint(f"[red]Préréglage inconnu « {preset} » (choix : {', '.join(PRESETS)}).[/red]")
        raise typer.Exit(1)
    base = dict(PRESETS[preset]) if preset else {
        "allowed_methods": ["GET"], "allowed_headers": [], "expose_headers": [], "max_age_seconds": 3600,
    }
    if methods:
        bad = [m for m in (x.upper() for x in methods) if m not in METHODES]
        if bad:
            rprint(f"[red]Méthode(s) non prise(s) en charge : {', '.join(bad)} "
                   f"(choix : {', '.join(METHODES)}).[/red]")
            raise typer.Exit(1)
        base["allowed_methods"] = [m.upper() for m in methods]
    if headers:
        base["allowed_headers"] = headers
    if expose:
        base["expose_headers"] = expose
    if max_age is not None:
        base["max_age_seconds"] = max_age
    base["allowed_origins"] = origins
    return base


def _afficher(cfg: dict[str, Any]) -> None:
    rows = [
        {
            "n": i,
            "origins": ", ".join(r.get("allowed_origins", [])),
            "methods": ", ".join(r.get("allowed_methods", [])),
            "headers": ", ".join(r.get("allowed_headers", [])) or "—",
            "max_age": r.get("max_age_seconds"),
        }
        for i, r in enumerate(cfg.get("rules", []), start=1)
    ]
    render_list(rows, title=f"Règles CORS ({len(rows)}) — statut : {cfg.get('status', 'none')}",
                columns=[("n", "#"), ("origins", "Origines"), ("methods", "Méthodes"),
                         ("headers", "En-têtes"), ("max_age", "Durée (s)")])
    if cfg.get("error_message"):
        rprint(f"[yellow]⚠ {cfg['error_message']}[/yellow]")
    if cfg.get("status") == "unmanaged":
        rprint("[dim]Configuration posée hors de la plateforme : un premier `set` la prend en charge.[/dim]")


@cors_app.command("show")
def show(bucket_id: str = typer.Argument(..., help="UUID du bucket")) -> None:
    """Affiche les règles CORS du bucket."""
    try:
        cfg = client.get(f"/v1/buckets/{bucket_id}/cors")
    except client.APIError as e:
        _fail(e)
    _afficher(cfg)


@cors_app.command("set")
def set_(
    bucket_id: str = typer.Argument(..., help="UUID du bucket"),
    file: Path | None = typer.Option(None, "--file", "-f", help="Fichier JSON de règles (remplace tout)"),
    origin: list[str] | None = typer.Option(None, "--origin", "-o", help="Origine autorisée (répétable)"),
    preset: str | None = typer.Option(None, "--preset", "-p", help="upload | read | media | tool"),
    method: list[str] | None = typer.Option(None, "--method", "-m", help="Méthode (répétable)"),
    header: list[str] | None = typer.Option(None, "--header", help="En-tête de requête autorisé (répétable)"),
    expose_header: list[str] | None = typer.Option(None, "--expose-header", help="En-tête exposé (répétable)"),
    max_age: int | None = typer.Option(None, "--max-age", help="Durée de mémorisation (secondes)"),
    add: bool = typer.Option(False, "--add", help="Ajoute la règle aux règles existantes au lieu de les remplacer"),
    force: bool = typer.Option(False, "--force", help="Écrit sans vérifier la révision"),
) -> None:
    """Remplace les règles CORS (ou ajoute une règle avec --add)."""
    try:
        courant = client.get(f"/v1/buckets/{bucket_id}/cors")
    except client.APIError as e:
        _fail(e)
    if file is not None:
        if origin or preset or method or header or expose_header or max_age is not None:
            rprint("[red]--file ne se combine pas avec les options de règle.[/red]")
            raise typer.Exit(1)
        rules = _lire_fichier(file)
    else:
        rule = _regle(preset, _split(origin), _split(method), _split(header),
                      _split(expose_header), max_age)
        rules = (courant.get("rules", []) if add else []) + [rule]
    body: dict[str, Any] = {"rules": rules}
    if not force:
        body["expected_revision"] = courant.get("revision")
    try:
        cfg = client.put(f"/v1/buckets/{bucket_id}/cors", json=body)
    except client.APIError as e:
        if e.status_code == 409:
            rprint(f"[red]{e.detail}[/red]")
            rprint("[dim]Relancez la commande après avoir relu les règles (`cors show`), "
                   "ou utilisez --force.[/dim]")
            raise typer.Exit(1)
        _fail(e)
    rprint("[green]✓[/green] Règles CORS enregistrées.")
    _afficher(cfg)


@cors_app.command("delete")
def delete(
    bucket_id: str = typer.Argument(..., help="UUID du bucket"),
    yes: bool = typer.Option(False, "--yes", "-y"),
    force: bool = typer.Option(False, "--force", help="Supprime sans vérifier la révision"),
) -> None:
    """Supprime toutes les règles CORS du bucket."""
    if not yes and not typer.confirm(f"Supprimer les règles CORS du bucket {bucket_id} ?"):
        raise typer.Abort()
    path = f"/v1/buckets/{bucket_id}/cors"
    try:
        if not force:
            rev = client.get(path).get("revision")
            if rev:
                path += f"?expected_revision={rev}"
        client.delete(path)
    except client.APIError as e:
        _fail(e)
    rprint("[green]✓[/green] Règles CORS supprimées.")


@cors_app.command("test")
def test(
    bucket_id: str = typer.Argument(..., help="UUID du bucket"),
    origin: str = typer.Option(..., "--origin", "-o", help="Origine de la page"),
    method: str = typer.Option("GET", "--method", "-m"),
    header: list[str] | None = typer.Option(None, "--header", help="En-tête de la requête (répétable)"),
) -> None:
    """Simule une requête de navigateur contre les règles enregistrées."""
    try:
        r = client.post(f"/v1/buckets/{bucket_id}/cors/test",
                        json={"origin": origin, "method": method.upper(), "headers": _split(header)})
    except client.APIError as e:
        _fail(e)
    if r.get("allowed"):
        rprint(f"[green]✓ Autorisé[/green] par la règle {r['rule_index'] + 1}.")
    else:
        cause = {"origin": "origine", "method": "méthode", "header": "en-tête"}.get(r.get("reason"), "?")
        extra = f" ({r['header']})" if r.get("header") else ""
        rprint(f"[red]✗ Refusé[/red] — cause : {cause}{extra}.")
    if r.get("ignored_headers"):
        rprint(f"[dim]En-têtes posés par le navigateur, non jugés : {', '.join(r['ignored_headers'])}[/dim]")
    if not r.get("allowed"):
        raise typer.Exit(1)
