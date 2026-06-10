"""cetic ssh — ouvre une session SSH sécurisée vers une cible privée.

Flux « zéro clé statique » :
  1. génération d'une paire ed25519 éphémère locale (jetable) ;
  2. signature de la clé publique par l'autorité de certification de la
     plateforme → certificat SSH à durée de vie courte ;
  3. résolution de l'hôte du bastion (explicite ou premier bastion de la
     région) ;
  4. connexion `ssh` à travers le bastion, qui relaie vers la cible privée.

Aucune clé n'est déposée sur la cible : le certificat éphémère est nettoyé en
fin de session.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import typer
from rich import print as rprint

from cetic import client


def _resolve_bastion_host(explicit: str | None) -> str:
    """Résout l'hôte du bastion à utiliser.

    - Si `explicit` est fourni, on l'utilise tel quel.
    - Sinon on prend l'`endpoint_host` du premier bastion disponible
      (priorité aux bastions `running`). Erreur claire si aucun.
    """
    if explicit:
        return explicit

    try:
        bastions = client.get("/v1/bastions")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1) from e

    candidates = [
        b for b in bastions
        if b.get("endpoint_host") and (b.get("status") in (None, "running"))
    ]
    if not candidates:
        # Fallback : n'importe quel bastion avec un hôte, même non-running.
        candidates = [b for b in bastions if b.get("endpoint_host")]

    if not candidates:
        rprint(
            "[red]Aucun bastion disponible.[/red] Créez-en un avec "
            "[cyan]cetic bastion create --name <nom> --region <région> "
            "--vpc <vpc>[/cyan], ou précisez un hôte via [cyan]--bastion[/cyan]."
        )
        raise typer.Exit(1)

    return candidates[0]["endpoint_host"]


def ssh(  # noqa: PLR0913
    target: str = typer.Argument(
        ...,
        metavar="TARGET",
        help="Cible privée à atteindre (nom d'hôte / IP privée / identifiant de "
             "ressource). Résolue côté bastion.",
    ),
    login: str = typer.Option(
        "root", "--login", "-l", help="Utilisateur de connexion sur la cible."
    ),
    bastion: str | None = typer.Option(
        None, "--bastion", "-b",
        help="Hôte du bastion à emprunter. Par défaut, le premier bastion de "
             "votre organisation.",
    ),
    ttl: int = typer.Option(
        300, "--ttl",
        help="Durée de vie du certificat SSH éphémère, en secondes.",
    ),
) -> None:
    """Ouvre une session SSH sécurisée vers une cible privée via le bastion.

    Une paire de clés éphémère est générée localement, signée par la
    plateforme (certificat à courte durée de vie), puis utilisée pour se
    connecter à travers le bastion. Aucune clé statique n'est déployée.

    Exemples :
        cetic ssh 10.0.1.42
        cetic ssh web-01 --login ubuntu --ttl 600
        cetic ssh 10.0.1.42 --bastion bastion.par.cloud.cetic-group.com
    """
    if ttl <= 0:
        rprint("[red]Erreur : --ttl doit être un entier positif.[/red]")
        raise typer.Exit(1)

    ssh_bin = shutil.which("ssh")
    if not ssh_bin:
        rprint("[red]Erreur : le client `ssh` est introuvable dans le PATH.[/red]")
        raise typer.Exit(1)
    keygen_bin = shutil.which("ssh-keygen")
    if not keygen_bin:
        rprint("[red]Erreur : `ssh-keygen` est introuvable dans le PATH.[/red]")
        raise typer.Exit(1)

    tmpdir = tempfile.mkdtemp(prefix="cetic-ssh-")
    try:
        key_path = Path(tmpdir) / "id"
        cert_path = Path(tmpdir) / "id-cert.pub"

        # 1. Paire ed25519 éphémère (sans passphrase).
        gen = subprocess.run(  # noqa: S603
            [
                keygen_bin, "-t", "ed25519", "-N", "", "-q",
                "-C", "cetic-ephemeral",
                "-f", str(key_path),
            ],
            capture_output=True,
            text=True,
        )
        if gen.returncode != 0:
            rprint(
                "[red]Erreur : impossible de générer la clé éphémère.[/red]\n"
                f"[dim]{gen.stderr.strip()}[/dim]"
            )
            raise typer.Exit(1)

        public_key = (key_path.with_suffix(".pub")).read_text(encoding="utf-8").strip()

        # 2. Signature par l'autorité de certification de la plateforme.
        body = {
            "public_key": public_key,
            "target": target,
            "login": login,
            "ttl_seconds": ttl,
        }
        try:
            signed = client.post("/v1/ssh/sign", json=body)
        except client.APIError as e:
            rprint(f"[red]Erreur : signature du certificat refusée — {e.detail}[/red]")
            raise typer.Exit(1) from e

        certificate = signed.get("certificate")
        if not certificate:
            rprint("[red]Erreur : la plateforme n'a pas renvoyé de certificat.[/red]")
            raise typer.Exit(1)
        cert_path.write_text(
            certificate if certificate.endswith("\n") else certificate + "\n",
            encoding="utf-8",
        )

        # 3. Résolution de l'hôte du bastion.
        bastion_host = _resolve_bastion_host(bastion)

        rprint(
            f"[dim]Accès SSH sécurisé vers [cyan]{target}[/cyan] via le bastion "
            f"[cyan]{bastion_host}[/cyan] "
            f"(certificat valable {ttl}s).[/dim]"
        )

        # 4. Connexion via le bastion. La cible est passée en argument de
        #    commande `host=<TARGET>` que le bastion résout côté serveur.
        cmd = [
            ssh_bin,
            # -t : force l'allocation d'un pseudo-terminal pour obtenir un shell
            # interactif à travers le bastion (sans ça, la session n'a pas de PTY
            # et la cible ouvre un shell non-interactif qui se ferme aussitôt).
            "-t",
            "-i", str(key_path),
            "-o", f"CertificateFile={cert_path}",
            "-o", "IdentitiesOnly=yes",
            f"{login}@{bastion_host}",
            f"host={target}",
        ]
        completed = subprocess.run(cmd)  # noqa: S603
        raise typer.Exit(completed.returncode)
    finally:
        # Nettoyage best-effort du matériel cryptographique éphémère.
        shutil.rmtree(tmpdir, ignore_errors=True)
