"""Helpers de rendu — table / json / yaml selon CCP_OUTPUT."""

from typing import Any

from rich import print as rprint
from rich.table import Table

from cetic import config


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
        rprint(json.dumps(items, ensure_ascii=False, indent=2, default=str))
        return
    if fmt == "yaml":
        import yaml
        rprint(yaml.safe_dump(items, allow_unicode=True, sort_keys=False))
        return

    table = Table(title=title)
    for _, label in columns:
        table.add_column(label, style="white")
    for item in items:
        table.add_row(*[str(item.get(k, "—")) for k, _ in columns])
    rprint(table)


def render_one(item: dict[str, Any], *, title: str) -> None:
    """Affiche une ressource détaillée."""
    fmt = config.get_output()
    if fmt == "json":
        import json
        rprint(json.dumps(item, ensure_ascii=False, indent=2, default=str))
        return
    if fmt == "yaml":
        import yaml
        rprint(yaml.safe_dump(item, allow_unicode=True, sort_keys=False))
        return

    table = Table(title=title, show_header=False)
    table.add_column("Champ", style="cyan", no_wrap=True)
    table.add_column("Valeur", style="white")
    for k, v in item.items():
        if isinstance(v, list | dict):
            v = str(v)
        table.add_row(k, "—" if v is None else str(v))
    rprint(table)


def render_table(
    items: list[dict[str, Any]],
    *,
    columns: list[str],
    title: str = "",
) -> None:
    """Affiche une liste avec colonnes identifiées par leur clé (sans label alternatif)."""
    render_list(items, title=title, columns=[(c, c) for c in columns])
