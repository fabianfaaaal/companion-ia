"""Bounded Ollama availability diagnostic; never performs inference."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_RESPONSE_BYTES = 1024 * 1024
Status = Literal["unavailable", "invalid_response", "no_models", "available"]


@dataclass(frozen=True)
class Diagnostic:
    status: Status
    model_count: int = 0


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _InvalidResponse(Exception):
    pass


def _get_json(opener, url: str, timeout: float) -> dict:
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        if response.status != 200:
            raise _InvalidResponse
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise _InvalidResponse
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _InvalidResponse from exc
    if not isinstance(value, dict):
        raise _InvalidResponse
    return value


def diagnose(*, port: int = 11434, timeout: float = 2.0) -> Diagnostic:
    """Check version and model inventory on IPv4 loopback, bypassing proxies.

    This reports API reachability and inventory only. It cannot establish GPU
    availability, successful chat, or whether models use external services.
    """
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a finite positive number")
    import math

    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    opener = build_opener(ProxyHandler({}), _RejectRedirects())
    base_url = f"http://127.0.0.1:{port}"
    try:
        version = _get_json(opener, base_url + "/api/version", timeout)
        if not isinstance(version.get("version"), str) or not version["version"].strip():
            raise _InvalidResponse
        inventory = _get_json(opener, base_url + "/api/tags", timeout)
        models = inventory.get("models")
        if not isinstance(models, list):
            raise _InvalidResponse
        if any(
            not isinstance(model, dict)
            or not isinstance(model.get("name"), str)
            or not model["name"].strip()
            for model in models
        ):
            raise _InvalidResponse
    except (HTTPError, _InvalidResponse):
        return Diagnostic("invalid_response")
    except (URLError, TimeoutError, socket.timeout, OSError):
        return Diagnostic("unavailable")
    return Diagnostic("available" if models else "no_models", len(models))


def main() -> int:
    result = diagnose()
    print(f"status={result.status} model_count={result.model_count}")
    return 0 if result.status in {"available", "no_models"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
