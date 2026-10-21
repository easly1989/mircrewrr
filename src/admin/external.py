"""Server-side calls to external indexers (status JSON, log SSE).

The browser never talks to an indexer: the panel proxies it, so the indexer
needs no published port and its API key never leaves the server.
Contract: easly1989/mircrewrr#57 (GET /api/status, GET /api/logs, X-Api-Key).
"""

import logging

import requests

logger = logging.getLogger("admin.external")

TIMEOUT = 5  # seconds, connect and read, for every server-side call


def _headers(entry: dict) -> dict:
    return {"X-Api-Key": entry["api_key"]} if entry.get("api_key") else {}


def describe_error(exc: Exception) -> str:
    """Short, user-facing reason; no URLs or internals."""
    if isinstance(exc, requests.Timeout):
        return f"Timed out after {TIMEOUT}s"
    if isinstance(exc, requests.ConnectionError):
        return "Connection failed"
    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code
        return "API key rejected (401)" if code in (401, 403) else f"HTTP {code}"
    return "Invalid response"


def fetch_status(entry: dict) -> dict:
    """Return the indexer status as the panel needs it; never raises."""
    base = {"name": entry["name"], "url": entry["url"]}
    try:
        r = requests.get(f"{entry['url']}/api/status", headers=_headers(entry), timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (requests.RequestException, ValueError) as e:
        logger.debug("External indexer '%s' unreachable: %s", entry["name"], e)
        return {**base, "up": False, "error": describe_error(e)}
    # Unknown fields are ignored; optional objects default to empty.
    return {
        **base,
        "up": True,
        "service": data.get("service"),
        "version": data.get("version"),
        "uptime_seconds": data.get("uptime_seconds"),
        "status": data.get("status") or "ok",
        "site": data.get("site") or {},
        "solver": data.get("solver") or {},
        "queue": data.get("queue") or [],
        "errors": data.get("errors") or [],
    }


def call_settings(entry: dict, method: str, path: str, body=None):
    """GET/PUT /api/settings or POST /api/restart (animeworldtv-indexer#21).

    Returns (json, status): the indexer's own answer for 2xx and 400 (validation
    message), otherwise a short error with 502. Never raises.
    """
    try:
        r = requests.request(method, f"{entry['url']}{path}", headers=_headers(entry), json=body, timeout=TIMEOUT)
        if r.status_code == 404:
            return {"error": "This indexer has no settings API"}, 404
        if r.status_code != 400:
            r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return data, r.status_code
    except (requests.RequestException, ValueError) as e:
        logger.warning("External indexer '%s' %s %s failed: %s", entry["name"], method, path, e)
        return {"error": describe_error(e)}, 502


def open_logs(entry: dict) -> requests.Response:
    """Open the indexer's SSE log stream. Raises requests.RequestException."""
    # Read timeout > the 15 s keepalive the contract promises, so an idle stream stays open.
    r = requests.get(
        f"{entry['url']}/api/logs",
        headers=_headers(entry),
        stream=True,
        timeout=(TIMEOUT, 30),
    )
    try:
        r.raise_for_status()
    except requests.RequestException:
        r.close()
        raise
    return r
