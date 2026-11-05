"""`cetic db <moteur> create` : corps envoyé à l'API (replicas, tier, tags, options PG)."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cetic.main import app

BASE = ["--name", "app-db", "--region", "RNN", "--vnet", "vnet-1"]


def _capture(cap: dict):
    def _h(request: httpx.Request) -> httpx.Response:
        cap.update(json.loads(request.content))
        return httpx.Response(201, json={"id": "db-1"})
    return _h


# Sous-commande CLI → chemin de l'API.
CLI = {"pg": "pg", "valkey": "redis", "mysql": "mysql", "ferretdb": "mongo"}


def _create(runner, mock_api, engine: str, *args: str) -> tuple[Any, dict]:
    cap: dict[str, Any] = {}
    route = mock_api.post(f"/v1/db/{engine}").mock(side_effect=_capture(cap))
    r = runner.invoke(app, ["db", CLI[engine], "create", *BASE, *args])
    return r, (cap if route.called else {})


def test_defaults_send_neither_tier_nor_replicas(runner, mock_api) -> None:
    """Sans option : la plateforme choisit (1 réplique), plus de tier « dev » imposé."""
    r, cap = _create(runner, mock_api, "pg")
    assert r.exit_code == 0, r.output
    for key in ("tier", "replicas", "tags", "database", "pgbouncer_enabled", "engine_version"):
        assert key not in cap, key


def test_replicas_and_tags(runner, mock_api) -> None:
    r, cap = _create(runner, mock_api, "valkey", "--replicas", "3", "--tag", "a", "--tag", "b", "--tag", "a")
    assert r.exit_code == 0, r.output
    assert cap["replicas"] == 3 and "tier" not in cap
    assert cap["tags"] == ["a", "b"]


def test_legacy_tier_still_sent(runner, mock_api) -> None:
    r, cap = _create(runner, mock_api, "mysql", "--tier", "prod")
    assert r.exit_code == 0, r.output
    assert cap["tier"] == "prod" and "replicas" not in cap


def test_tier_consistent_with_replicas_sends_replicas(runner, mock_api) -> None:
    r, cap = _create(runner, mock_api, "pg", "--tier", "prod", "--replicas", "3")
    assert r.exit_code == 0, r.output
    assert cap["replicas"] == 3 and "tier" not in cap


@pytest.mark.parametrize("args", [
    ["--replicas", "2"],
    ["--tier", "staging"],
    ["--tier", "dev", "--replicas", "3"],
    ["--pgbouncer"],
    ["--pgbouncer", "--tier", "dev"],
])
def test_refused_before_any_call(runner, mock_api, args) -> None:
    r, cap = _create(runner, mock_api, "pg", *args)
    assert r.exit_code == 2, r.output
    assert cap == {}


def test_pg_database_and_pgbouncer(runner, mock_api) -> None:
    r, cap = _create(runner, mock_api, "pg", "--replicas", "3", "--database", "app_main", "--pgbouncer")
    assert r.exit_code == 0, r.output
    assert cap["database"] == "app_main"
    assert cap["pgbouncer_enabled"] is True


def test_pgbouncer_with_prod_tier(runner, mock_api) -> None:
    r, cap = _create(runner, mock_api, "pg", "--tier", "prod", "--pgbouncer")
    assert r.exit_code == 0, r.output
    assert cap["tier"] == "prod" and cap["pgbouncer_enabled"] is True


@pytest.mark.parametrize("engine", ["valkey", "mysql", "ferretdb"])
@pytest.mark.parametrize("args", [["--database", "x"], ["--replicas", "3", "--pgbouncer"]])
def test_pg_options_refused_on_other_engines(runner, mock_api, engine, args) -> None:
    r, cap = _create(runner, mock_api, engine, *args)
    assert r.exit_code == 2, r.output
    assert cap == {}


def _hidden_options(group: str) -> dict[str, bool]:
    """Options de `db <group> create` -> masquée ou non (sans dépendre du rendu de l'aide)."""
    import typer.main

    create = typer.main.get_command(app).commands["db"].commands[group].commands["create"]
    return {opt: p.hidden for p in create.params for opt in p.opts}


@pytest.mark.parametrize("group", ["redis", "mysql", "mongo"])
def test_pg_options_hidden_from_other_engines_help(group) -> None:
    hidden = _hidden_options(group)
    assert hidden["--pgbouncer"] and hidden["--database"]
    assert not hidden["--replicas"] and not hidden["--tag"]


def test_pg_options_shown_for_pg() -> None:
    hidden = _hidden_options("pg")
    assert not hidden["--pgbouncer"] and not hidden["--database"]
