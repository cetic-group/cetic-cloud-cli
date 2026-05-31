"""cetic iam — Identity & Access Management (Roles v1, AWS-style).

Sous-commandes :
    cetic iam roles list [--built-in] [--custom]
    cetic iam roles get ID|NAME [--reveal-policy]
    cetic iam roles create --name NAME --policy-file FILE.json [--description DESC]
    cetic iam roles update ID|NAME [--policy-file FILE.json] [--description DESC]
    cetic iam roles delete ID|NAME [--yes]
    cetic iam roles attach ROLE_ID|NAME --principal-type T --principal-id ID|NAME [--expires-at ISO]
    cetic iam roles detach ASSIGNMENT_ID [--yes]
    cetic iam built-ins list
    cetic iam who-am-i [--effective-permissions]
    cetic iam simulate --action ACT --resource ARN [--principal-type T --principal-id ID]

Le `policy_document` est lu depuis un fichier JSON local (cf. `_load.py`).
Les ARN dans `resources` sont validés côté CLI via `parse_arn` AVANT POST.

Cf. apps/api/IAM_CONTRACT_FROZEN.md + apps/docs-internal/services/iam-arn-scheme.md.
"""
from __future__ import annotations

from typing import Any

import typer
from rich import print as rprint

from cetic import client
from cetic._format import _format_decision
from cetic._iam_arn import parse_arn
from cetic._load import _load_policy_file
from cetic._resolve import resolve_id, resolve_principal
from cetic.commands._render import render_list, render_one


IAM_ROLES_PATH = "/v1/iam/roles"
IAM_BUILT_INS_PATH = "/v1/iam/built-in-roles"
IAM_SIMULATE_PATH = "/v1/iam/simulate"
IAM_PRINCIPALS_PATH = "/v1/iam/principals"

PRINCIPAL_TYPES = ("org_member", "api_key", "service_account", "ccks_workload")


app = typer.Typer(help="IAM Roles v1 — gestion des rôles et permissions")
roles_app = typer.Typer(help="Rôles IAM (custom + built-in)")
builtins_app = typer.Typer(help="Catalogue des rôles built-in CETIC")
app.add_typer(roles_app, name="roles")
app.add_typer(builtins_app, name="built-ins")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


SENSITIVE_FIELDS = (
    "policy_document",  # masqué par défaut sur `roles get`, sauf --reveal-policy
)


def _format_api_error(e: client.APIError) -> str:
    if e.status_code == 401:
        return "Non authentifié — vérifiez `cetic auth login` ou `CCP_API_KEY`."
    if e.status_code == 403:
        return "Accès refusé — droits insuffisants pour cette opération."
    if e.status_code == 404:
        return "Ressource introuvable."
    if e.status_code == 409:
        return f"Conflit : {e.detail}"
    if e.status_code == 422:
        return f"Données invalides : {e.detail}"
    if e.status_code >= 500:
        return f"Erreur serveur ({e.status_code}). Réessayez plus tard."
    return e.detail or f"Erreur HTTP {e.status_code}"


def _bail(e: client.APIError) -> typer.Exit:
    rprint(f"[red]Erreur : {_format_api_error(e)}[/red]")
    return typer.Exit(1)


def _resolve_role(id_or_name: str) -> str:
    return resolve_id(IAM_ROLES_PATH, id_or_name)


def _redact_role(item: dict[str, Any]) -> dict[str, Any]:
    """Cache `policy_document` (sauf si --reveal-policy)."""
    out = dict(item)
    if out.get("policy_document"):
        out["policy_document"] = "<masqué — utilisez --reveal-policy>"
    return out


# ---------------------------------------------------------------------------
# Sub-app `roles`
# ---------------------------------------------------------------------------


@roles_app.command(name="list")
def roles_list(
    built_in: bool = typer.Option(False, "--built-in", help="Filtre : built-ins uniquement"),
    custom: bool = typer.Option(False, "--custom", help="Filtre : rôles custom uniquement"),
) -> None:
    """Liste les rôles IAM visibles par le tenant (custom + built-ins)."""
    if built_in and custom:
        rprint("[red]Les flags --built-in et --custom sont mutuellement exclusifs.[/red]")
        raise typer.Exit(1)
    params: dict[str, Any] = {}
    if built_in:
        params["built_in"] = "true"
    elif custom:
        params["built_in"] = "false"
    try:
        items = client.get(IAM_ROLES_PATH, params=params or None)
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": r["id"],
            "name": r["name"],
            "kind": "built-in" if r.get("is_built_in") else "custom",
            "statements": str(len((r.get("policy_document") or {}).get("statements") or [])),
            "description": (r.get("description") or "—"),
        }
        for r in items
    ]
    render_list(
        rows,
        title=f"Rôles IAM ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("name", "Nom"),
            ("kind", "Type"),
            ("statements", "Stmts"),
            ("description", "Description"),
        ],
    )


