"""cetic db — bases de données managées CETIC Cloud (DBaaS)."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="DBaaS CETIC Cloud (PostgreSQL, MariaDB, Valkey, FerretDB)")
pg_app = typer.Typer(help="PostgreSQL (CNPG)")
my_app = typer.Typer(help="MySQL (MariaDB-operator)")
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
