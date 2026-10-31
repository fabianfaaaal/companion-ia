"""Bounded direct HTTPS chat adapters. No credentials, prompts or results persist.

Application code must authorize each remote operation before calling, and gate
publication again afterward. Cancel cannot undo data already sent or charges
already incurred. This module never retries requests or changes providers.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import http.client
import json
import re
import socket
import ssl
import threading
import time

from .ollama_client import Cancelled
from .session import final_answer

MAX_RESPONSE = 4 * 1024 * 1024
MAX_MESSAGES = 64
MAX_TEXT = 65536
ENDPOINTS = {
    "deepseek": ("api.deepseek.com", "/chat/completions"),
    "openai": ("api.openai.com", "/v1/chat/completions"),
}
LIMIT_HEADERS = {
    "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests", "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens", "retry-after",
}
USAGE_KEYS = {
    "prompt_tokens", "completion_tokens", "total_tokens",
    "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
}
_ERROR_MESSAGES = {
    "auth": "La API rechazó las credenciales. Revisá la cuenta y la clave.",
    "quota": "La cuenta no tiene saldo disponible para esta operación.",
    "rate": "El proveedor informó un límite de uso. Probá más tarde.",
    "network": "No se pudo completar la conexión con el proveedor.",
    "tls": "No se pudo verificar el certificado seguro del proveedor. Revisá fecha/hora y certificados de Windows; no desactives la verificación.",
    "dns": "Windows no pudo resolver la dirección del proveedor. Revisá Internet o DNS.",
    "timeout": "El proveedor no respondió dentro del tiempo de espera. Probá nuevamente.",
    "blocked": "Windows o un firewall bloqueó la conexión del programa con el proveedor.",
    "service": "El proveedor informó un error de servicio. El respaldo local sigue disponible.",
    "protocol": "El proveedor o la configuración devolvió una respuesta incompatible.",
}


class RemoteError(RuntimeError):
    """Stable error category and safe message; never a provider payload."""

    def __init__(self, code="protocol"):
        self.code = code if code in _ERROR_MESSAGES else "protocol"
        super().__init__(_ERROR_MESSAGES[self.code])


class RemoteClient:
    def __init__(self, provider, api_key):
        if not isinstance(provider, str) or provider.lower() not in ENDPOINTS:
            raise RemoteError("protocol")
        if (not isinstance(api_key, str) or not 1 <= len(api_key) <= 1024
                or any(not 33 <= ord(char) <= 126 for char in api_key)):
            raise RemoteError("auth")
        self.provider = provider.lower()
        self._api_key = api_key
        self._stopped = threading.Event()
        self._operation = threading.Lock()
        self._connection = None
        self._socket = None

    def cancel(self):
        self._stopped.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection = self._connection
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _check(self):
        if self._stopped.is_set():
            raise Cancelled("Solicitud cancelada.")

    @staticmethod
    def _messages(messages):
        if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
            raise RemoteError("protocol")
        total = 0
        for message in messages:
            if (not isinstance(message, dict) or set(message) != {"role", "content"}
                    or message["role"] not in ("system", "user", "assistant")
                    or not isinstance(message["content"], str)):
                raise RemoteError("protocol")
            total += len(message["content"])
            if total > MAX_TEXT:
                raise RemoteError("protocol")
        return [dict(message) for message in messages]

    def _request(self, method, path, payload=None):
        self._check()
        if not self._operation.acquire(blocking=False):
            raise RemoteError("protocol")
        connection = None
        try:
            self._check()
            deadline = time.monotonic() + 120
            host = ENDPOINTS[self.provider][0]
            connection = http.client.HTTPSConnection(host, timeout=10)
            self._connection = connection
            connection.connect()
            self._socket = connection.sock
            self._check()
            if self._socket is None:
                raise RemoteError("network")
            self._socket.settimeout(60)
            body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"Authorization": "Bearer " + self._api_key,
                       "Content-Type": "application/json", "Accept": "application/json",
                       "User-Agent": "CompanionIA/0.0.6"}
            self._check()
            connection.request(method, path, body=body, headers=headers)
            self._check()
            response = connection.getresponse()
            self._check()
            status = response.status
            if status in (401, 403):
                raise RemoteError("auth")
            if status == 402:
                raise RemoteError("quota")
            if status == 429:
                raise RemoteError("rate")
            if status >= 500:
                raise RemoteError("service")
            if status != 200:
                raise RemoteError("protocol")
            limits = {}
            for name, value in response.getheaders():
                if (isinstance(name, str) and name.lower() in LIMIT_HEADERS
                        and isinstance(value, str) and len(value) <= 100
                        and all(32 <= ord(char) <= 126 for char in value)):
                    limits[name.lower()] = value
            parts, size = [], 0
            while True:
                self._check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteError("network")
                self._socket.settimeout(min(30, remaining))
                chunk = response.read1(min(65536, MAX_RESPONSE + 1 - size))
                self._check()
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise RemoteError("protocol")
                parts.append(chunk)
                if response.isclosed() is True:
                    if type(response.length) is int and response.length > 0:
                        raise RemoteError("protocol")
                    break
            data = json.loads(b"".join(parts))
            if not isinstance(data, dict):
                raise RemoteError("protocol")
            return data, limits
        except Cancelled:
            raise
        except RemoteError:
            self._check()
            raise
        except ssl.SSLError:
            self._check()
            raise RemoteError("tls") from None
        except socket.gaierror:
            self._check()
            raise RemoteError("dns") from None
        except TimeoutError:
            self._check()
            raise RemoteError("timeout") from None
        except OSError as error:
            self._check()
            raise RemoteError("blocked" if getattr(error, "winerror", None) == 10013 or error.errno in (13, 10013) else "network") from None
        except http.client.HTTPException:
            self._check()
            raise RemoteError("network") from None
        except (ValueError, TypeError, AttributeError, OverflowError):
            self._check()
            raise RemoteError("protocol") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
            self._connection = self._socket = None
            self._operation.release()

    def complete(self, model, messages, depth="brief", max_tokens=1024):
        self._check()
        if (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,200}", model)
                or depth not in ("brief", "deep")
                or type(max_tokens) is not int or not 1 <= max_tokens <= 16384):
            raise RemoteError("protocol")
        payload = {"model": model, "messages": self._messages(messages), "stream": False}
        if self.provider == "deepseek":
            payload["max_tokens"] = max_tokens
            payload["thinking"] = {"type": "enabled" if depth == "deep" else "disabled"}
            if depth == "deep":
                payload["reasoning_effort"] = "high"
        else:
            payload["max_completion_tokens"] = max_tokens
            payload["store"] = False
        data, limits = self._request("POST", ENDPOINTS[self.provider][1], payload)
        self._check()
        try:
            choices = data["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError()
            choice = choices[0]
            message = choice["message"]
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise ValueError()
            # Ignore reasoning_content, tool_calls and any other provider fields.
            answer = final_answer(message["content"])
            reason = choice.get("finish_reason")
            if reason not in ("stop", "length", "content_filter", None):
                raise ValueError()
            usage = data.get("usage", {})
            if not isinstance(usage, dict):
                usage = {}
            reported = {key: value for key, value in usage.items()
                        if key in USAGE_KEYS and type(value) is int and 0 <= value <= 10**12}
            return {"answer": answer, "usage": reported, "limits": limits,
                    "finish_reason": reason, "truncated": reason == "length"}
        except (KeyError, IndexError, TypeError, ValueError):
            raise RemoteError("protocol") from None

    def balance(self):
        self._check()
        if self.provider != "deepseek":
            return {"reported": False, "is_available": None, "balance_infos": []}
        data, limits = self._request("GET", "/user/balance")
        try:
            if type(data.get("is_available")) is not bool or not isinstance(data.get("balance_infos"), list):
                raise ValueError()
            if len(data["balance_infos"]) > 10:
                raise ValueError()
            balances = []
            for item in data["balance_infos"]:
                if not isinstance(item, dict) or not re.fullmatch("[A-Z]{3}", item.get("currency", "")):
                    raise ValueError()
                balance = {"currency": item["currency"]}
                for key in ("total_balance", "granted_balance", "topped_up_balance"):
                    value = item[key]
                    if not isinstance(value, str) or len(value) > 32:
                        raise ValueError()
                    number = Decimal(value)
                    if not number.is_finite() or not 0 <= number <= 10**12:
                        raise ValueError()
                    balance[key] = value
                balances.append(balance)
            return {"reported": True, "is_available": data["is_available"],
                    "balance_infos": balances, "limits": limits}
        except (ValueError, TypeError, KeyError, InvalidOperation):
            raise RemoteError("protocol") from None
