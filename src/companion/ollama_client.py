"""Loopback-only Ollama transport. Call from a worker, never the UI thread.

Policy authorization belongs to the application before work is scheduled and
before results are published. This transport performs no policy callbacks,
configuration reads or persistence. A reachable loopback server is trusted to
report its model metadata honestly; this is not network sandboxing of Ollama.
"""
from __future__ import annotations

import http.client
import json
import socket
import threading
from dataclasses import dataclass
from collections.abc import Iterator

MAX_LINE = 1024 * 1024
MAX_RESPONSE = 4 * 1024 * 1024


class ClientError(RuntimeError):
    """Safe user-facing error, excluding provider content and model names."""


class Cancelled(ClientError):
    pass


@dataclass(frozen=True)
class ChatChunk:
    text: str = ""
    done: bool = False
    metrics: dict | None = None


class _Connection(http.client.HTTPConnection):
    def __init__(self, port: int, stopped: threading.Event):
        super().__init__("127.0.0.1", port, timeout=3)
        self.stopped = stopped

    def connect(self):
        if self.stopped.is_set():
            raise Cancelled("Solicitud cancelada.")
        super().connect()
        if self.stopped.is_set():
            self.close()
            raise Cancelled("Solicitud cancelada.")
        self.interrupt_socket = self.sock


class OllamaClient:
    def __init__(self, *, port: int = 11434):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid loopback port")
        self._port = port
        self._stopped = threading.Event()
        self._state_lock = threading.Lock()
        self._operation = threading.Lock()
        self._connection = None
        self._thinking_required: dict[str, bool] = {}

    def cancel(self) -> None:
        """Interrupt active socket reads; never wait for an inference timeout."""
        self._stopped.set()
        # Do not wait for the request lock: shutdown must interrupt blocked I/O.
        connection = self._connection
        if connection is not None:
            sock = connection.sock or getattr(connection, "interrupt_socket", None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()

    def _check(self, cancel=None):
        if self._stopped.is_set() or (cancel is not None and cancel.is_set()):
            raise Cancelled("Solicitud cancelada.")

    def _begin(self):
        if not self._operation.acquire(blocking=False):
            raise ClientError("Ya hay una solicitud en curso.")
        if self._stopped.is_set():
            self._operation.release()
            raise Cancelled("Solicitud cancelada.")

    def _close(self):
        with self._state_lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def _open(self, method, path, body=None):
        self._check()
        connection = _Connection(self._port, self._stopped)
        with self._state_lock:
            self._connection = connection
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        if payload is not None and len(payload) > MAX_RESPONSE:
            raise ClientError("La solicitud supera el límite permitido.")
        with self._state_lock:
            self._check()
            connection.request(method, path, body=payload, headers={
                "Accept": "application/json", "Content-Type": "application/json",
            })
        self._check()
        if connection.sock is not None:
            connection.sock.settimeout(60)
        response = connection.getresponse()
        self._check()
        if response.status != 200:
            raise ClientError("El motor no pudo completar la solicitud.")
        return response

    def _json(self, method, path, body=None):
        try:
            response = self._open(method, path, body)
            payload = response.read(MAX_RESPONSE + 1)
            self._check()
            if len(payload) > MAX_RESPONSE:
                raise ClientError("La respuesta supera el límite permitido.")
            result = json.loads(payload)
            if not isinstance(result, dict):
                raise ValueError
            return result
        finally:
            self._close()

    @staticmethod
    def _validate_name(model):
        if not isinstance(model, str) or not model.strip() or len(model) > 512:
            raise ClientError("Modelo no válido.")
        if ":cloud" in model.lower():
            raise ClientError("El modelo requiere un servicio remoto.")

    def _verify(self, model):
        self._validate_name(model)
        metadata = self._json("POST", "/api/show", {"model": model})
        if metadata.get("remote_host") or metadata.get("remote_model"):
            raise ClientError("El modelo requiere un servicio remoto.")
        details = metadata.get("details")
        if not isinstance(details, dict) or not isinstance(details.get("family"), str) or not details["family"].strip():
            raise ClientError("No se pudo verificar el modelo local.")
        thinking = metadata.get("thinking", {})
        values = thinking.get("values", []) if isinstance(thinking, dict) else []
        self._thinking_required[model] = isinstance(values, list) and True in values and False not in values
        return True

    def thinking_required(self, model: str) -> bool:
        """Last verified metadata only; no I/O. Reverified before every chat."""
        return self._thinking_required.get(model, False)

    def _safe_error(self, exc):
        self._check()
        if isinstance(exc, ClientError):
            return exc
        return ClientError("No se pudo comunicar con el motor local.")

    def verify_local(self, model: str) -> bool:
        self._begin()
        try:
            return self._verify(model)
        except (OSError, http.client.HTTPException, ValueError, TypeError, ClientError) as exc:
            raise self._safe_error(exc) from None
        finally:
            self._close()
            self._operation.release()

    def list_models(self) -> list[str]:
        self._begin()
        try:
            inventory = self._json("GET", "/api/tags")
            models = inventory.get("models")
            if not isinstance(models, list):
                raise ClientError("Inventario de modelos no válido.")
            result = []
            for entry in models:
                self._check()
                if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                    raise ClientError("Inventario de modelos no válido.")
                name = entry["name"]
                try:
                    self._verify(name)
                except ClientError:
                    self._check()
                    continue
                if name not in result:
                    result.append(name)
            return result
        except (OSError, http.client.HTTPException, ValueError, TypeError, ClientError) as exc:
            raise self._safe_error(exc) from None
        finally:
            self._close()
            self._operation.release()

    def stream_chat(self, model: str, messages: list[dict], cancel: threading.Event) -> Iterator[ChatChunk]:
        self._begin()
        try:
            self._check(cancel)
            self._verify(model)
            self._check(cancel)
            if not isinstance(messages, list) or not messages or any(
                not isinstance(message, dict)
                or message.get("role") not in {"system", "user", "assistant"}
                or not isinstance(message.get("content"), str)
                or set(message) != {"role", "content"}
                for message in messages
            ):
                raise ClientError("Mensajes no válidos.")
            response = self._open("POST", "/api/chat", {
                "model": model, "messages": messages, "stream": True, "think": self.thinking_required(model),
                "options": {"num_ctx": 4096, "num_predict": 1024},
            })
            total = 0
            while True:
                self._check(cancel)
                line = response.readline(MAX_LINE + 1)
                self._check(cancel)
                total += len(line)
                if len(line) > MAX_LINE or total > MAX_RESPONSE:
                    raise ClientError("La respuesta supera el límite permitido.")
                if not line:
                    raise ClientError("La respuesta del motor quedó incompleta.")
                item = json.loads(line)
                if not isinstance(item, dict) or item.get("error"):
                    raise ClientError("El motor devolvió una respuesta no válida.")
                if type(item.get("done")) is not bool:
                    raise ClientError("El motor devolvió una respuesta no válida.")
                message = item.get("message", {})
                if not isinstance(message, dict) or not isinstance(message.get("content", ""), str):
                    raise ClientError("El motor devolvió una respuesta no válida.")
                # Explicit thinking fields must never be forwarded as user text.
                metrics = None
                if item["done"]:
                    keys = ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration")
                    metrics = {key: item[key] for key in keys if type(item.get(key)) in (int, float) and item[key] >= 0}
                yield ChatChunk(message.get("content", ""), item["done"], metrics)
                if item["done"]:
                    return
        except (OSError, http.client.HTTPException, ValueError, TypeError, ClientError) as exc:
            raise self._safe_error(exc) from None
        finally:
            self._close()
            self._operation.release()
