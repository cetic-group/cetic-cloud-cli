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


def test_templates_sorted_major_desc(runner, mock_api, _json_out):
    mock_api.get("/v1/k8s/templates").mock(return_value=httpx.Response(200, json=_PAYLOAD))
    result = runner.invoke(app, ["k8s", "templates"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    versions = [r["k8s_version"] for r in rows]
    assert versions == ["v1.35.1", "v1.34.8", "v1.34.6", "v1.33.2"]


def test_templates_same_version_sorted_by_name_asc(runner, mock_api, _json_out):
    # Deux templates en MÊME version → départage par nom croissant (a→z).
    payload = [
        _tmpl("kube-v1-34-8-ubuntu", "Ubuntu 24.04 k8s 1.34.8", "v1.34.8"),
        _tmpl("kube-v1-34-8-rocky", "Rocky 9 k8s 1.34.8", "v1.34.8", os_slug="rocky9"),
        _tmpl("kube-v1-35-1", "Ubuntu 24.04 k8s 1.35.1", "v1.35.1"),
    ]
    mock_api.get("/v1/k8s/templates").mock(return_value=httpx.Response(200, json=payload))
    result = runner.invoke(app, ["k8s", "templates"])
    rows = json.loads(result.stdout)
    # v1.35.1 d'abord (majeure en haut), puis les deux v1.34.8 par nom : Rocky < Ubuntu.
    assert [r["display_name"] for r in rows] == [
        "Ubuntu 24.04 k8s 1.35.1",
        "Rocky 9 k8s 1.34.8",
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
    # Deux templates en 1.34.x, triés décroissant.
    assert [r["k8s_version"] for r in rows] == ["v1.34.8", "v1.34.6"]


def test_templates_filter_version_accepts_v_prefix(runner, mock_api, _json_out):
    mock_api.get("/v1/k8s/templates").mock(return_value=httpx.Response(200, json=_PAYLOAD))
    result = runner.invoke(app, ["k8s", "templates", "--k8s-version", "v1.35"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert [r["k8s_version"] for r in rows] == ["v1.35.1"]