@roles_app.command()
def get(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    reveal_policy: bool = typer.Option(
        False, "--reveal-policy",
        help="Affiche le `policy_document` complet (masqué par défaut)",
    ),
) -> None:
    """Détails d'un rôle IAM. Policy masquée par défaut."""
    rid = _resolve_role(id_or_name)
    try:
        role = client.get(f"{IAM_ROLES_PATH}/{rid}")
    except client.APIError as e:
        raise _bail(e) from e
    payload = role if reveal_policy else _redact_role(role)
    render_one(payload, title=f"Rôle {role.get('name', rid)}")


@roles_app.command()
def create(
    name: str = typer.Option(..., "--name", "-n"),
    policy_file: str = typer.Option(
        ..., "--policy-file",
        help="Fichier JSON contenant le policy_document",
    ),
    description: str | None = typer.Option(None, "--description", "-d"),
) -> None:
    """Crée un rôle IAM custom à partir d'un fichier policy JSON.

    Les ARN dans `resources` sont validés côté CLI (parse strict)
    AVANT l'appel API.
    """
    doc = _load_policy_file(policy_file)
    body: dict[str, Any] = {"name": name, "policy_document": doc}
    if description is not None:
        body["description"] = description
    try:
        role = client.post(IAM_ROLES_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Rôle créé : [bold]{role['name']}[/bold] ({role['id']})")


@roles_app.command()
def update(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    policy_file: str | None = typer.Option(
        None, "--policy-file",
        help="Nouveau policy_document JSON (remplace)",
    ),
    description: str | None = typer.Option(None, "--description", "-d"),
) -> None:
    """Modifie un rôle custom. Refusé sur les built-ins (403)."""
    if policy_file is None and description is None:
        rprint(
            "[red]Aucune modification demandée. "
            "Fournissez `--policy-file` et/ou `--description`.[/red]"
        )
        raise typer.Exit(1)
    body: dict[str, Any] = {}
    if policy_file is not None:
        body["policy_document"] = _load_policy_file(policy_file)
    if description is not None:
        body["description"] = description
    rid = _resolve_role(id_or_name)
    try:
        role = client.patch(f"{IAM_ROLES_PATH}/{rid}", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[green]✓[/green] Rôle mis à jour : [bold]{role.get('name', rid)}[/bold]")


@roles_app.command()
def delete(
    id_or_name: str = typer.Argument(..., metavar="ID|NAME"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Supprime un rôle custom. Refusé si des assignments existent (409)."""
    rid = _resolve_role(id_or_name)
    if not yes and not typer.confirm(f"Supprimer le rôle {id_or_name} ?"):
        raise typer.Abort()
    try:
        client.delete(f"{IAM_ROLES_PATH}/{rid}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Rôle supprimé.")


# ---------------------------------------------------------------------------
# Roles attach / detach (assignments)
# ---------------------------------------------------------------------------


def _validate_principal_type(value: str) -> str:
    if value not in PRINCIPAL_TYPES:
        rprint(
            f"[red]--principal-type doit être un de {list(PRINCIPAL_TYPES)} "
            f"(reçu {value!r}).[/red]"
        )
        raise typer.Exit(1)
    return value


@roles_app.command()
def attach(
    role_id_or_name: str = typer.Argument(..., metavar="ROLE_ID|NAME"),
    principal_type: str = typer.Option(
        ..., "--principal-type",
        help=f"Type de principal : {' | '.join(PRINCIPAL_TYPES)}",
    ),
    principal_id: str = typer.Option(
        ..., "--principal-id",
        help="UUID ou nom/email du principal (résolu via /v1/api-keys, /v1/members, etc.)",
    ),
    expires_at: str | None = typer.Option(
        None, "--expires-at",
        help="Date ISO 8601 d'expiration (ex: 2027-05-10T00:00:00Z)",
    ),
) -> None:
    """Attache un rôle à un principal (member / api_key / service_account / ccks_workload)."""
    ptype = _validate_principal_type(principal_type)
    pid = resolve_principal(ptype, principal_id)
    role_id = _resolve_role(role_id_or_name)

    body: dict[str, Any] = {"principal_type": ptype, "principal_id": pid}
    if expires_at:
        body["expires_at"] = expires_at
    try:
        a = client.post(f"{IAM_ROLES_PATH}/{role_id}/assignments", json=body)
    except client.APIError as e:
        raise _bail(e) from e
    rprint(
        f"[green]✓[/green] Rôle attaché : assignment [bold]{a['id']}[/bold] "
        f"({ptype} → {pid[:8]})"
    )


@roles_app.command()
def detach(
    assignment_id: str = typer.Argument(..., help="UUID de l'assignment à supprimer"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Détache un rôle d'un principal (par ID d'assignment).

    Note : l'API expose `DELETE /v1/iam/roles/{role_id}/assignments/{id}`.
    Le CLI résout d'abord l'assignment côté serveur via une recherche
    cross-roles (peut nécessiter plusieurs tentatives si l'ID seul est
    fourni — pour optimiser, fournir aussi `--role` n'est pas requis :
    on liste les rôles attachés au tenant et on cherche l'assignment).
    """
    if not yes and not typer.confirm(f"Détacher l'assignment {assignment_id} ?"):
        raise typer.Abort()
    # Trouver le role_id qui contient cet assignment.
    try:
        roles = client.get(IAM_ROLES_PATH)
    except client.APIError as e:
        raise _bail(e) from e
    role_id_found: str | None = None
    for r in roles:
        try:
            assignments = client.get(f"{IAM_ROLES_PATH}/{r['id']}/assignments")
        except client.APIError:
            continue
        if any(a["id"] == assignment_id for a in assignments):
            role_id_found = r["id"]
            break
    if role_id_found is None:
        rprint(
            f"[red]Assignment {assignment_id} introuvable parmi les rôles du tenant.[/red]"
        )
        raise typer.Exit(1)
    try:
        client.delete(f"{IAM_ROLES_PATH}/{role_id_found}/assignments/{assignment_id}")
    except client.APIError as e:
        raise _bail(e) from e
    rprint("[green]✓[/green] Assignment supprimé.")


# ---------------------------------------------------------------------------
# Sub-app `built-ins`
# ---------------------------------------------------------------------------


@builtins_app.command(name="list")
def builtins_list() -> None:
    """Liste les 10 rôles built-in CETIC (AdminAll, RegistryAdmin, ...)."""
    try:
        items = client.get(IAM_BUILT_INS_PATH)
    except client.APIError as e:
        raise _bail(e) from e
    rows = [
        {
            "id": r["id"],
            "name": r["name"],
            "statements": str(len((r.get("policy_document") or {}).get("statements") or [])),
            "description": (r.get("description") or "—"),
        }
        for r in items
    ]
    render_list(
        rows,
        title=f"Built-in roles ({len(rows)})",
        columns=[
            ("id", "ID"),
            ("name", "Nom"),
            ("statements", "Stmts"),
            ("description", "Description"),
        ],
    )


# ---------------------------------------------------------------------------
# who-am-i + simulate
# ---------------------------------------------------------------------------


@app.command(name="who-am-i")
def who_am_i(
    effective_permissions: bool = typer.Option(
        False, "--effective-permissions",
        help="Affiche aussi les statements effectifs résolus côté serveur",
    ),
) -> None:
    """Affiche l'identité courante du caller (depuis /v1/tenants/me).

    Avec `--effective-permissions`, liste les statements (effect / actions /
    resources) résolus par l'évaluateur côté serveur.
    """
    try:
        me = client.get("/v1/tenants/me")
    except client.APIError as e:
        raise _bail(e) from e
    rprint(f"[bold]Email[/bold]   : {me.get('email', '—')}")
    rprint(f"[bold]Tenant[/bold]  : {me.get('id', '—')}")
    rprint(f"[bold]Statut[/bold]  : {me.get('status', '—')}")
    if me.get("company_name"):
        rprint(f"[bold]Société[/bold] : {me['company_name']}")

    if not effective_permissions:
        return

    # Pour un humain JWT, le principal est `org_member`. On essaie de déterminer
    # le principal_id via la liste des members du tenant courant en cherchant
    # par email. Pour une API key, on n'a pas d'endpoint /self direct ; on
    # affiche un message UX.
    email = me.get("email")
    if not email:
        rprint("[yellow]Impossible de résoudre le principal courant (email manquant).[/yellow]")
        return
    try:
        members = client.get("/v1/members")
    except client.APIError as e:
        raise _bail(e) from e
    me_member = next((m for m in members if m.get("email") == email), None)
    if me_member is None:
        rprint(
            "[yellow]Aucun assignment IAM résolu — l'identité courante n'est pas "
            "un membre invité (owner short-circuit, ou auth via API key/SA).[/yellow]"
        )
        return
    pid = me_member["id"]
    try:
        perms = client.get(
            f"{IAM_PRINCIPALS_PATH}/org_member/{pid}/effective-permissions"
        )
    except client.APIError as e:
        raise _bail(e) from e
    if not perms:
        rprint("[dim]Aucune permission effective (rôle non assigné).[/dim]")
        return
    rows = [
        {
            "role": p.get("role_name", "—"),
            "effect": p.get("effect", "—"),
            "actions": ",".join(p.get("actions", [])),
            "resources": ",".join(p.get("resources", [])),
            "sid": p.get("statement_sid") or "—",
        }
        for p in perms
    ]
    render_list(
        rows,
        title=f"Permissions effectives ({len(rows)})",
        columns=[
            ("role", "Rôle"),
            ("effect", "Effect"),
            ("actions", "Actions"),
            ("resources", "Resources"),
            ("sid", "SID"),
        ],
    )


@app.command()
def simulate(
    action: str = typer.Option(..., "--action",
                                help="Action testée, ex: registry:Pull, bucket:GetObject"),
    resource: str = typer.Option(..., "--resource",
                                  help="ARN ciblé (ex: arn:ccp:bucket:rnn:UUID:bucket/foo)"),
    principal_type: str | None = typer.Option(
        None, "--principal-type",
        help=f"Type de principal simulé : {' | '.join(PRINCIPAL_TYPES)} (default: org_member courant)",
    ),
    principal_id: str | None = typer.Option(
        None, "--principal-id",
        help="UUID/nom/email du principal simulé (default: caller)",
    ),
) -> None:
    """Simule une décision IAM pour (principal, action, resource).

    Valide l'ARN côté CLI (parse strict) avant le POST. La couleur de
    sortie reflète la décision : vert=Allow, rouge=ExplicitDeny,
    gris=ImplicitDeny.
    """
    # Validation ARN côté CLI (sauf wildcard global).
    if resource != "*":
        try:
            parse_arn(resource)
        except ValueError as e:
            rprint(f"[red]ARN invalide : {e}[/red]")
            raise typer.Exit(1) from e

    # Résoudre le principal — fallback sur le caller si non fourni.
    try:
        me = client.get("/v1/tenants/me")
    except client.APIError as e:
        raise _bail(e) from e

    tenant_id = me["id"]
    org_id = me.get("active_org_id") or me.get("default_org_id") or me.get("org_id")

    if principal_type is None and principal_id is None:
        # Caller fallback : on prend org_member du caller via /v1/members (on cherche son email).
        email = me.get("email")
        members = []
        if email:
            try:
                members = client.get("/v1/members")
            except client.APIError:
                members = []
        me_member = next((m for m in members if m.get("email") == email), None)
        if me_member is None:
            rprint(
                "[yellow]Impossible de déduire le principal courant — "
                "fournissez --principal-type et --principal-id.[/yellow]"
            )
            raise typer.Exit(1)
        ptype = "org_member"
        pid = me_member["id"]
    else:
        if principal_type is None or principal_id is None:
            rprint(
                "[red]Fournissez à la fois --principal-type et --principal-id.[/red]"
            )
            raise typer.Exit(1)
        ptype = _validate_principal_type(principal_type)
        pid = resolve_principal(ptype, principal_id)

    body: dict[str, Any] = {
        "principal": {
            "type": ptype,
            "id": pid,
            "tenant_id": tenant_id,
        },
        "action": action,
        "resource_arn": resource,
    }
    if org_id:
        body["principal"]["org_id"] = org_id
    try:
        resp = client.post(IAM_SIMULATE_PATH, json=body)
    except client.APIError as e:
        raise _bail(e) from e
    decision = resp.get("decision") or {}
    rprint(_format_decision(decision))
    matched = resp.get("matched_statements") or []
    if matched:
        rprint("\n[bold]Statements évalués[/bold] :")
        rows = [
            {
                "role": m.get("role_name", "—"),
                "effect": m.get("effect", "—"),
                "actions": ",".join(m.get("actions", [])),
                "resources": ",".join(m.get("resources", [])),
                "sid": m.get("statement_sid") or "—",
            }
            for m in matched
        ]
        render_list(
            rows,
            title="Permissions effectives",
            columns=[
                ("role", "Rôle"),
                ("effect", "Effect"),
                ("actions", "Actions"),
                ("resources", "Resources"),
                ("sid", "SID"),
            ],
        )
