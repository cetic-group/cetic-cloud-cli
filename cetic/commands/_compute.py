"""Helpers partagés pour les commandes de création compute (vm/container/scale-sets).

Centralise l'option `--cloud-init` (lecture d'un fichier cloud-config envoyé en
`user_data`) et l'enrichissement du body avec les options d'accès optionnelles.
"""
from __future__ import annotations

import re

from pathlib import Path

import typer
from rich import print as rprint


def validate_windows_password(password: str) -> None:
    """Valide la complexité du mot de passe administrateur Windows.

    Politique alignée sur le backend (`apps/api/app/api/v1/vm_instances.py`) :
    ≥ 12 caractères ET ≥ 3 catégories parmi minuscule / majuscule / chiffre /
    symbole. Échoue tôt côté CLI pour un message clair (le backend renvoie sinon
    un 422). Appelé uniquement quand le template est Windows
    (``--windows-license-consent``).
    """
    categories = sum(
        bool(re.search(pattern, password))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^a-zA-Z0-9]")
    )
    if len(password) < 12 or categories < 3:
        rprint(
            "[red]Erreur : mot de passe Windows trop faible. "
            "Il doit faire au moins 12 caractères et couvrir au moins 3 catégories "
            "parmi minuscule, majuscule, chiffre, symbole.[/red]"
        )
        raise typer.Exit(1)


def read_cloud_init(path: Path | None) -> str | None:
    """Lit le fichier cloud-config et renvoie son contenu, ou None si non fourni.

    Typer valide l'existence/lisibilité du fichier (``exists=True``) en amont ;
    on lit simplement le contenu en UTF-8.
    """
    if path is None:
        return None
    return path.read_text(encoding="utf-8")


def apply_compute_access_options(
    body: dict,
    *,
    cloud_init: Path | None,
    bastion_access: bool,
    template_source: bool | None = None,
    windows_license_consent: bool = False,
    docker: bool = False,
) -> None:
    """Ajoute `user_data`/`bastion_access`/`is_template_source`/`windows_license_consent`/`docker` au body si demandés.

    - ``user_data`` n'est ajouté que si un fichier cloud-init est fourni (sinon le
      backend applique ses défauts cloud-init CETIC).
    - ``bastion_access`` n'est ajouté que s'il vaut True (défaut backend = False).
    - ``is_template_source`` n'est ajouté que s'il vaut True. ``None`` =
      l'option n'existe pas pour cette ressource (scale-sets).
    - ``windows_license_consent`` n'est ajouté que s'il vaut True (obligatoire pour
      un template Windows ; défaut backend = False).
    - ``docker`` n'est ajouté que s'il vaut True (active le nesting ; défaut
      backend = False → conteneur durci).
    """
    user_data = read_cloud_init(cloud_init)
    if user_data is not None:
        body["user_data"] = user_data
    if bastion_access:
        body["bastion_access"] = True
    if template_source:
        body["is_template_source"] = True
    if windows_license_consent:
        body["windows_license_consent"] = True
    if docker:
        body["docker"] = True
