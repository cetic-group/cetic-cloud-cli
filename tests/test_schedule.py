"""Tests de la commande `cetic schedule` — parsing fenêtres + CRUD + erreurs contrat."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cetic.commands.schedule import _fmt_window, _parse_off_window, _parse_off_windows
from cetic.main import app

SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
VM = "11111111-1111-1111-1111-111111111111"


# --- parsing des fenêtres -------------------------------------------------


def test_parse_off_window_names() -> None:
    assert _parse_off_window("fri:20-mon:08") == {
        "start_day": 4,
        "start_hour": 20,
        "end_day": 0,
        "end_hour": 8,
    }


def test_parse_off_window_numeric_days() -> None:
    assert _parse_off_window("4:20-0:8") == {
        "start_day": 4,
        "start_hour": 20,
        "end_day": 0,
        "end_hour": 8,
    }


def test_parse_off_window_hour_24_and_whitespace() -> None:
    assert _parse_off_window(" mon:0 - mon:24 ") == {
        "start_day": 0,
        "start_hour": 0,
        "end_day": 0,
        "end_hour": 24,
    }


def test_parse_off_windows_multiple() -> None:
    out = _parse_off_windows(["mon:22-tue:07", "sat:00-mon:00"])
    assert len(out) == 2
    assert out[1]["start_day"] == 5


@pytest.mark.parametrize(
    "bad",
    ["", "fri-mon", "fri:20", "xyz:20-mon:08", "fri:25-mon:08", "fri:20:mon:08"],
)
def test_parse_off_window_invalid(bad: str) -> None:
    import typer

    with pytest.raises(typer.BadParameter):
        _parse_off_window(bad)


def test_fmt_window_roundtrip() -> None:
    assert (
        _fmt_window({"start_day": 4, "start_hour": 20, "end_day": 0, "end_hour": 8})
        == "fri:20→mon:08"
    )


# --- CRUD / body ----------------------------------------------------------


def _capture(captured: dict, status: int = 200, resp: dict | None = None):
    def _h(request: httpx.Request) -> httpx.Response:
        if request.content:
            captured.update(json.loads(request.content))
        return httpx.Response(status, json=resp or {"id": SID, "name": "weekend"})

    return _h


def test_create_vm_body(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.post("/v1/schedules").mock(side_effect=_capture(cap, status=201))
    r = runner.invoke(
        app,
        ["schedule", "create", "weekend", "--vm", VM, "--off", "fri:20-mon:08"],
    )
    assert r.exit_code == 0, r.output
    assert cap == {
        "name": "weekend",
        "resource_type": "vm",
        "resource_id": VM,
        "windows": [{"start_day": 4, "start_hour": 20, "end_day": 0, "end_hour": 8}],
        "enabled": True,
    }


def test_create_vm_scale_set_with_tz_and_multi_off(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.post("/v1/schedules").mock(side_effect=_capture(cap, status=201))
    r = runner.invoke(
        app,
        [
            "schedule", "create", "nuits", "--vm-scale-set", VM,
            "--off", "mon:22-tue:07", "--off", "tue:22-wed:07",
            "--timezone", "Europe/Paris",
        ],
    )
    assert r.exit_code == 0, r.output
    assert cap["resource_type"] == "vm_scale_set"
    assert cap["timezone"] == "Europe/Paris"
    assert len(cap["windows"]) == 2


def test_create_ccks_node_pool(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.post("/v1/schedules").mock(side_effect=_capture(cap, status=201))
    pool_id = "22222222-2222-2222-2222-222222222222"
    r = runner.invoke(
        app,
        ["schedule", "create", "p", "--ccks-node-pool", VM, pool_id, "--off", "mon:20-tue:08"],
    )
    assert r.exit_code == 0, r.output
    assert cap["resource_type"] == "ccks_node_pool"
    assert cap["resource_id"] == pool_id


def test_create_rejects_two_targets(runner, mock_api) -> None:
    r = runner.invoke(
        app,
        ["schedule", "create", "x", "--vm", VM, "--container", VM, "--off", "fri:20-mon:08"],
    )
    assert r.exit_code == 1
    assert "une seule ressource" in r.output.lower() or "multiples" in r.output.lower()


def test_create_requires_a_target(runner, mock_api) -> None:
    r = runner.invoke(app, ["schedule", "create", "x", "--off", "fri:20-mon:08"])
    assert r.exit_code == 1
    assert "cible" in r.output.lower()


def test_create_disabled_flag(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.post("/v1/schedules").mock(side_effect=_capture(cap, status=201))
    r = runner.invoke(
        app,
        ["schedule", "create", "x", "--vm", VM, "--off", "fri:20-mon:08", "--disabled"],
    )
    assert r.exit_code == 0, r.output
    assert cap["enabled"] is False


def test_422_flapping_message_shown_verbatim(runner, mock_api) -> None:
    msg = "L'économie est facturée à l'heure : espacez vos fenêtres d'au moins 1 heure."
    mock_api.post("/v1/schedules").mock(
        return_value=httpx.Response(
            422, json={"detail": {"code": "schedule_too_frequent", "message": msg}}
        )
    )
    r = runner.invoke(
        app,
        ["schedule", "create", "x", "--vm", VM, "--off", "fri:20-mon:08"],
    )
    assert r.exit_code == 1
    # Rich peut replier (fold) la ligne au rendu ; on compare sans les sauts.
    assert msg in " ".join(r.output.split())


def test_429_quota_message(runner, mock_api) -> None:
    mock_api.post("/v1/schedules").mock(
        return_value=httpx.Response(
            429, json={"detail": {"code": "quota_exceeded", "message": "Quota de plannings atteint."}}
        )
    )
    r = runner.invoke(
        app,
        ["schedule", "create", "x", "--vm", VM, "--off", "fri:20-mon:08"],
    )
    assert r.exit_code == 1
    assert "Quota de plannings atteint." in r.output


def test_update_replaces_windows(runner, mock_api) -> None:
    cap: dict[str, Any] = {}
    mock_api.patch(f"/v1/schedules/{SID}").mock(side_effect=_capture(cap))
    r = runner.invoke(
        app,
        ["schedule", "update", SID, "--name", "w2", "--off", "fri:19-mon:09"],
    )
    assert r.exit_code == 0, r.output
    assert cap == {
        "name": "w2",
        "windows": [{"start_day": 4, "start_hour": 19, "end_day": 0, "end_hour": 9}],
    }


def test_update_nothing(runner, mock_api) -> None:
    r = runner.invoke(app, ["schedule", "update", SID])
    assert r.exit_code == 0
    assert "Rien à modifier" in r.output


def test_enable_disable(runner, mock_api) -> None:
    mock_api.post(f"/v1/schedules/{SID}/enable").mock(
        return_value=httpx.Response(200, json={"id": SID, "name": "w"})
    )
    mock_api.post(f"/v1/schedules/{SID}/disable").mock(
        return_value=httpx.Response(200, json={"id": SID, "name": "w"})
    )
    assert runner.invoke(app, ["schedule", "enable", SID]).exit_code == 0
    assert runner.invoke(app, ["schedule", "disable", SID]).exit_code == 0


def test_delete_yes(runner, mock_api) -> None:
    mock_api.delete(f"/v1/schedules/{SID}").mock(return_value=httpx.Response(204))
    r = runner.invoke(app, ["schedule", "delete", SID, "--yes"])
    assert r.exit_code == 0, r.output
    assert "supprimé" in r.output.lower()


def test_list_renders_state_and_fee(runner, mock_api) -> None:
    mock_api.get("/v1/schedules").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": SID,
                    "name": "weekend",
                    "resource_type": "vm",
                    "resource_id": VM,
                    "current_state": "off",
                    "enabled": True,
                    "windows": [{"start_day": 4, "start_hour": 20, "end_day": 0, "end_hour": 8}],
                    "estimated_monthly_fee_cents": 39,
                }
            ],
        )
    )
    r = runner.invoke(app, ["schedule", "list"])
    assert r.exit_code == 0, r.output
    assert "OFF" in r.output
    assert "0.39" in r.output
