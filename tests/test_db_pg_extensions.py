"""Tests des extensions PostgreSQL : catalogue, autorisation, attente."""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.commands import db as db_cmd
from cetic.main import app

DB = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

CATALOG = {
    "version": 16,
    "extensions": [
        {"name": "postgis", "description": "Données géospatiales", "requires_preload": False,
         "requires": [], "single_database": False},
        {"name": "postgis_topology", "description": "Topologie PostGIS", "requires_preload": False,
         "requires": ["postgis"], "single_database": False},
        {"name": "pg_cron", "description": "Tâches planifiées", "requires_preload": True,
         "requires": [], "single_database": True},
    ],
    "preload_libraries": ["pg_cron", "pg_stat_statements"],
    "direct_extensions": ["pgcrypto", "pg_trgm"],
}


def _instance(status: str = "applied", **kw: Any) -> dict[str, Any]:
    d = {"id": DB, "name": "app", "extensions_available": True, "allowed_extensions": ["postgis"],
         "preload_libraries": [], "cron_database": None, "extensions_status": status}
    d.update(kw)
    return d


def _capture(captured: dict, status: int = 200, body: dict | None = None):
    def _h(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(status, json=body if body is not None else _instance())
    return _h


def test_catalog_table(runner, mock_api) -> None:
    route = mock_api.get("/v1/db/pg/extensions").mock(return_value=httpx.Response(200, json=CATALOG))
    r = runner.invoke(app, ["db", "pg", "extensions"])
    assert r.exit_code == 0, r.output
    assert route.calls.last.request.url.params["version"] == "16"
    assert "postgis_topology" in r.output and "pg_cron" in r.output
    assert "pgcrypto, pg_trgm" in r.output


def test_catalog_json_keeps_the_whole_catalog(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    mock_api.get("/v1/db/pg/extensions").mock(return_value=httpx.Response(200, json=CATALOG))
    r = runner.invoke(app, ["db", "pg", "extensions", "--version", "16"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["direct_extensions"] == ["pgcrypto", "pg_trgm"]


def test_catalog_unknown_version(runner, mock_api) -> None:
    mock_api.get("/v1/db/pg/extensions").mock(return_value=httpx.Response(
        404, json={"detail": "Aucune extension n'est proposée pour PostgreSQL 15."}))
    r = runner.invoke(app, ["db", "pg", "extensions", "--version", "15"])
    assert r.exit_code == 1
    assert "PostgreSQL 15" in r.output


def test_set_sends_only_given_settings(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.patch(f"/v1/db/pg/{DB}/extensions").mock(side_effect=_capture(cap))
    r = runner.invoke(app, ["db", "pg", "extensions-set", DB,
                            "--allow", "postgis", "--allow", "postgis_topology", "--allow", "postgis"])
    assert r.exit_code == 0, r.output
    assert cap == {"allowed_extensions": ["postgis", "postgis_topology"]}


def test_set_clear_sends_empty_list(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.patch(f"/v1/db/pg/{DB}/extensions").mock(side_effect=_capture(cap))
    r = runner.invoke(app, ["db", "pg", "extensions-set", DB, "--clear-allowed"])
    assert r.exit_code == 0, r.output
    assert cap == {"allowed_extensions": []}


def test_set_preload_asks_for_restart_confirmation(runner, mock_api) -> None:
    route = mock_api.patch(f"/v1/db/pg/{DB}/extensions").mock(side_effect=_capture({}))
    r = runner.invoke(app, ["db", "pg", "extensions-set", DB, "--preload", "pg_cron"], input="n\n")
    assert r.exit_code != 0
    assert "redémarre l'instance" in r.output
    assert not route.called


def test_set_preload_and_cron_with_yes(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.patch(f"/v1/db/pg/{DB}/extensions").mock(side_effect=_capture(cap))
    r = runner.invoke(app, ["db", "pg", "extensions-set", DB, "--allow", "pg_cron",
                            "--preload", "pg_cron", "--cron-database", "jobs", "--yes"])
    assert r.exit_code == 0, r.output
    assert cap == {"allowed_extensions": ["pg_cron"], "preload_libraries": ["pg_cron"],
                   "cron_database": "jobs"}


def test_set_exclusive_options(runner, mock_api) -> None:
    r = runner.invoke(app, ["db", "pg", "extensions-set", DB, "--allow", "postgis", "--clear-allowed"])
    assert r.exit_code == 2
    assert "s'excluent" in r.output


def test_set_nothing(runner, mock_api) -> None:
    r = runner.invoke(app, ["db", "pg", "extensions-set", DB])
    assert r.exit_code == 0
    assert "Rien à modifier" in r.output


def test_set_shows_the_platform_refusal(runner, mock_api) -> None:
    mock_api.patch(f"/v1/db/pg/{DB}/extensions").mock(return_value=httpx.Response(409, json={
        "detail": "Supprimez d'abord ces extensions (DROP EXTENSION) avant de les retirer : app : postgis."}))
    r = runner.invoke(app, ["db", "pg", "extensions-set", DB, "--clear-allowed"])
    assert r.exit_code == 1
    assert "DROP EXTENSION" in r.output


def test_set_wait_polls_until_applied(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setattr(db_cmd, "_EXT_WAIT_INTERVAL_S", 0)
    mock_api.patch(f"/v1/db/pg/{DB}/extensions").mock(
        side_effect=_capture({}, body=_instance("pending")))
    route = mock_api.get(f"/v1/db/pg/{DB}").mock(side_effect=[
        httpx.Response(200, json=_instance("pending")),
        httpx.Response(200, json=_instance("applied", allowed_extensions=["postgis", "vector"])),
    ])
    r = runner.invoke(app, ["db", "pg", "extensions-set", DB, "--allow", "postgis", "--allow", "vector",
                            "--wait"])
    assert r.exit_code == 0, r.output
    assert route.call_count == 2
    assert "postgis, vector" in r.output and "applied" in r.output


def test_set_wait_timeout(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setattr(db_cmd, "_EXT_WAIT_INTERVAL_S", 0)
    mock_api.patch(f"/v1/db/pg/{DB}/extensions").mock(
        side_effect=_capture({}, body=_instance("pending")))
    mock_api.get(f"/v1/db/pg/{DB}").mock(return_value=httpx.Response(200, json=_instance(
        "pending", error_message="La mise à jour des extensions n'a pas pu être appliquée")))
    r = runner.invoke(app, ["db", "pg", "extensions-set", DB, "--allow", "postgis", "--wait",
                            "--timeout", "0"])
    assert r.exit_code == 1
    assert "pas pu être appliquée" in r.output


def test_get_shows_lists_readably(runner, mock_api) -> None:
    mock_api.get(f"/v1/db/pg/{DB}").mock(return_value=httpx.Response(
        200, json=_instance(allowed_extensions=["postgis", "vector"])))
    r = runner.invoke(app, ["db", "pg", "get", DB])
    assert r.exit_code == 0, r.output
    assert "postgis, vector" in r.output and "['postgis'" not in r.output
