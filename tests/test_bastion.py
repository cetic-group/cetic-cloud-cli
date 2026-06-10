"""Tests pour `cetic bastion` (list/get/create/delete/ca/revoke/krl) + `cetic ssh`."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from cetic.main import app


BASTION_ID = "11111111-2222-3333-4444-555555555555"
VPC_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _bastion(
    bid: str = BASTION_ID,
    name: str = "edge",
    region: str = "PAR",
    status: str = "running",
    endpoint_host: str = "bastion.par.cloud.cetic-group.com",
) -> dict[str, Any]:
    return {
        "id": bid,
        "name": name,
        "region": region,
        "vpc_id": VPC_ID,
        "status": status,
        "endpoint_host": endpoint_host,
        "endpoint_port": 22,
        "created_at": "2026-06-09T10:00:00Z",
    }


# ---------------------------------------------------------------------------
# Enregistrement des sous-apps / commande de premier niveau
# ---------------------------------------------------------------------------


def test_bastion_subapp_registered():
    names = [g.name for g in app.registered_groups]
    assert "bastion" in names


def test_ssh_top_level_command_registered():
    names = [c.name for c in app.registered_commands]
    assert "ssh" in names


# ---------------------------------------------------------------------------
# list / get
# ---------------------------------------------------------------------------


def test_list_table(runner, mock_api):
    mock_api.get("/v1/bastions").mock(
        return_value=httpx.Response(200, json=[_bastion(), _bastion(name="edge2")])
    )
    result = runner.invoke(app, ["bastion", "list"])
    assert result.exit_code == 0, result.stdout
    assert "edge" in result.stdout
    assert "PAR" in result.stdout
    assert "bastion.par.cloud.cetic-group.com" in result.stdout


def test_get(runner, mock_api):
    mock_api.get(f"/v1/bastions/{BASTION_ID}").mock(
        return_value=httpx.Response(200, json=_bastion())
    )
    result = runner.invoke(app, ["bastion", "get", BASTION_ID])
    assert result.exit_code == 0, result.stdout
    assert "edge" in result.stdout


def test_get_404(runner, mock_api):
    mock_api.get(f"/v1/bastions/{BASTION_ID}").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    result = runner.invoke(app, ["bastion", "get", BASTION_ID])
    assert result.exit_code == 1
    assert "introuvable" in result.stdout


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_sends_correct_body(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=_bastion(name="edge", region="RNN"))

    mock_api.post("/v1/bastions").mock(side_effect=_capture)
    result = runner.invoke(
        app,
        ["bastion", "create", "--name", "edge", "--region", "RNN", "--vpc", VPC_ID],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"name": "edge", "region": "RNN", "vpc_id": VPC_ID}
    assert "Bastion créé" in result.stdout


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_success(runner, mock_api):
    mock_api.delete(f"/v1/bastions/{BASTION_ID}").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["bastion", "delete", BASTION_ID, "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "supprimé" in result.stdout


def test_delete_aborted_without_yes(runner, mock_api):
    result = runner.invoke(app, ["bastion", "delete", BASTION_ID], input="n\n")
    assert result.exit_code != 0
    assert not any(call.request.method == "DELETE" for call in mock_api.calls)


# ---------------------------------------------------------------------------
# ca
# ---------------------------------------------------------------------------


def test_ca_default_user(runner, mock_api):
    mock_api.get("/v1/ssh/ca/user/public").mock(
        return_value=httpx.Response(200, json={"public_key": "ssh-ed25519 AAAAUSERCA ca@user"})
    )
    result = runner.invoke(app, ["bastion", "ca"])
    assert result.exit_code == 0, result.stdout
    assert "ssh-ed25519 AAAAUSERCA" in result.stdout


def test_ca_host(runner, mock_api):
    mock_api.get("/v1/ssh/ca/host/public").mock(
        return_value=httpx.Response(200, json={"public_key": "ssh-ed25519 AAAAHOSTCA ca@host"})
    )
    result = runner.invoke(app, ["bastion", "ca", "--kind", "host"])
    assert result.exit_code == 0, result.stdout
    assert "AAAAHOSTCA" in result.stdout


def test_ca_invalid_kind(runner, mock_api):
    result = runner.invoke(app, ["bastion", "ca", "--kind", "bogus"])
    assert result.exit_code == 1
    assert "invalide" in result.stdout.lower()
    assert not any(call.request.method == "GET" for call in mock_api.calls)


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------


def test_revoke_by_serial(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    mock_api.post("/v1/ssh/revoke").mock(side_effect=_capture)
    result = runner.invoke(
        app, ["bastion", "revoke", "--serial", "42", "--reason", "lost laptop"]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"serial": 42, "reason": "lost laptop"}
    assert "révoqué" in result.stdout


def test_revoke_by_key_id(runner, mock_api):
    captured: dict[str, Any] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    mock_api.post("/v1/ssh/revoke").mock(side_effect=_capture)
    result = runner.invoke(app, ["bastion", "revoke", "--key-id", "sess-abc"])
    assert result.exit_code == 0, result.stdout
    assert captured["body"] == {"key_id": "sess-abc"}


def test_revoke_requires_serial_or_key_id(runner, mock_api):
    result = runner.invoke(app, ["bastion", "revoke"])
    assert result.exit_code == 1
    assert "--serial" in result.stdout or "--key-id" in result.stdout
    assert not any(call.request.method == "POST" for call in mock_api.calls)


# ---------------------------------------------------------------------------
# krl
# ---------------------------------------------------------------------------


def test_krl_lists_serials_and_key_ids(runner, mock_api):
    mock_api.get("/v1/ssh/krl").mock(
        return_value=httpx.Response(200, json={"serials": [1, 7], "key_ids": ["sess-x"]})
    )
    result = runner.invoke(app, ["bastion", "krl"])
    assert result.exit_code == 0, result.stdout
    assert "7" in result.stdout
    assert "sess-x" in result.stdout


def test_krl_empty(runner, mock_api):
    mock_api.get("/v1/ssh/krl").mock(
        return_value=httpx.Response(200, json={"serials": [], "key_ids": []})
    )
    result = runner.invoke(app, ["bastion", "krl"])
    assert result.exit_code == 0, result.stdout
    assert "Aucun certificat révoqué" in result.stdout


# ---------------------------------------------------------------------------
# cetic ssh — auto-flow (subprocess mocké)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ssh_tooling(monkeypatch, tmp_path):
    """Mock ssh-keygen/ssh : ssh-keygen écrit une fausse .pub, ssh renvoie 0."""
    calls: dict[str, Any] = {"runs": []}

    import cetic.commands.ssh as ssh_mod

    monkeypatch.setattr(ssh_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, *args, **kwargs):
        calls["runs"].append(cmd)

        class _CP:
            returncode = 0

        # ssh-keygen : crée le fichier .pub attendu
        if cmd[0].endswith("ssh-keygen"):
            # -f <path> est le dernier élément
            fidx = cmd.index("-f")
            keypath = cmd[fidx + 1]
            from pathlib import Path as _P
            _P(keypath + ".pub").write_text(
                "ssh-ed25519 AAAAEPHEMERAL cetic-ephemeral\n", encoding="utf-8"
            )
        return _CP()

    monkeypatch.setattr(ssh_mod.subprocess, "run", fake_run)
    return calls


def test_ssh_signs_and_connects(runner, mock_api, fake_ssh_tooling):
    captured: dict[str, Any] = {}

    def _sign(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "certificate": "ssh-ed25519-cert-v01@openssh.com AAAACERT signed",
                "session_id": "sess-1",
                "serial": 99,
                "ttl_seconds": 300,
                "principals": ["root"],
            },
        )

    mock_api.post("/v1/ssh/sign").mock(side_effect=_sign)
    mock_api.get("/v1/bastions").mock(
        return_value=httpx.Response(200, json=[_bastion()])
    )

    result = runner.invoke(app, ["ssh", "10.0.1.42", "--login", "ubuntu", "--ttl", "120"])
    assert result.exit_code == 0, result.stdout

    # Body de signature correct.
    assert captured["body"]["target"] == "10.0.1.42"
    assert captured["body"]["login"] == "ubuntu"
    assert captured["body"]["ttl_seconds"] == 120
    assert captured["body"]["public_key"].startswith("ssh-ed25519 AAAAEPHEMERAL")

    # Une commande ssh a été lancée vers le bastion avec host=<TARGET>.
    ssh_calls = [c for c in fake_ssh_tooling["runs"] if c[0].endswith("/ssh")]
    assert ssh_calls, fake_ssh_tooling["runs"]
    ssh_cmd = ssh_calls[-1]
    assert "ubuntu@bastion.par.cloud.cetic-group.com" in ssh_cmd
    assert "host=10.0.1.42" in ssh_cmd
    assert "IdentitiesOnly=yes" in ssh_cmd
    # Anti-leak : aucun jargon infra.
    assert "LXC" not in result.stdout
    assert "Proxmox" not in result.stdout


def test_ssh_explicit_bastion_skips_listing(runner, mock_api, fake_ssh_tooling):
    mock_api.post("/v1/ssh/sign").mock(
        return_value=httpx.Response(
            200,
            json={
                "certificate": "ssh-cert AAAACERT",
                "session_id": "s",
                "serial": 1,
                "ttl_seconds": 300,
                "principals": ["root"],
            },
        )
    )
    result = runner.invoke(
        app, ["ssh", "web-01", "--bastion", "bastion.custom.example.com"]
    )
    assert result.exit_code == 0, result.stdout
    # Pas d'appel de listing des bastions puisque --bastion fourni.
    assert not any(
        call.request.url.path == "/v1/bastions" for call in mock_api.calls
    )
    ssh_cmd = [c for c in fake_ssh_tooling["runs"] if c[0].endswith("/ssh")][-1]
    assert "root@bastion.custom.example.com" in ssh_cmd


def test_ssh_no_bastion_available(runner, mock_api, fake_ssh_tooling):
    mock_api.post("/v1/ssh/sign").mock(
        return_value=httpx.Response(
            200,
            json={
                "certificate": "ssh-cert AAAACERT",
                "session_id": "s",
                "serial": 1,
                "ttl_seconds": 300,
                "principals": ["root"],
            },
        )
    )
    mock_api.get("/v1/bastions").mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(app, ["ssh", "10.0.1.42"])
    assert result.exit_code == 1
    assert "Aucun bastion" in result.stdout
