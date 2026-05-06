"""Client HTTP CETIC Cloud Platform API.

Gère automatiquement :
- Injection du Bearer token depuis la config
- Refresh automatique si 401
- Affichage d'erreurs en français
"""

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
    token = _get_token()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def get(path: str, params: dict[str, Any] | None = None) -> Any:
    url = config.get_api_url().rstrip("/") + path
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=_headers(), params=params)
    _raise_for_status(resp)
    return resp.json()


def post(path: str, json: dict[str, Any] | None = None) -> Any:
    url = config.get_api_url().rstrip("/") + path
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, headers=_headers(), json=json)
    _raise_for_status(resp)
    return resp.json() if resp.content else None


def patch(path: str, json: dict[str, Any] | None = None) -> Any:
    url = config.get_api_url().rstrip("/") + path
    with httpx.Client(timeout=30) as client:
        resp = client.patch(url, headers=_headers(), json=json)
    _raise_for_status(resp)
    return resp.json() if resp.content else None


def put(path: str, json: dict[str, Any] | None = None) -> Any:
    url = config.get_api_url().rstrip("/") + path
    with httpx.Client(timeout=30) as client:
        resp = client.put(url, headers=_headers(), json=json)
    _raise_for_status(resp)
    return resp.json() if resp.content else None


def delete(path: str) -> None:
    url = config.get_api_url().rstrip("/") + path
    with httpx.Client(timeout=30) as client:
        resp = client.delete(url, headers=_headers())
    _raise_for_status(resp)


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    raise APIError(resp.status_code, detail)
