"""cetic member — membres d'une organisation CETIC Cloud (RBAC)."""

import typer
from rich import print as rprint

from cetic import client
from cetic.commands._render import render_list, render_one

app = typer.Typer(help="Membres d'organisations CETIC Cloud")


@app.command(name="list")
def list_members() -> None:
    """Liste les membres invités sur le tenant courant."""
    try:
        items = client.get("/v1/members")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": m["id"][:8], "email": m["email"], "role": m["role"],
         "accepted": "✓" if m.get("accepted_at") else "—",
         "added_at": (m.get("created_at") or "")[:10]}
        for m in items
    ]
    render_list(rows, title=f"Membres ({len(rows)})",
                columns=[("id", "ID"), ("email", "Email"), ("role", "Rôle"),
                         ("accepted", "Accepté"), ("added_at", "Ajouté")])


@app.command()
def invite(
    email: str = typer.Option(..., "--email", "-e"),
    role: str = typer.Option("member", "--role", "-r", help="admin | member | viewer"),
) -> None:
    """Invite un membre par email."""
    try:
        m = client.post("/v1/members", json={"email": email, "role": role})
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Invitation envoyée à {email} (rôle: {role}).")
    rprint(f"[dim]ID: {m['id']}[/dim]")


@app.command(name="set-role")
def set_role(
    member_id: str = typer.Argument(...),
    role: str = typer.Option(..., "--role", "-r", help="admin | member | viewer"),
) -> None:
    """Change le rôle d'un membre."""
    try:
        client.post(f"/v1/members/{member_id}", json={"role": role})
    except client.APIError as e:
        # Fallback PATCH
        try:
            import httpx
            from cetic import config
            url = config.get_api_url().rstrip("/") + f"/v1/members/{member_id}"
            with httpx.Client() as h:
                resp = h.patch(url, headers={"Authorization": f"Bearer {config.get('api_key') or ''}"},
                               json={"role": role})
                resp.raise_for_status()
        except Exception:
            rprint(f"[red]Erreur : {e.detail}[/red]")
            raise typer.Exit(1)
    rprint(f"[green]✓[/green] Rôle mis à jour → {role}.")


@app.command()
def remove(
    member_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Retire un membre."""
    if not yes and not typer.confirm(f"Retirer le membre {member_id} ?"):
        raise typer.Abort()
    try:
        client.delete(f"/v1/members/{member_id}")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rprint("[green]✓[/green] Membre retiré.")


@app.command(name="orgs")
def accessible_orgs() -> None:
    """Liste les organisations accessibles (les miennes + celles où je suis membre)."""
    try:
        orgs = client.get("/v1/members/accessible-orgs")
    except client.APIError as e:
        rprint(f"[red]Erreur : {e.detail}[/red]")
        raise typer.Exit(1)
    rows = [
        {"id": o["id"][:8], "name": o.get("name", "—"),
         "tenant_id": (o.get("owner_tenant_id") or "")[:8],
         "role": o.get("role", "owner")}
        for o in orgs
    ]
    render_list(rows, title=f"Orgs accessibles ({len(rows)})",
                columns=[("id", "ID"), ("name", "Nom"), ("tenant_id", "Owner"), ("role", "Rôle")])
