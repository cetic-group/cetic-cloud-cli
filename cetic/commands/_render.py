"""Helpers de rendu — table / json / yaml selon CCP_OUTPUT."""

import sys
from typing import Any

from rich.console import Console
from rich.table import Table

from cetic import config


def _cell(value: Any) -> str:
    """Rend une valeur de cellule de table — JAMAIS tronquée.

    Les identifiants (UUID inclus) sont affichés en entier, comme en
    JSON/YAML, pour rester exploitables par copier-coller / scripts.
    """
    if value is None:
        return "—"
    return str(value)


def _console() -> Console:
    """Console Rich qui ne tronque jamais.

    Quand stdout n'est pas un TTY (pipe, redirection, CI), Rich retombe sur
    une largeur de 80 colonnes et tronquerait les cellules. On force alors une
    largeur généreuse. Sur un vrai terminal on garde la largeur détectée et on
    s'appuie sur `overflow="fold"` (wrap multi-lignes) pour ne rien couper.
    """
    if not sys.stdout.isatty():
        return Console(width=200)
    return Console()


def render_list(
    items: list[dict[str, Any]],
    *,
    title: str,
    columns: list[tuple[str, str]],
) -> None:
    """Affiche une liste selon le format configuré.

    `columns` = liste de (clé_dict, label_colonne).
    """
    fmt = config.get_output()
    if fmt == "json":
        import json
        print(json.dumps(items, ensure_ascii=False, indent=2, default=str))
        return
    if fmt == "yaml":
        import yaml
        print(yaml.safe_dump(items, allow_unicode=True, sort_keys=False))
        return

    table = Table(title=title)
    for _, label in columns:
        # Ne jamais tronquer : on wrappe (fold) au lieu de couper avec « … ».
        table.add_column(label, style="white", overflow="fold", no_wrap=False)
    for item in items:
        table.add_row(*[_cell(item.get(k)) for k, _ in columns])
    _console().print(table)


def render_one(item: dict[str, Any], *, title: str) -> None:
    """Affiche une ressource détaillée (identifiants complets)."""
    fmt = config.get_output()
    if fmt == "json":
        import json
        print(json.dumps(item, ensure_ascii=False, indent=2, default=str))
        return
    if fmt == "yaml":
        import yaml
        print(yaml.safe_dump(item, allow_unicode=True, sort_keys=False))
        return

    table = Table(title=title, show_header=False)
    table.add_column("Champ", style="cyan", overflow="fold", no_wrap=False)
    table.add_column("Valeur", style="white", overflow="fold", no_wrap=False)
    for k, v in item.items():
        if isinstance(v, list | dict):
            v = str(v)
        table.add_row(k, "—" if v is None else str(v))
    _console().print(table)


def render_table(
    items: list[dict[str, Any]],
    *,
    columns: list[str],
    title: str = "",
) -> None:
    """Affiche une liste avec colonnes identifiées par leur clé (sans label alternatif)."""
    render_list(items, title=title, columns=[(c, c) for c in columns])
