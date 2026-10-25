"""cetic db — bases de données managées CETIC Cloud (DBaaS)."""

import httpx
import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="DBaaS CETIC Cloud (PostgreSQL, MariaDB, Valkey, FerretDB)")
pg_app = typer.Typer(help="PostgreSQL")
my_app = typer.Typer(help="MySQL (MariaDB)")
rd_app = typer.Typer(help="Redis (Valkey)")
mg_app = typer.Typer(help="MongoDB (FerretDB)")
app.add_typer(pg_app, name="pg")
app.add_typer(my_app, name="mysql")
app.add_typer(rd_app, name="redis")
app.add_typer(mg_app, name="mongo")


def _list_engine(engine: str, region: str | None) -> None:
    try:
        items = client.get(f"/v1/db/{engine}", params={"region": region} if region else None)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": d["id"], "name": d["name"], "region": d["region"],
         "tier": d.get("tier", "—"), "plan": d["plan"], "status": d["status"],
         "endpoint": d.get("endpoint_vnet_ip") or "—"}
        for d in items
    ]
    render_list(rows, title=f"DBaaS {engine.upper()} ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("region", "Région"),
                         ("tier", "Tier"), ("plan", "Plan"), ("status", "Statut"), ("endpoint", "Endpoint")])


def _create_engine(engine: str, name: str, region: str, tier: str, plan: str,
                   vnet_id: str, vpc_id: str | None,
                   storage_gb: int, engine_version: str | None) -> None:
    body = {"name": name, "region": region, "tier": tier, "plan": plan,
            "vnet_id": vnet_id, "storage_gb": storage_gb}
    if vpc_id: body["vpc_id"] = vpc_id
    if engine_version: body["engine_version"] = engine_version
    try:
        d = client.post(f"/v1/db/{engine}", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Instance {engine} créée : [bold]{d['id']}[/bold]")


def _get_engine(engine: str, db_id: str) -> None:
    try:
        d = client.get(f"/v1/db/{engine}/{db_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(d, title=f"{engine.upper()} {d.get('name', db_id)}")


def _credentials_engine(engine: str, db_id: str) -> None:
    try:
        c = client.get(f"/v1/db/{engine}/{db_id}/credentials")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(c, title="Credentials")


def _resize_disk_engine(engine: str, db_id: str, storage_gb: int, yes: bool) -> None:
    if not yes and not typer.confirm(
        f"Redimensionner le stockage de l'instance {engine}/{db_id} à {storage_gb} Go ?"
    ):
        raise typer.Abort()
    try:
        d = client.post(f"/v1/db/{engine}/{db_id}/resize-disk", json={"storage_gb": storage_gb})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Redimensionnement demandé. Nouveau statut : {d.get('status', '—')}")


def _delete_engine(engine: str, db_id: str, yes: bool) -> None:
    if not yes and not typer.confirm(f"Supprimer l'instance {engine}/{db_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/db/{engine}/{db_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Instance supprimée.")


# Génère un set CRUD pour chaque engine (pg/mysql/redis/mongo) — le code est
# parameterisé via une fermeture, ce qui économise 4× la duplication.
def _bind_engine(typer_app: typer.Typer, engine: str) -> None:
    @typer_app.command(name="list")
    def _list(region: str | None = typer.Option(None, "--region", "-r")) -> None:
        """Liste les instances."""
        _list_engine(engine, region)

    @typer_app.command(name="get")
    def _get(db_id: str = typer.Argument(...)) -> None:
        """Détails d'une instance."""
        _get_engine(engine, db_id)

    @typer_app.command(name="create")
    def _create(
        name: str = typer.Option(..., "--name", "-n"),
        region: str = typer.Option(..., "--region", "-r"),
        tier: str = typer.Option("dev", "--tier", help="dev | prod"),
        plan: str = typer.Option("nano", "--plan", "-p"),
        vnet_id: str = typer.Option(..., "--vnet"),
        vpc_id: str | None = typer.Option(None, "--vpc"),
        storage_gb: int = typer.Option(20, "--storage-gb", help="Taille du volume persistant (Go)"),
        engine_version: str | None = typer.Option(
            None, "--engine-version",
            help="Version DB (ex: 16 pour PG, 8 pour Valkey). Listez les versions dispo : `cetic db versions --engine pg`",
        ),
    ) -> None:
        """Crée une instance."""
        _create_engine(engine, name, region, tier, plan, vnet_id, vpc_id, storage_gb, engine_version)

    @typer_app.command(name="plans")
    def _plans() -> None:
        """Liste les plans disponibles pour ce moteur."""
        try:
            data = client.get(f"/v1/db/plans?engine={engine}")
        except client.APIError as e:
            rprint(f"[red]Erreur : {e.detail}[/red]")
            raise typer.Exit(1)
        rows = [{"plan": p["key"], "cpu": p.get("cpu_millicores", "—"),
                 "ram": p.get("memory_mb", "—"), "prix_mois": p.get("price_eur_month", "—")}
                for p in data]
        render_list(rows, title=f"Plans {engine.upper()} ({len(rows)})",
                    columns=[("plan", "Plan"), ("cpu", "CPU (m)"), ("ram", "RAM (Mo)"), ("prix_mois", "€/mois")])

    @typer_app.command(name="versions")
    def _versions() -> None:
        """Liste les versions DB disponibles pour ce moteur."""
        try:
            data = client.get(f"/v1/db/engine-versions?engine={engine}")
        except client.APIError as e:
            rprint(f"[red]Erreur : {e.detail}[/red]")
            raise typer.Exit(1)
        rows = [{"version": v["version"], "label": v.get("label", ""), "défaut": "✓" if v.get("is_default") else ""}
                for v in data]
        render_list(rows, title=f"Versions {engine.upper()}",
                    columns=[("version", "Version"), ("label", "Label"), ("défaut", "Défaut")])

    @typer_app.command(name="credentials")
    def _credentials(db_id: str = typer.Argument(...)) -> None:
        """Affiche les credentials de connexion."""
        _credentials_engine(engine, db_id)

    @typer_app.command(name="attach-ip")
    def _attach_ip(
        db_id: str = typer.Argument(...),
        ip_id: str = typer.Option(..., "--ip", help="UUID de l'IP publique"),
    ) -> None:
        """Attache une IP publique à l'instance DB."""
        try:
            client.post(f"/v1/db/{engine}/{db_id}/attach-ip", json={"public_ip_id": ip_id})
        except client.APIError as e:
            rprint(f"[red]Erreur : {e.detail}[/red]")
            raise typer.Exit(1)
        rprint("[green]✓[/green] Attache IP en cours.")

    @typer_app.command(name="detach-ip")
    def _detach_ip(db_id: str = typer.Argument(...)) -> None:
        """Détache l'IP publique de l'instance DB."""
        try:
            client.post(f"/v1/db/{engine}/{db_id}/detach-ip")
        except client.APIError as e:
            rprint(f"[red]Erreur : {e.detail}[/red]")
            raise typer.Exit(1)
        rprint("[green]✓[/green] Détachement IP en cours.")

    @typer_app.command(name="resize-disk")
    def _resize_disk(
        db_id: str = typer.Argument(...),
        storage_gb: int = typer.Option(
            ..., "--storage-gb", help="Nouvelle taille du volume persistant (Go) — agrandissement uniquement."
        ),
        yes: bool = typer.Option(False, "--yes", "-y"),
    ) -> None:
        """Redimensionne le stockage d'une instance (agrandissement uniquement)."""
        _resize_disk_engine(engine, db_id, storage_gb, yes)

    @typer_app.command(name="delete")
    def _delete(
        db_id: str = typer.Argument(...),
        yes: bool = typer.Option(False, "--yes", "-y"),
    ) -> None:
        """Supprime une instance."""
        _delete_engine(engine, db_id, yes)


_bind_engine(pg_app, "pg")
_bind_engine(my_app, "mysql")
_bind_engine(rd_app, "valkey")
_bind_engine(mg_app, "ferretdb")


# ── Extensions PostgreSQL ──────────────────────────────────────────────────
# Un client autorise des extensions du catalogue sur son instance, puis le
# propriétaire d'une base les crée avec `CREATE EXTENSION`. L'API valide les
# réglages (dépendances, préchargement) et refuse avec un message explicite :
# la CLI ne reproduit pas ces règles, qui divergeraient.

_EXT_WAIT_INTERVAL_S = 5


def _join(values: list[str] | None) -> str:
    return ", ".join(values) if values else "—"


@pg_app.command(name="extensions")
def pg_extensions(
    version: int = typer.Option(16, "--version", "-v", help="Version majeure de PostgreSQL"),
) -> None:
    """Catalogue des extensions autorisables pour une version majeure."""
    from cetic import config

    try:
        cat = client.get("/v1/db/pg/extensions", params={"version": version})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    if config.get_output() in ("json", "yaml"):
        render_one(cat, title=f"Extensions PostgreSQL {version}")
        return
    rows = [
        {"name": e["name"], "description": e.get("description", ""),
         "requires": _join(e.get("requires")),
         "preload": "oui" if e.get("requires_preload") else "—",
         "single": "oui" if e.get("single_database") else "—"}
        for e in cat.get("extensions", [])
    ]
    render_list(rows, title=f"Extensions autorisables — PostgreSQL {version} ({len(rows)})",
                columns=[("name", "Extension"), ("description", "Description"),
                         ("requires", "Dépend de"), ("preload", "À précharger"),
                         ("single", "Une seule base")])
    rprint(f"Bibliothèques préchargeables (--preload) : {_join(cat.get('preload_libraries'))}")
    rprint(f"Créables directement, sans autorisation : {_join(cat.get('direct_extensions'))}")


def _is_transient(e: Exception) -> bool:
    """Erreur passagère qui ne doit pas interrompre l'attente."""
    if isinstance(e, client.APIError):
        return e.status_code == 429 or e.status_code >= 500
    return isinstance(e, httpx.TransportError)


def _wait_extensions_applied(db_id: str, timeout_s: int) -> dict:
    """Interroge l'instance jusqu'à `applied`. Une erreur passagère (5xx, 429,
    réseau) est retentée jusqu'au délai ; une erreur client (404, 403) arrête."""
    import time

    deadline = time.monotonic() + timeout_s
    d: dict = {}
    while True:
        try:
            d = client.get(f"/v1/db/pg/{db_id}")
        except (client.APIError, httpx.TransportError) as e:
            if not _is_transient(e) or time.monotonic() > deadline:
                raise
        else:
            if d.get("extensions_status") == "applied":
                return d
            if time.monotonic() > deadline:
                raise TimeoutError(d.get("error_message") or "toujours en cours d'application")
        time.sleep(_EXT_WAIT_INTERVAL_S)


@pg_app.command(name="extensions-set")
def pg_extensions_set(
    db_id: str = typer.Argument(...),
    allow: list[str] | None = typer.Option(
        None, "--allow",
        help="Extension autorisée (répétable). Remplace la liste ; listez aussi ses dépendances.",
    ),
    clear_allowed: bool = typer.Option(
        False, "--clear-allowed", help="Retire toutes les extensions autorisées.",
    ),
    preload: list[str] | None = typer.Option(
        None, "--preload",
        help="Bibliothèque préchargée (répétable, ex. pg_cron). Remplace la liste ; redémarre l'instance.",
    ),
    clear_preload: bool = typer.Option(
        False, "--clear-preload", help="Retire toutes les bibliothèques préchargées.",
    ),
    cron_database: str | None = typer.Option(
        None, "--cron-database", help="Base où pg_cron exécute ses tâches (pg_cron doit être préchargé).",
    ),
    clear_cron_database: bool = typer.Option(
        False, "--clear-cron-database", help="Retire la base de pg_cron (valeur par défaut de la plateforme).",
    ),
    wait: bool = typer.Option(False, "--wait", help="Attend que les réglages soient appliqués."),
    timeout: int = typer.Option(1200, "--timeout", help="Délai d'attente de --wait, en secondes."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ne pas demander confirmation du redémarrage."),
) -> None:
    """Autorise des extensions sur une instance PostgreSQL.

    Une option omise laisse le réglage inchangé. Une extension encore installée
    dans une base ne peut pas être retirée : supprimez-la d'abord
    (DROP EXTENSION).
    """
    from cetic import config

    structured = config.get_output() in ("json", "yaml")
    if allow and clear_allowed:
        rprint("[red]Erreur : --allow et --clear-allowed s'excluent.[/red]")
        raise typer.Exit(2)
    if preload and clear_preload:
        rprint("[red]Erreur : --preload et --clear-preload s'excluent.[/red]")
        raise typer.Exit(2)
    if cron_database is not None and clear_cron_database:
        rprint("[red]Erreur : --cron-database et --clear-cron-database s'excluent.[/red]")
        raise typer.Exit(2)
    body: dict = {}
    if allow or clear_allowed:
        body["allowed_extensions"] = [] if clear_allowed else list(dict.fromkeys(allow or []))
    if preload or clear_preload:
        body["preload_libraries"] = [] if clear_preload else list(dict.fromkeys(preload or []))
    if cron_database is not None or clear_cron_database:
        body["cron_database"] = None if clear_cron_database else cron_database
    if not body:
        rprint("Rien à modifier : précisez --allow, --preload ou --cron-database.")
        return
    if "preload_libraries" in body and not yes and not typer.confirm(
        "Changer les bibliothèques préchargées redémarre l'instance (une réplique à la fois ; "
        "courte interruption sans réplique). Continuer ?"
    ):
        raise typer.Abort()
    try:
        d = client.patch(f"/v1/db/pg/{db_id}/extensions", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    if wait and d.get("extensions_status") != "applied":
        if not structured:
            rprint("Application des réglages en cours…")
        try:
            d = _wait_extensions_applied(db_id, timeout)
        except TimeoutError as e:
            rprint(f"[yellow]Réglages acceptés mais pas encore appliqués : {e}[/yellow]")
            raise typer.Exit(1)
        except client.APIError as e:
            rprint(f"[red]Erreur : {e.detail}[/red]")
            raise typer.Exit(1)
        except httpx.TransportError as e:
            rprint(f"[red]Erreur : API injoignable ({e}).[/red]")
            raise typer.Exit(1)
    if structured:
        render_one(d, title=f"Instance {db_id}")
        return
    rprint(
        f"[green]✓[/green] Extensions autorisées : {_join(d.get('allowed_extensions'))} · "
        f"préchargées : {_join(d.get('preload_libraries'))} · "
        f"état : {d.get('extensions_status', '—')}"
    )
    if d.get("extensions_status") == "applied" and d.get("allowed_extensions"):
        rprint("Le propriétaire d'une base peut maintenant exécuter CREATE EXTENSION.")
