"""cetic ssh / cetic scp — accès et transfert sécurisés vers une cible privée.

Flux « zéro clé statique » (commun aux deux commandes) :
  1. génération d'une paire ed25519 éphémère locale (jetable) ;
  2. signature de la clé publique par l'autorité de certification de la
     plateforme → certificat SSH à durée de vie courte ;
  3. résolution du bastion qui **dessert le VPC de la cible** (ou bastion
     explicite) ;
  4. connexion `ssh`/`scp` à travers le bastion, qui relaie vers la cible.

Aucune clé n'est déposée sur la cible : le certificat éphémère est nettoyé en
fin de session.
"""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
import tempfile
from pathlib import Path

import typer
from rich import print as rprint

from cetic import client

# Statuts de bastion considérés comme utilisables.
_USABLE_STATUS = (None, "active", "running")


# ── Résolution du bastion (VPC-aware) ────────────────────────────────────────
def _parse_ip(target: str) -> ipaddress._BaseAddress | None:
    """Retourne l'adresse IP si `target` en est une, sinon None."""
    try:
        return ipaddress.ip_address(target.strip())
    except ValueError:
        return None


def _cidr_contains(cidr: str | None, ip: ipaddress._BaseAddress) -> bool:
    if not cidr:
        return False
    try:
        return ip in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def _bastion_vpc_ids(b: dict) -> set[str]:
    """Ensemble des VPC desservis par un bastion (primaire + secondaires)."""
    ids = {str(x) for x in (b.get("vpc_ids") or [])}
    if b.get("vpc_id"):
        ids.add(str(b["vpc_id"]))
    return ids


def _resolve_target_vpc_ids(ip: ipaddress._BaseAddress) -> set[str]:
    """VPC dont un CIDR (VPC ou VNet) contient `ip`.

    Essaie d'abord le bloc CIDR du VPC (1 seul appel), puis se rabat sur les
    CIDR des VNets de chaque VPC (les VPC legacy n'ont pas de bloc). Best-effort :
    toute erreur réseau → ensemble vide (la sélection retombe sur l'ambiguïté)."""
    try:
        vpcs = client.get("/v1/vpcs")
    except client.APIError:
        return set()
    matches = {str(v["id"]) for v in vpcs if _cidr_contains(v.get("cidr"), ip)}
    if matches:
        return matches
    for v in vpcs:
        try:
            vnets = client.get(f"/v1/vpcs/{v['id']}/vnets")
        except client.APIError:
            continue
        if any(_cidr_contains(n.get("cidr"), ip) for n in vnets):
            matches.add(str(v["id"]))
    return matches


def _resolve_bastion_host(explicit: str | None, target: str) -> str:
    """Résout l'hôte du bastion à emprunter pour atteindre `target`.

    - `explicit` fourni → tel quel.
    - Un seul bastion → on l'utilise.
    - Plusieurs bastions → on choisit celui qui **dessert le VPC de la cible**
      (résolu via l'IP de la cible). Si on ne peut pas trancher, on demande
      explicitement `--bastion` plutôt que d'en dialer un à l'aveugle (cause
      historique du « connexion à la cible impossible »).
    """
    if explicit:
        return explicit

    try:
        bastions = client.get("/v1/bastions")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1) from e

    usable = [
        b for b in bastions
        if b.get("endpoint_host") and b.get("status") in _USABLE_STATUS
    ]
    if not usable:
        usable = [b for b in bastions if b.get("endpoint_host")]

    if not usable:
        rprint(
            "[red]Aucun bastion disponible.[/red] Créez-en un avec "
            "[cyan]cetic bastion create --name <nom> --region <région> "
            "--vpc <vpc>[/cyan], ou précisez un hôte via [cyan]--bastion[/cyan]."
        )
        raise typer.Exit(1)

    if len(usable) == 1:
        return usable[0]["endpoint_host"]

    # Plusieurs bastions : router vers celui du VPC de la cible.
    ip = _parse_ip(target)
    if ip is not None:
        target_vpcs = _resolve_target_vpc_ids(ip)
        if target_vpcs:
            matching = [b for b in usable if _bastion_vpc_ids(b) & target_vpcs]
            if matching:
                return matching[0]["endpoint_host"]
            rprint(
                f"[red]Aucun bastion ne dessert le VPC de la cible {target}.[/red]\n"
                "Créez un bastion dans ce VPC, ajoutez ce VPC à un bastion "
                "existant, ou précisez [cyan]--bastion <hôte>[/cyan]."
            )
            raise typer.Exit(1)

    hosts = ", ".join(b["endpoint_host"] for b in usable)
    rprint(
        f"[red]Plusieurs bastions disponibles et impossible de déterminer "
        f"lequel atteint « {target} ».[/red]\n"
        f"Précisez [cyan]--bastion <hôte>[/cyan]. Bastions : {hosts}"
    )
    raise typer.Exit(1)


