"""Tests pour `cetic k8s templates` — filtres --name / --k8s-version + tri version."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cetic.commands.k8s import _version_sort_key
from cetic.main import app


def _tmpl(os_key: str, name: str, ver: str, os_slug: str = "ubuntu") -> dict[str, Any]:
    return {
        "os_key": os_key,
        "display_name": name,
        "k8s_version": ver,
        "os": os_slug,
        "os_label": os_slug.title(),
        "region": "RNN",
        "built_at": "2026-06-19T10:00:00Z",
    }


_PAYLOAD = [
    _tmpl("kube-v1-34-8", "Ubuntu 24.04 k8s 1.34.8", "v1.34.8"),
    _tmpl("kube-v1-35-1", "Ubuntu 24.04 k8s 1.35.1", "v1.35.1"),
    _tmpl("kube-v1-34-6", "Rocky 9 k8s 1.34.6", "v1.34.6", os_slug="rocky9"),
    _tmpl("kube-v1-33-2", "Flatcar k8s 1.33.2", "v1.33.2", os_slug="flatcar"),
]


@pytest.fixture
def _json_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")


# ── tri version (pure) ───────────────────────────────────────────────────────
def test_version_sort_key_numeric():
    assert _version_sort_key("v1.35.1") > _version_sort_key("v1.34.8")
    assert _version_sort_key("1.34.10") > _version_sort_key("1.34.9")  # pas lexicographique
    assert _version_sort_key(None) == (0,)
    assert _version_sort_key("") == (0,)


def test_templates_grouped_by_name_then_version(runner, mock_api, _json_out):
    # Tri par défaut : nom (OS) croissant PRIMAIRE, version décroissante au sein
    # de chaque OS. _PAYLOAD : os_label = Flatcar < Rocky9 < Ubuntu.
    mock_api.get("/v1/k8s/templates").mock(return_value=httpx.Response(200, json=_PAYLOAD))
    result = runner.invoke(app, ["k8s", "templates"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert [r["os"] for r in rows] == ["Flatcar", "Rocky9", "Ubuntu", "Ubuntu"]
    assert [r["k8s_version"] for r in rows] == ["v1.33.2", "v1.34.6", "v1.35.1", "v1.34.8"]


def test_templates_show_both_pve_tags(runner, mock_api, _json_out):
    # Chaque template expose les DEUX tags PVE : version (os_key) + OS (os_tag).
    payload = [_tmpl("kube-v1-34-8", "Ubuntu 1.34.8", "v1.34.8", os_slug="ubuntu")]
    mock_api.get("/v1/k8s/templates").mock(return_value=httpx.Response(200, json=payload))
    result = runner.invoke(app, ["k8s", "templates"])
    rows = json.loads(result.stdout)
    assert rows[0]["os_key"] == "kube-v1-34-8"
    assert rows[0]["os_tag"] == "ccks-os-ubuntu"


def test_templates_os_tag_dash_when_no_os(runner, mock_api, _json_out):
    payload = [{"os_key": "kube-v1-30-0", "display_name": "legacy", "k8s_version": "v1.30.0",
                "region": "RNN", "built_at": "2026-01-01T00:00:00Z"}]  # pas de champ `os`
    mock_api.get("/v1/k8s/templates").mock(return_value=httpx.Response(200, json=payload))
    result = runner.invoke(app, ["k8s", "templates"])
    rows = json.loads(result.stdout)
    assert rows[0]["os_tag"] == "—"


def test_templates_grouped_by_os_then_version_desc(runner, mock_api, _json_out):
    # Nom (OS) primaire : Rocky9 avant Ubuntu ; au sein d'Ubuntu, version desc.
    payload = [
        _tmpl("kube-v1-34-8-ubuntu", "Ubuntu 24.04 k8s 1.34.8", "v1.34.8"),
        _tmpl("kube-v1-34-8-rocky", "Rocky 9 k8s 1.34.8", "v1.34.8", os_slug="rocky9"),
        _tmpl("kube-v1-35-1", "Ubuntu 24.04 k8s 1.35.1", "v1.35.1"),
    ]
    mock_api.get("/v1/k8s/templates").mock(return_value=httpx.Response(200, json=payload))
    result = runner.invoke(app, ["k8s", "templates"])
    rows = json.loads(result.stdout)
    assert [r["display_name"] for r in rows] == [
        "Rocky 9 k8s 1.34.8",
        "Ubuntu 24.04 k8s 1.35.1",
        "Ubuntu 24.04 k8s 1.34.8",
    ]


def test_templates_filter_name(runner, mock_api, _json_out):
    mock_api.get("/v1/k8s/templates").mock(return_value=httpx.Response(200, json=_PAYLOAD))
    result = runner.invoke(app, ["k8s", "templates", "--name", "rocky"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert [r["os_key"] for r in rows] == ["kube-v1-34-6"]


def test_templates_filter_k8s_version_prefix(runner, mock_api, _json_out):
    mock_api.get("/v1/k8s/templates").mock(return_value=httpx.Response(200, json=_PAYLOAD))
    result = runner.invoke(app, ["k8s", "templates", "--k8s-version", "1.34"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    # Deux templates en 1.34.x : tri par nom (OS) — Rocky9 (1.34.6) avant Ubuntu (1.34.8).
    assert [r["k8s_version"] for r in rows] == ["v1.34.6", "v1.34.8"]


def test_templates_filter_version_accepts_v_prefix(runner, mock_api, _json_out):
    mock_api.get("/v1/k8s/templates").mock(return_value=httpx.Response(200, json=_PAYLOAD))
    result = runner.invoke(app, ["k8s", "templates", "--k8s-version", "v1.35"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert [r["k8s_version"] for r in rows] == ["v1.35.1"]
