"""Helpers partagés pour les commandes de création compute (vm/container/scale-sets).

Centralise l'option `--cloud-init` (lecture d'un fichier cloud-config envoyé en
`user_data`) et l'enrichissement du body avec les options d'accès optionnelles.
"""
from __future__ import annotations

from pathlib import Path


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
) -> None:
    """Ajoute `user_data`/`bastion_access`/`is_template_source` au body si demandés.

    - ``user_data`` n'est ajouté que si un fichier cloud-init est fourni (sinon le
      backend applique ses défauts cloud-init CETIC).
    - ``bastion_access`` n'est ajouté que s'il vaut True (défaut backend = False).
    - ``is_template_source`` n'est ajouté que s'il vaut True. ``None`` =
      l'option n'existe pas pour cette ressource (scale-sets).
    """
    user_data = read_cloud_init(cloud_init)
    if user_data is not None:
        body["user_data"] = user_data
    if bastion_access:
        body["bastion_access"] = True
    if template_source:
        body["is_template_source"] = True
