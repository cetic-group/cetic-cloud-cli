"""Client HTTP CETIC Cloud Platform API.

Gère automatiquement :
- Injection du Bearer token depuis la config
- Refresh automatique du JWT expiré (401) via le `refresh_token` stocké
- Affichage d'erreurs en français
"""

import json
from typing import Any

import httpx

from cetic import config


class APIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{status_code}] {detail}")


def _get_token() -> str | None:
    return config.get("api_key")


def _headers() -> dict[str, str]:
    from cetic import __version__
    headers = {
        "X-CCP-Client": "cli",
        "User-Agent": f"cetic-cli/{__version__}",
    }
    token = _get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _try_refresh() -> bool:
    """Rafraîchit le JWT d'accès expiré via le `refresh_token` stocké.

    Le JWT d'accès (posé par `cetic auth login` / `--sso`) est court (~15 min) ;
    sans ce refresh, toute commande lancée passé ce délai échouait en 401
    « Token invalide ou expiré » (l'utilisateur devait re-login). Best-effort :
    aucune exception ne remonte (un refresh raté laisse le 401 d'origine
    s'afficher). Appel httpx direct — jamais via `_request` — pour éviter toute
    récursion de refresh. Retourne True si un nouveau token a été persisté.
    """
    refresh_token = config.get("refresh_token")
    if not refresh_token:
        return False
    url = config.get_api_url().rstrip("/") + "/v1/auth/refresh"
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json={"refresh_token": refresh_token})
        if not resp.is_success:
            return False
        data = resp.json()
    except Exception:
        return False
    access = data.get("access_token")
    if not access:
        return False
    config.set_value("api_key", access)
    # Rotation éventuelle du refresh token côté serveur.
    if data.get("refresh_token"):
        config.set_value("refresh_token", data["refresh_token"])
    return True


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    """Exécute la requête ; sur 401, tente UN refresh du JWT puis rejoue."""
    url = config.get_api_url().rstrip("/") + path
    with httpx.Client(timeout=30) as client:
        resp = client.request(method, url, headers=_headers(), params=params, json=json)
        # JWT expiré → refresh transparent (une seule fois) + rejeu.
        if resp.status_code == 401 and _try_refresh():
            resp = client.request(
                method, url, headers=_headers(), params=params, json=json
            )
    return resp


def get(path: str, params: dict[str, Any] | None = None) -> Any:
    resp = _request("GET", path, params=params)
    _raise_for_status(resp)
    return resp.json()


def post(path: str, json: dict[str, Any] | None = None) -> Any:
    resp = _request("POST", path, json=json)
    _raise_for_status(resp)
    return resp.json() if resp.content else None


def patch(path: str, json: dict[str, Any] | None = None) -> Any:
    resp = _request("PATCH", path, json=json)
    _raise_for_status(resp)
    return resp.json() if resp.content else None


def put(path: str, json: dict[str, Any] | None = None) -> Any:
    resp = _request("PUT", path, json=json)
    _raise_for_status(resp)
    return resp.json() if resp.content else None


def delete(path: str) -> Any:
    """DELETE — retourne le body JSON décodé si présent (ex: bulk delete),
    sinon None pour les 204."""
    resp = _request("DELETE", path)
    _raise_for_status(resp)
    if resp.content:
        try:
            return resp.json()
        except Exception:
            return None
    return None


def _normalize_detail(detail: Any) -> str:
    """Aplatit le champ ``detail`` d'une erreur API en message lisible.

    Depuis le contrat d'erreur structuré de l'API (#618), certains 4xx/5xx
    renvoient ``detail`` sous forme d'objet ``{"code": ..., "message": ...,
    "action_url": ...}`` au lieu d'une simple chaîne. Le CLI n'affichant que
    ``e.detail`` (et faisant parfois ``.lower()`` dessus), on extrait le
    ``message`` lisible et on retombe sur ``code`` puis sur le JSON brut. Les
    ``detail`` déjà-chaîne (contrat historique) passent tels quels — le CLI
    reste compatible quel que soit le format renvoyé par l'API.
    """
    if isinstance(detail, dict):
        msg = detail.get("message") or detail.get("code")
        if msg:
            return str(msg)
        return json.dumps(detail, ensure_ascii=False)
    return detail if isinstance(detail, str) else str(detail)


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    try:
        detail = _normalize_detail(resp.json().get("detail", resp.text))
    except Exception:
        detail = resp.text
    raise APIError(resp.status_code, detail)
