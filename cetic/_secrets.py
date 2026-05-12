"""Helpers de gestion de secrets via le trousseau système (keyring).

Usage typique pour la registry CCR :
    save_admin_password(registry_id, "admin", pwd)
    pwd = get_admin_password(registry_id, "admin") or prompt_password()
    delete_admin_password(registry_id, "admin")

Le service name est dérivé d'un préfixe constant + ressource pour éviter les
collisions entre familles d'objets (ex : registry vs db credentials).
"""

from __future__ import annotations

import typer
from rich import print as rprint
from rich.prompt import Prompt

# Service names — chaque famille a son propre namespace dans le trousseau.
SERVICE_REGISTRY = "cetic-registry"
SERVICE_SA = "cetic-service-account"


def _service_username(resource_id: str, username: str) -> str:
    return f"{resource_id}:{username}"


def save_admin_password(resource_id: str, username: str, password: str) -> bool:
    """Persiste un mot de passe dans le trousseau système.

    Retourne True si OK, False si l'OS n'a pas de backend dispo.
    """
    try:
        import keyring

        keyring.set_password(SERVICE_REGISTRY, _service_username(resource_id, username), password)
    except Exception as e:  # noqa: BLE001 — keyring expose plusieurs sous-classes
        rprint(f"[yellow]Trousseau indisponible ({e}). Mot de passe non sauvegardé.[/yellow]")
        return False
    return True


def get_admin_password(resource_id: str, username: str) -> str | None:
    """Lit le mot de passe stocké, ou None s'il est absent / inaccessible."""
    try:
        import keyring

        return keyring.get_password(SERVICE_REGISTRY, _service_username(resource_id, username))
    except Exception:  # noqa: BLE001
        return None


def delete_admin_password(resource_id: str, username: str) -> None:
    """Supprime l'entrée du trousseau (silencieux si absente)."""
    try:
        import keyring

        keyring.delete_password(SERVICE_REGISTRY, _service_username(resource_id, username))
    except Exception:  # noqa: BLE001 — silencieux: la registry est déjà détruite
        pass


def prompt_password(label: str = "Mot de passe") -> str:
    """Prompt masqué pour saisie d'un mot de passe."""
    pwd: str = Prompt.ask(label, password=True)
    if not pwd:
        rprint("[red]Mot de passe vide.[/red]")
        raise typer.Exit(1)
    return pwd


def offer_save_password(resource_id: str, username: str, password: str) -> None:
    """Propose interactivement de sauvegarder le mot de passe dans le trousseau."""
    if typer.confirm("Sauvegarder dans le trousseau système ?", default=True):
        if save_admin_password(resource_id, username, password):
            rprint("[green]✓[/green] Mot de passe enregistré dans le trousseau.")


# ─────────────────────────────────────────────────────────────────────────────
# Service Account tokens (IAM v1)
# ─────────────────────────────────────────────────────────────────────────────


def save_sa_token(sa_id: str, token: str) -> bool:
    """Persiste un token de Service Account dans le trousseau système."""
    try:
        import keyring

        keyring.set_password(SERVICE_SA, sa_id, token)
    except Exception as e:  # noqa: BLE001
        rprint(f"[yellow]Trousseau indisponible ({e}). Token non sauvegardé.[/yellow]")
        return False
    return True


def get_sa_token(sa_id: str) -> str | None:
    """Lit le token SA stocké, ou None s'il est absent / inaccessible."""
    try:
        import keyring

        return keyring.get_password(SERVICE_SA, sa_id)
    except Exception:  # noqa: BLE001
        return None


def delete_sa_token(sa_id: str) -> None:
    """Supprime l'entrée SA du trousseau (silencieux si absente)."""
    try:
        import keyring

        keyring.delete_password(SERVICE_SA, sa_id)
    except Exception:  # noqa: BLE001
        pass


def offer_save_sa_token(sa_id: str, token: str) -> None:
    """Propose interactivement de sauvegarder un SA token dans le trousseau."""
    if typer.confirm("Sauvegarder dans le trousseau système ?", default=True):
        if save_sa_token(sa_id, token):
            rprint("[green]✓[/green] Token enregistré dans le trousseau.")
