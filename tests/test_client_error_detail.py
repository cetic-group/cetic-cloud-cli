"""Régression : le client CLI doit aplatir le champ ``detail`` structuré des
erreurs API (#618) en message lisible.

Depuis le contrat d'erreur unifié de l'API, certains 4xx/5xx renvoient
``detail`` sous forme d'objet ``{"code", "message", "action_url"}`` au lieu
d'une chaîne. Le CLI n'affiche que ``e.detail`` (et fait parfois ``.lower()``
dessus) : sans normalisation, il imprimerait la repr Python du dict
(``{'code': 'quota_exceeded', ...}``) — sortie illisible. On extrait le
``message``. Les ``detail`` déjà-chaîne (contrat historique) restent intacts.
"""

from __future__ import annotations

import httpx
import pytest

from cetic import client


def test_normalize_dict_detail_prefers_message():
    d = {"code": "quota_exceeded", "message": "Quota dépassé : containers."}
    assert client._normalize_detail(d) == "Quota dépassé : containers."


def test_normalize_dict_detail_falls_back_to_code():
    assert client._normalize_detail({"code": "payment_method_required"}) == "payment_method_required"


def test_normalize_string_detail_unchanged():
    assert client._normalize_detail("Ressource introuvable.") == "Ressource introuvable."


def test_normalize_dict_without_message_or_code_dumps_json():
    out = client._normalize_detail({"foo": "bar"})
    assert "foo" in out and "bar" in out


def test_raise_for_status_flattens_structured_429(mock_api):
    """Un 429 avec detail dict → APIError.detail = message (string), pas la repr dict."""
    mock_api.get("/v1/quota-test").mock(
        return_value=httpx.Response(
            429,
            json={
                "detail": {
                    "code": "quota_exceeded",
                    "message": "Quota dépassé : containers. Ajoutez un moyen de paiement.",
                }
            },
        )
    )
    with pytest.raises(client.APIError) as exc:
        client.get("/v1/quota-test")
    assert isinstance(exc.value.detail, str)
    assert exc.value.detail == "Quota dépassé : containers. Ajoutez un moyen de paiement."
    # Les appelants qui font .lower() sur e.detail (appgw/registry) ne cassent plus.
    assert "quota" in exc.value.detail.lower()