# ── Certificat éphémère ──────────────────────────────────────────────────────
def _require_bin(name: str) -> str:
    found = shutil.which(name)
    if not found:
        rprint(f"[red]Erreur : `{name}` est introuvable dans le PATH.[/red]")
        raise typer.Exit(1)
    return found


def _mint_ephemeral_cert(
    tmpdir: str, *, target: str, login: str, ttl: int
) -> tuple[Path, Path]:
    """Génère une paire éphémère + récupère un certificat signé par la plateforme.

    Retourne `(key_path, cert_path)`. Lève `typer.Exit` en cas d'échec.
    """
    keygen_bin = _require_bin("ssh-keygen")
    key_path = Path(tmpdir) / "id"
    cert_path = Path(tmpdir) / "id-cert.pub"

    gen = subprocess.run(  # noqa: S603
        [keygen_bin, "-t", "ed25519", "-N", "", "-q",
         "-C", "cetic-ephemeral", "-f", str(key_path)],
        capture_output=True, text=True,
    )
    if gen.returncode != 0:
        rprint(
            "[red]Erreur : impossible de générer la clé éphémère.[/red]\n"
            f"[dim]{gen.stderr.strip()}[/dim]"
        )
        raise typer.Exit(1)

    public_key = (key_path.with_suffix(".pub")).read_text(encoding="utf-8").strip()
    try:
        signed = client.post("/v1/ssh/sign", json={
            "public_key": public_key,
            "target": target,
            "login": login,
            "ttl_seconds": ttl,
        })
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
    return key_path, cert_path


# ── cetic ssh ────────────────────────────────────────────────────────────────
def ssh(  # noqa: PLR0913
    target: str = typer.Argument(
        ...,
        metavar="TARGET",
        help="Cible privée à atteindre (IP privée / nom d'hôte / identifiant de "
             "ressource). Résolue côté bastion.",
    ),
    login: str = typer.Option(
        "root", "--login", "-l", help="Utilisateur de connexion sur la cible."
    ),
    bastion: str | None = typer.Option(
        None, "--bastion", "-b",
        help="Hôte du bastion à emprunter. Par défaut, le bastion qui dessert "
             "le VPC de la cible.",
    ),
    ttl: int = typer.Option(
        300, "--ttl", help="Durée de vie du certificat SSH éphémère, en secondes.",
    ),
) -> None:
    """Ouvre une session SSH sécurisée vers une cible privée via le bastion.

    Une paire de clés éphémère est générée localement, signée par la plateforme
    (certificat à courte durée de vie), puis utilisée pour se connecter à travers
    le bastion. Aucune clé statique n'est déployée.

    Exemples :
        cetic ssh 10.0.1.42
        cetic ssh web-01 --login ubuntu --ttl 600
        cetic ssh 10.0.1.42 --bastion bastion.par.cloud.cetic-group.com
    """
    if ttl <= 0:
        rprint("[red]Erreur : --ttl doit être un entier positif.[/red]")
        raise typer.Exit(1)

    ssh_bin = _require_bin("ssh")

    tmpdir = tempfile.mkdtemp(prefix="cetic-ssh-")
    try:
        key_path, cert_path = _mint_ephemeral_cert(
            tmpdir, target=target, login=login, ttl=ttl)
        bastion_host = _resolve_bastion_host(bastion, target)

        rprint(
            f"[dim]Accès SSH sécurisé vers [cyan]{target}[/cyan] via le bastion "
            f"[cyan]{bastion_host}[/cyan] (certificat valable {ttl}s).[/dim]"
        )

        # La cible est passée en argument `host=<TARGET>` que le bastion résout
        # côté serveur. `-t` force un PTY pour un shell interactif.
        cmd = [
            ssh_bin, "-t",
            "-i", str(key_path),
            "-o", f"CertificateFile={cert_path}",
            "-o", "IdentitiesOnly=yes",
            f"{login}@{bastion_host}",
            f"host={target}",
        ]
        completed = subprocess.run(cmd)  # noqa: S603
        raise typer.Exit(completed.returncode)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── cetic scp ────────────────────────────────────────────────────────────────
