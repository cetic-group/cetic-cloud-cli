"""Helpers de formatage pour la sortie console (couleurs Rich)."""
from __future__ import annotations

from typing import Any


def _format_decision(d: dict[str, Any]) -> str:
    """Rendu Rich-friendly d'une `Decision` IAM.

    Mapping couleurs :
      - Allow / *ShortCircuit (= Allow effectif) → vert
      - ExplicitDeny → rouge
      - ImplicitDeny (default deny) → gris
      - autres → blanc neutre

    Le rendu inclut le sid + role_id matché si présents.
    """
    if not isinstance(d, dict):
        return f"[red]Décision invalide : {d!r}[/red]"

    reason = str(d.get("reason", "?"))
    allow = bool(d.get("allow"))
    color = "white"
    if allow or reason in {
        "Allow",
        "OwnerShortCircuit",
        "AdminShortCircuit",
        "MemberShortCircuit",
        "ViewerShortCircuit",
        "ApiKeyScopeShortCircuit",
    }:
        color = "green"
    elif reason == "ExplicitDeny":
        color = "red"
    elif reason == "ImplicitDeny":
        color = "bright_black"

    label = "ALLOW" if allow else "DENY "
    parts = [f"[{color}][bold]{label}[/bold] ({reason})[/{color}]"]

    sid = d.get("matched_statement_sid")
    if sid:
        parts.append(f"[dim]via statement {sid}[/dim]")
    role_id = d.get("matched_role_id")
    if role_id:
        parts.append(f"[dim]role={str(role_id)[:8]}[/dim]")
    return " — ".join(parts)
