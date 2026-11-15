"""Tests des buckets à clé fournie par le client (--encryption, présigné)."""
from __future__ import annotations

import json
from typing import Any

import httpx

from cetic.main import app

BID = "11111111-2222-3333-4444-555555555555"
PLACEHOLDER = "<à fournir par le client>"
KEY_MATERIAL = "c2VjcmV0LWtleS1tYXRlcmlhbA=="


def _capture(mock_api, reponse: dict[str, Any]) -> dict[str, Any]:
    cap: dict[str, Any] = {}

    def _h(req: httpx.Request) -> httpx.Response:
        cap.update(json.loads(req.content))
        return httpx.Response(201, json=reponse)

    mock_api.post("/v1/buckets").mock(side_effect=_h)
    return cap


def test_create_sse_c_envoie_encryption_mode(runner, mock_api) -> None:
    cap = _capture(mock_api, {"id": BID})
    r = runner.invoke(app, ["bucket", "create", "-n", "coffre", "-r", "RNN", "--encryption", "sse-c"])
    assert r.exit_code == 0, r.output
    assert cap == {"name": "coffre", "region": "RNN", "encryption_mode": "sse_c"}


def test_create_sans_option_charge_inchangee(runner, mock_api) -> None:
    cap = _capture(mock_api, {"id": BID})
    r = runner.invoke(app, ["bucket", "create", "-n", "coffre", "-r", "RNN"])
    assert r.exit_code == 0, r.output
    assert cap == {"name": "coffre", "region": "RNN"}


def test_create_refus_api_affiche_le_message(runner, mock_api) -> None:
    mock_api.post("/v1/buckets").mock(return_value=httpx.Response(
        422, json={"detail": "Le chiffrement à clé client n'est pas activé."}))
    r = runner.invoke(app, ["bucket", "create", "-n", "c", "-r", "RNN", "--encryption", "sse-c"])
    assert r.exit_code == 1
    assert "pas activé" in r.output


def _bucket(**kw: Any) -> dict[str, Any]:
    b = {"id": BID, "name": "coffre", "region": "RNN", "status": "active"}
    b.update(kw)
    return b


def test_get_affiche_le_libelle(runner, mock_api) -> None:
    mock_api.get(f"/v1/buckets/{BID}").mock(
        return_value=httpx.Response(200, json=_bucket(encryption_mode="sse_c")))
    r = runner.invoke(app, ["bucket", "get", BID])
    assert r.exit_code == 0, r.output
    assert "Chiffrement" in r.output and "clé client" in r.output


def test_get_mode_inconnu_et_absent(runner, mock_api) -> None:
    mock_api.get(f"/v1/buckets/{BID}").mock(
        return_value=httpx.Response(200, json=_bucket(encryption_mode="kms_futur")))
    r = runner.invoke(app, ["bucket", "get", BID])
    assert "kms_futur" in r.output
    mock_api.get(f"/v1/buckets/{BID}").mock(return_value=httpx.Response(200, json=_bucket()))
    r = runner.invoke(app, ["bucket", "get", BID])
    assert "aucun" in r.output


def test_list_affiche_la_colonne(runner, mock_api) -> None:
    mock_api.get("/v1/buckets").mock(return_value=httpx.Response(200, json=[
        _bucket(encryption_mode="sse_c"), _bucket(id="x2", name="b", encryption_mode="zzz"),
        _bucket(id="x3", name="c")]))
    r = runner.invoke(app, ["bucket", "list"])
    assert r.exit_code == 0, r.output
    assert "Chiffrement" in r.output and "clé client" in r.output
    assert "zzz" in r.output and "aucun" in r.output


def test_get_json_intact(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    src = _bucket(encryption_mode="sse_c", encryption={"client_key_required": True})
    mock_api.get(f"/v1/buckets/{BID}").mock(return_value=httpx.Response(200, json=src))
    r = runner.invoke(app, ["bucket", "get", BID])
    assert json.loads(r.output) == src


def _presign(mock_api, **kw: Any) -> None:
    rep = {"url": "https://s3.test/signed", "key": "k", "method": "PUT",
           "expires_at": "2026-11-01T10:00:00Z", "password_protected": False}
    rep.update(kw)
    mock_api.post(f"/v1/buckets/{BID}/presign").mock(return_value=httpx.Response(200, json=rep))


SSE_HEADERS = {
    "x-amz-server-side-encryption-customer-algorithm": "AES256",
    "x-amz-server-side-encryption-customer-key": PLACEHOLDER,
    "x-amz-server-side-encryption-customer-key-MD5": PLACEHOLDER,
}


def test_presign_affiche_les_en_tetes(runner, mock_api) -> None:
    _presign(mock_api, required_headers=SSE_HEADERS)
    r = runner.invoke(app, ["bucket", "presign", BID, "k", "-m", "PUT"])
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert "En-têtes à envoyer avec la requête" in out
    assert "AES256" in out
    assert "customer-key-MD5" in out
    assert "<votre valeur>" in out
    assert PLACEHOLDER not in out
    assert "jamais envoyés à CETIC" in out


def test_presign_sans_materiel_de_cle(runner, mock_api) -> None:
    _presign(mock_api, required_headers=SSE_HEADERS)
    r = runner.invoke(app, ["bucket", "presign", BID, "k"])
    assert KEY_MATERIAL not in r.output


def test_presign_bornes_1806_intactes(runner, mock_api) -> None:
    _presign(mock_api, required_headers={"Content-Length": "2048", "Content-Type": "image/png"})
    r = runner.invoke(app, ["bucket", "presign", BID, "k", "-m", "PUT",
                            "--content-length", "2048", "--content-type", "image/png"])
    assert "à l'identique" in r.output and "<votre valeur>" not in r.output


def test_presign_json_intact(runner, mock_api, monkeypatch) -> None:
    monkeypatch.setenv("CCP_OUTPUT", "json")
    _presign(mock_api, required_headers=SSE_HEADERS)
    r = runner.invoke(app, ["bucket", "presign", BID, "k"])
    assert json.loads(r.output)["required_headers"] == SSE_HEADERS