def _split_remote(arg: str) -> tuple[str, str] | None:
    """Découpe `TARGET:chemin` → (target, chemin). None si pas de partie distante.

    On exige au moins 2 caractères avant `:` pour ne pas confondre avec un
    chemin Windows (`C:\\...`), peu pertinent ici mais prudent.
    """
    idx = arg.find(":")
    if idx <= 0:
        return None
    return arg[:idx], arg[idx + 1:]


def scp(  # noqa: PLR0913
    source: str = typer.Argument(
        ...,
        metavar="SOURCE",
        help="Source. Distante = TARGET:chemin (ex 10.0.1.42:/etc/hosts), "
             "sinon chemin local.",
    ),
    dest: str = typer.Argument(
        ...,
        metavar="DEST",
        help="Destination. Distante = TARGET:chemin, sinon chemin local.",
    ),
    recursive: bool = typer.Option(
        False, "--recursive", "-r",
        help="Copier récursivement les répertoires (auto-activé si la source "
             "locale est un répertoire).",
    ),
    login: str = typer.Option(
        "root", "--login", "-l", help="Utilisateur de connexion sur la cible."
    ),
    bastion: str | None = typer.Option(
        None, "--bastion", "-b",
        help="Hôte du bastion à emprunter. Par défaut, le bastion du VPC de la "
             "cible.",
    ),
    ttl: int = typer.Option(
        300, "--ttl", help="Durée de vie du certificat SSH éphémère, en secondes.",
    ),
) -> None:
    """Copie des fichiers/répertoires vers ou depuis une cible privée via le bastion.

    Exactement une des deux extrémités doit être distante, au format
    `TARGET:chemin` (TARGET = IP privée / nom d'hôte / identifiant de ressource).

    Exemples :
        cetic scp ./app.tar 10.0.1.42:/srv/            # upload
        cetic scp 10.0.1.42:/var/log/app ./logs -r     # download récursif
        cetic scp ./site 10.0.1.42:/var/www -r         # upload récursif
    """
    if ttl <= 0:
        rprint("[red]Erreur : --ttl doit être un entier positif.[/red]")
        raise typer.Exit(1)

    src_remote = _split_remote(source)
    dst_remote = _split_remote(dest)
    if (src_remote is None) == (dst_remote is None):
        rprint(
            "[red]Erreur : exactement une extrémité doit être distante "
            "(format [cyan]TARGET:chemin[/cyan]).[/red]\n"
            "Exemples : [cyan]cetic scp ./f 10.0.1.42:/tmp/[/cyan] (upload) ou "
            "[cyan]cetic scp 10.0.1.42:/etc/hosts ./[/cyan] (download)."
        )
        raise typer.Exit(1)

    target = (src_remote or dst_remote)[0]  # type: ignore[index]

    # -r auto si la source locale est un répertoire.
    if dst_remote is not None and not recursive and Path(source).is_dir():
        recursive = True

    scp_bin = _require_bin("scp")

    tmpdir = tempfile.mkdtemp(prefix="cetic-scp-")
    try:
        key_path, cert_path = _mint_ephemeral_cert(
            tmpdir, target=target, login=login, ttl=ttl)
        bastion_host = _resolve_bastion_host(bastion, target)

        # La cible est transmise au bastion via la variable d'environnement de
        # session `CCP_TARGET` (le sous-système sftp ne permet pas de passer un
        # argument `host=` comme le shell interactif).
        remote_arg = f"{login}@{bastion_host}:"
        if src_remote is not None:
            local = dest
            remote = f"{remote_arg}{src_remote[1]}"
            scp_args = [remote, local]
        else:
            local = source
            remote = f"{remote_arg}{dst_remote[1]}"  # type: ignore[index]
            scp_args = [local, remote]

        rprint(
            f"[dim]Transfert sécurisé via le bastion [cyan]{bastion_host}[/cyan] "
            f"→ cible [cyan]{target}[/cyan] (certificat valable {ttl}s).[/dim]"
        )

        cmd = [scp_bin]
        if recursive:
            cmd.append("-r")
        cmd += [
            "-i", str(key_path),
            "-o", f"CertificateFile={cert_path}",
            "-o", "IdentitiesOnly=yes",
            "-o", f"SetEnv=CCP_TARGET={target}",
            *scp_args,
        ]
        completed = subprocess.run(cmd)  # noqa: S603
        raise typer.Exit(completed.returncode)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
