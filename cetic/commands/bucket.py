"""cetic bucket — stockage objet S3 CETIC Cloud."""

import typer
from rich import print as rprint

from cetic import client, config
from cetic.commands._render import render_list, render_one
from cetic.commands.bucket_cors import cors_app
from cetic.commands.bucket_versioning import versioning_app

_ENC_LABELS = {"none": "aucun", "sse_c": "clé client"}


def _is_placeholder(v: object) -> bool:
    """Valeur d'en-tête à fournir par le client (texte entre chevrons)."""
    return isinstance(v, str) and v.startswith("<")


def _enc_label(b: dict) -> str:
    """Libellé du chiffrement ; un mode inconnu s'affiche tel quel."""
    mode = b.get("encryption_mode") or "none"
    return _ENC_LABELS.get(mode, mode)


app = typer.Typer(help="Buckets S3 CETIC Cloud")
app.add_typer(cors_app, name="cors")
app.add_typer(versioning_app, name="versioning")


@app.command(name="list")
def list_buckets(region: str | None = typer.Option(None, "--region", "-r")) -> None:
    """Liste les buckets S3."""
    try:
        items = client.get("/v1/buckets", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    table = config.get_output() == "table"
    rows = []
    for b in items:
        row = {"id": b["id"], "name": b["name"], "region": b["region"], "status": b["status"],
               "encryption_mode": b.get("encryption_mode") or "none"}
        if "encryption" in b:
            row["encryption"] = b["encryption"]
        if table:
            row["chiffrement_label"] = _enc_label(b)
        rows.append(row)
    render_list(rows, title=f"Buckets ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"), ("status", "Statut"),
                         ("chiffrement_label", "Chiffrement")])


