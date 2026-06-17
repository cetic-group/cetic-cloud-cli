"""cetic template — gestion des templates customs (snapshot container/VM
réutilisable comme image de base par toute l'organisation).

Endpoints API :
  GET    /v1/custom-templates
  GET    /v1/custom-templates/{id}
  POST   /v1/custom-templates/from-container/{container_id}
  POST   /v1/custom-templates/from-vm/{vm_id}
  PATCH  /v1/custom-templates/{id}
  DELETE /v1/custom-templates/{id}
"""

import typer
from rich import print as rprint
from rich.prompt import Confirm
from rich.table import Table

from cetic import client, config
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Templates custom (snapshots container/VM réutilisables)")


_LIST_COLUMNS = [
    ("id", "ID"),
    ("name", "Nom"),
    ("template_type", "Type"),
    ("os_family", "OS"),
    ("region", "Région"),
    ("status", "Statut"),
    ("disk_gb", "Disque GB"),
    ("source_instance_type", "Source"),
]


@app.command(name="list")
def list_templates() -> None:
    """Liste les templates customs de l'organisation."""
    try:
        items = client.get("/v1/custom-templates")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_list(items, title="Templates custom", columns=_LIST_COLUMNS)


@app.command()
def get(template_id: str = typer.Argument(..., help="UUID du template")) -> None:
    """Affiche un template custom."""
    try:
        tpl = client.get(f"/v1/custom-templates/{template_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    render_one(tpl, title=f"Template {tpl.get('name', '')}")


@app.command(name="create-from-container")
def create_from_container(
    container_id: str = typer.Argument(..., help="UUID du container source"),
    name: str = typer.Option(..., "--name", "-n", help="Nom du template"),
    description: str | None = typer.Option(None, "--description", "-d"),
) -> None:
    """Crée un template à partir d'un snapshot container.

    Le container doit être stoppé. La création est asynchrone — vérifier
    l'état avec `cetic template get <id>`.
    """
    body: dict = {"name": name}
    if description:
        body["description"] = description
    try:
        tpl = client.post(
            f"/v1/custom-templates/from-container/{container_id}", json=body,
        )
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(
        f"[green]Template en création[/green] : {tpl['name']} "
        f"(id={tpl['id']}, status={tpl['status']})",
    )


@app.command(name="create-from-vm")
def create_from_vm(
    vm_id: str = typer.Argument(..., help="UUID de la VM source"),
    name: str = typer.Option(..., "--name", "-n", help="Nom du template"),
    description: str | None = typer.Option(None, "--description", "-d"),
) -> None:
    """Crée un template à partir d'un snapshot VM.

    La VM doit être stoppée. La création est asynchrone — vérifier l'état
    avec `cetic template get <id>`.
    """
    body: dict = {"name": name}
    if description:
        body["description"] = description
    try:
        tpl = client.post(f"/v1/custom-templates/from-vm/{vm_id}", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(
        f"[green]Template en création[/green] : {tpl['name']} "
        f"(id={tpl['id']}, status={tpl['status']})",
    )


@app.command()
def update(
    template_id: str = typer.Argument(...),
    name: str | None = typer.Option(None, "--name", "-n"),
    description: str | None = typer.Option(None, "--description", "-d"),
) -> None:
    """Met à jour le nom ou la description d'un template."""
    body: dict = {}
    if name:
        body["name"] = name
    if description is not None:
        body["description"] = description
    if not body:
        rprint("[yellow]Rien à mettre à jour (utiliser --name ou --description).[/yellow]")
        return
    try:
        tpl = client.patch(f"/v1/custom-templates/{template_id}", json=body)
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]Template mis à jour[/green] : {tpl['name']}")


@app.command()
def delete(
    template_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip la confirmation"),
) -> None:
    """Supprime un template custom."""
    if not yes:
        if not Confirm.ask(f"Supprimer le template {template_id} ?", default=False):
            raise typer.Exit(0)
    try:
        client.delete(f"/v1/custom-templates/{template_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]Template supprimé[/green]")
