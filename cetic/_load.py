"""Helpers de chargement / validation de fichiers (policy JSON, etc.).

Utilisé par `cetic iam roles create/update --policy-file FILE.json`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich import print as rprint

from cetic._iam_arn import parse_arn


_SUPPORTED_EFFECTS = {"Allow", "Deny"}


def _load_policy_file(path: str) -> dict[str, Any]:
    """Lit un fichier JSON de policy et valide la structure minimale.

    Critères :
      - fichier existe et est lisible
      - JSON valide
      - dict avec `statements` (liste non vide)
      - chaque statement a `effect` (`Allow`/`Deny`), `actions` (liste
        non vide), `resources` (liste non vide)
      - chaque ARN dans `resources` parse (sauf `*` wildcard global)

    Retourne le dict prêt à être envoyé en POST. Lève `typer.Exit(1)`
    avec message UX en cas d'erreur de format.

    Note : `version` n'est pas obligatoire côté CLI — l'API la pose
    par défaut à `"2026-05-10"`. Si l'utilisateur la passe, elle est
    transmise telle quelle.
    """
    file = Path(path)
    if not file.is_file():
        rprint(f"[red]Fichier introuvable : {path}[/red]")
        raise typer.Exit(1)
    try:
        raw = file.read_text(encoding="utf-8")
    except OSError as e:
        rprint(f"[red]Impossible de lire {path} : {e}[/red]")
        raise typer.Exit(1) from e
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        rprint(f"[red]JSON invalide ({path}, ligne {e.lineno}) : {e.msg}[/red]")
        raise typer.Exit(1) from e

    if not isinstance(doc, dict):
        rprint(
            f"[red]Le document policy doit être un objet JSON "
            f"(reçu {type(doc).__name__}).[/red]"
        )
        raise typer.Exit(1)
    statements = doc.get("statements")
    if not isinstance(statements, list) or not statements:
        rprint(
            "[red]Champ `statements` manquant ou vide. "
            "Format attendu : { \"statements\": [ {effect, actions, resources}, ... ] }[/red]"
        )
        raise typer.Exit(1)

    for i, stmt in enumerate(statements):
        if not isinstance(stmt, dict):
            rprint(f"[red]statements[{i}] doit être un objet JSON.[/red]")
            raise typer.Exit(1)
        effect = stmt.get("effect")
        if effect not in _SUPPORTED_EFFECTS:
            rprint(
                f"[red]statements[{i}].effect doit être `Allow` ou `Deny` "
                f"(reçu {effect!r}).[/red]"
            )
            raise typer.Exit(1)
        actions = stmt.get("actions")
        if not isinstance(actions, list) or not actions:
            rprint(
                f"[red]statements[{i}].actions doit être une liste non vide "
                f"(ex: [\"registry:Pull\", \"bucket:*\"]).[/red]"
            )
            raise typer.Exit(1)
        for a in actions:
            if not isinstance(a, str) or not a:
                rprint(
                    f"[red]statements[{i}].actions doit contenir des chaînes "
                    f"(reçu {a!r}).[/red]"
                )
                raise typer.Exit(1)
        resources = stmt.get("resources")
        if not isinstance(resources, list) or not resources:
            rprint(
                f"[red]statements[{i}].resources doit être une liste non vide "
                f"(ex: [\"arn:ccp:bucket:*:UUID:*\"] ou [\"*\"]).[/red]"
            )
            raise typer.Exit(1)
        for r in resources:
            if not isinstance(r, str) or not r:
                rprint(
                    f"[red]statements[{i}].resources doit contenir des chaînes "
                    f"(reçu {r!r}).[/red]"
                )
                raise typer.Exit(1)
            if r == "*":
                continue
            try:
                parse_arn(r)
            except ValueError as e:
                rprint(
                    f"[red]statements[{i}].resources : ARN invalide "
                    f"`{r}` — {e}[/red]"
                )
                raise typer.Exit(1) from e

    return doc