@app.command()
def get(bucket_id: str = typer.Argument(...)) -> None:
    """Détails d'un bucket."""
    try:
        b = client.get(f"/v1/buckets/{bucket_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    if config.get_output() == "table":
        b = {**b, "Chiffrement": _enc_label(b)}
    render_one(b, title=f"Bucket {b.get('name', bucket_id)}")


@app.command()
def credentials(bucket_id: str = typer.Argument(...)) -> None:
    """Affiche les credentials master S3 du bucket."""
    try:
        creds = client.get(f"/v1/buckets/{bucket_id}/credentials")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(creds, title="Credentials S3")


@app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    region: str = typer.Option(..., "--region", "-r"),
    encryption: str | None = typer.Option(
        None, "--encryption",
        help="Chiffrement des fichiers : aucun (défaut) ou sse-c "
             "(clé fournie par vous, jamais connue de CETIC)"),
) -> None:
    """Crée un bucket S3."""
    body: dict = {"name": name, "region": region}
    if encryption is not None:
        body["encryption_mode"] = encryption.strip().lower().replace("-", "_")
    try:
        b = client.post("/v1/buckets", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Bucket créé : [bold]{b['id']}[/bold]")


@app.command()
def delete(
    bucket_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un bucket."""
    if not yes and not typer.confirm(f"Supprimer le bucket {bucket_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/buckets/{bucket_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Bucket supprimé.")


# ── Clés S3 scopées par bucket ─────────────────────────────────────────────

key_app = typer.Typer(help="Clés S3 scopées par bucket")
app.add_typer(key_app, name="key")


@key_app.command(name="list")
def list_keys(
    region: str | None = typer.Option(None, "--region", "-r"),
) -> None:
    """Liste les clés S3 scopées."""
    try:
        items = client.get("/v1/object-storage/keys",
                           params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": k["id"], "label": k["label"], "region": k["region"],
         "access_level": k.get("access_level", "—"),
         "prefix": k.get("access_key_prefix", "—"),
         "expires": (k.get("expires_at") or "—")[:10]}
        for k in items
    ]
    render_list(rows, title=f"Clés S3 ({len(rows)})",
                columns=[("id", "ID"), ("label", "Label"), ("region", "Région"),
                         ("access_level", "Accès"), ("prefix", "Prefix clé"), ("expires", "Expire le")])


@key_app.command()
def create(
    region: str = typer.Option(..., "--region", "-r"),
    label: str = typer.Option(..., "--label", "-l"),
    access_level: str = typer.Option("readwrite", "--access", "-a",
                                     help="read | write | readwrite | full"),
    expires_in_days: int | None = typer.Option(None, "--expires-days",
                                               help="Durée de validité en jours (1–3650)"),
) -> None:
    """Crée une clé S3 scopée. Les credentials ne sont affichés qu'une seule fois."""
    body: dict = {"region": region, "label": label, "access_level": access_level}
    if expires_in_days:
        body["expires_in_days"] = expires_in_days
    try:
        k = client.post("/v1/object-storage/keys", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Clé créée : [bold]{k['id']}[/bold]")
    rprint(f"  [dim]Access Key :[/dim] {k.get('access_key')}")
    rprint(f"  [dim]Secret Key :[/dim] {k.get('secret_key')}")
    rprint(f"  [dim]Endpoint   :[/dim] {k.get('endpoint_url')}")
    rprint("[yellow]⚠ Sauvegardez ces credentials — ils ne seront plus affichés.[/yellow]")


@key_app.command()
def revoke(
    key_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Révoque une clé S3 (suppression permanente)."""
    if not yes and not typer.confirm(f"Révoquer la clé {key_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/object-storage/keys/{key_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Clé révoquée.")


@app.command()
def update(
    bucket_id: str = typer.Argument(..., help="UUID du bucket"),
    public: bool | None = typer.Option(
        None, "--public/--private", help="Rendre le bucket public ou privé"),
) -> None:
    """Modifie la visibilité (publique/privée) d'un bucket."""
    if public is None:
        rprint("[yellow]Précisez [bold]--public[/bold] ou [bold]--private[/bold].[/yellow]")
        raise typer.Exit(0)
    try:
        client.patch(f"/v1/buckets/{bucket_id}", json={"is_public": public})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Bucket désormais [bold]{'public' if public else 'privé'}[/bold].")


@app.command()
def presign(
    bucket_id: str = typer.Argument(..., help="UUID du bucket"),
    key: str = typer.Argument(..., help="Clé de l'objet (chemin complet)"),
    method: str = typer.Option("GET", "--method", "-m", help="GET (lecture) ou PUT (envoi)"),
    expires: int = typer.Option(3600, "--expires", "-e", help="Durée de validité en secondes (60 à 604800)"),
    content_length: int | None = typer.Option(
        None, "--content-length", help="PUT : taille exacte du fichier en octets, signée dans le lien"),
    content_type: str | None = typer.Option(
        None, "--content-type", help="PUT : type du fichier (ex. image/png), signé dans le lien"),
    password: str | None = typer.Option(
        None, "--password", help="Protège le lien par un mot de passe (4 caractères minimum)"),
) -> None:
    """Génère une URL signée pour lire (GET) ou envoyer (PUT) un objet.

    Avec --content-length et --content-type, le stockage refuse tout envoi dont la
    taille ou le type diffère. Le lien reste utilisable jusqu'à son expiration."""
    method = method.upper()
    if method not in {"GET", "PUT"}:
        rprint("[red]La méthode doit être GET ou PUT.[/red]")
        raise typer.Exit(1)
    if method != "PUT" and (content_length is not None or content_type is not None):
        rprint("[red]--content-length et --content-type ne s'appliquent qu'à un envoi (--method PUT).[/red]")
        raise typer.Exit(1)
    if content_length is not None and content_length < 1:
        rprint("[red]--content-length doit être un nombre d'octets supérieur à 0.[/red]")
        raise typer.Exit(1)
    body: dict = {"key": key, "method": method, "expires_in": expires}
    if content_length is not None:
        body["content_length"] = content_length
    if content_type is not None:
        body["content_type"] = content_type
    if password:
        body["password"] = password
    try:
        r = client.post(f"/v1/buckets/{bucket_id}/presign", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    headers = r.get("required_headers") or {}
    if config.get_output() != "table":
        render_one(r, title="URL signée")
        return
    shown = {k: ("<votre valeur>" if _is_placeholder(v) else v) for k, v in headers.items()}
    render_one(
        {"url": r["url"], "method": r["method"], "expires_at": r["expires_at"],
         "password_protected": r.get("password_protected", False),
         **({"required_headers": shown} if shown else {})},
        title="URL signée",
    )
    if not headers:
        return
    if any(_is_placeholder(v) for v in headers.values()):
        rprint("[yellow]En-têtes à envoyer avec la requête :[/yellow]")
        for k, v in shown.items():
            rprint(f"  {k}: {v}")
        rprint("[dim]Votre clé et son empreinte MD5 (base64) sont calculées de votre côté "
               "et ne sont jamais envoyés à CETIC.[/dim]")
    else:
        rprint("[yellow]L'envoi doit reprendre ces en-têtes à l'identique, sinon le stockage le refuse.[/yellow]")
