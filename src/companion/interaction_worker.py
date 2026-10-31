"""Local preparation -> explicit remote route -> announced local fallback."""
import threading
import time
from PySide6.QtCore import QThread, Signal
from .ollama_client import OllamaClient, Cancelled, ClientError
from .remote_client import RemoteClient, RemoteError
from .policy import Denied
from .orchestration import planner_messages, apply_plan
from .session import final_answer


class InteractionWorker(QThread):
    result = Signal(int, object)
    failure = Signal(int, str)
    stage = Signal(int, str)
    snapshot = Signal(int, object)

    def __init__(self, job, model, local_messages, remote_messages, route, depth, mode,
                 policy, local_lease, remote_lease=None):
        super().__init__()
        self.job, self.kind, self.model = job, "chat", model
        self.local_messages, self.remote_messages = local_messages, remote_messages
        self.route, self.depth, self.mode = route, depth, mode
        self.policy, self.local_lease, self.remote_lease = policy, local_lease, remote_lease
        self.cancel_event = threading.Event()
        self.client = OllamaClient()
        self.remote = None

    def cancel(self):
        self.cancel_event.set()
        self.client.cancel()
        if self.remote is not None:
            self.remote.cancel()

    def _check(self, remote=False):
        if self.cancel_event.is_set():
            raise Cancelled()
        lease = self.remote_lease if remote else self.local_lease
        if lease is None:
            raise Denied("No se autorizó esta ruta.")
        self.policy.publish(lease, lambda: None)

    def _local(self, messages, maximum):
        self._check()
        if not self.model:
            raise ClientError("No hay modelo local seleccionado. Detectá Ollama.")
        parts, metrics, first = [], {}, None
        started = time.perf_counter()
        for chunk in self.client.stream_chat(self.model, messages, self.cancel_event, max_tokens=maximum):
            self._check()
            if chunk.text:
                if first is None:
                    first = time.perf_counter() - started
                parts.append(chunk.text)
            if chunk.metrics:
                metrics = chunk.metrics
        return final_answer("".join(parts)), metrics, first

    def run(self):
        started = time.perf_counter()
        try:
            fallback_reason = ""
            prepared = False
            if self.route is not None:
                if self.route.get("error") or not self.route.get("allowed") or not self.route.get("key"):
                    fallback_reason = self.route.get("error") or "Cuenta externa no autorizada o sin clave."
                else:
                    outgoing = self.remote_messages
                    if self.route.get("prepare") and self.model:
                        self.stage.emit(self.job, "Preparando instrucciones y contexto con Ollama local…")
                        try:
                            plan, _, _ = self._local(planner_messages(outgoing, self.mode, self.depth), 256)
                            outgoing, prepared = apply_plan(outgoing, plan, self.mode, self.depth)
                        except (ClientError, ValueError):
                            self._check()
                            self.stage.emit(self.job, "El preparador local no respondió correctamente; se conserva el contexto filtrado y tu mensaje original.")
                    self._check(remote=True)
                    self.snapshot.emit(self.job, outgoing)
                    self.stage.emit(self.job, "Consultando " + self.route["provider"] + " con la cuenta seleccionada…")
                    try:
                        self.remote = RemoteClient(self.route["provider"].lower(), self.route["key"])
                        self._check(remote=True)
                        response = self.remote.complete(self.route["model"], outgoing, depth=self.depth, max_tokens=self.route["max_tokens"])
                        self._check(remote=True)
                        self.result.emit(self.job, dict(response, elapsed=time.perf_counter()-started, first=None, metrics={},
                            provider=self.route["provider"], account=self.route["alias"], prepared=prepared,
                            request_messages=outgoing, fallback_reason=""))
                        return
                    except RemoteError as error:
                        fallback_reason = str(error)
                self._check()
                if not self.route.get("fallback"):
                    self.failure.emit(self.job, fallback_reason + " Respaldo local desactivado.")
                    return
                self.stage.emit(self.job, fallback_reason + " Continuando con Ollama local; puede tener menos capacidad. Una solicitud remota fallida podría haber consumido cuota.")
            fallback_messages = self.remote_messages if self.route else self.local_messages
            self.snapshot.emit(self.job, fallback_messages)
            maximum = self.route["max_tokens"] if self.route else (2048 if self.depth == "deep" else 1024)
            answer, metrics, first = self._local(fallback_messages, maximum)
            self.result.emit(self.job, dict(answer=answer, metrics=metrics, first=first,
                elapsed=time.perf_counter()-started, provider="Ollama local", account="", prepared=False,
                usage={}, limits={}, request_messages=fallback_messages, fallback_reason=fallback_reason))
        except (Cancelled, Denied):
            pass
        except (ClientError, ValueError):
            self.failure.emit(self.job, "No se pudo completar la respuesta. Revisá Ollama y el proveedor elegido.")
        except Exception:
            self.failure.emit(self.job, "No se pudo completar la interacción. No se guardó ni reenvió la solicitud a otra cuenta.")
        finally:
            if self.route:
                self.route["key"] = ""
