"""Helpers de rendu — table / json / yaml selon CCP_OUTPUT."""

import re
from typing import Any

from rich import print as rprint
from rich.table import Table

from cetic import config

# UUID complet (8-4-4-4-12). Sert à raccourcir les identifiants UNIQUEMENT
# dans l'affichage des LISTES en table — jamais en JSON/YAML (qui doivent
# rester exploitables par jq/yq/Terraform/scripts), ni dans le détail
# (`render_one`) où l'on veut l'identifiant complet.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _short_for_table(value: Any) -> str:
    """Rend une valeur lisible en table de liste : un UUID est raccourci à
    `xxxxxxxx…`. Les autres valeurs sont rendues telles quelles.
    """
    if value is None:
        return "—"
    s = str(value)
    if _UUID_RE.match(s):
        return s[:8] + "…"
    return s


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
        table.add_row(*[_short_for_table(item.get(k)) for k, _ in columns])
    rprint(table)


def render_one(item: dict[str, Any], *, title: str) -> None:
    """Affiche une ressource détaillée (identifiants complets)."""
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
