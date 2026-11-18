"""Direct HTTPS transport paired by a one-time QR."""
import hmac
import ssl
import socket
import os
from datetime import datetime, timezone
from pathlib import Path
import json
from concurrent.futures import Future, TimeoutError
from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Full, Queue
from secrets import token_urlsafe
from threading import Event, Thread
from time import monotonic


def local_ipv4():
    # A UDP connect selects the interface without sending a datagram.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect(("192.0.2.1", 9))
        return route.getsockname()[0]


class SecureServer(HTTPServer):
    def get_request(self):
        sock, address = super().get_request()
        try:
            sock.settimeout(3)
            return self.context.wrap_socket(sock, server_side=True), address
        except (OSError, ssl.SSLError):
            sock.close()
            raise


class PhoneBridge:
    def __init__(self, host):
        from companion.phone_tls import session_tls

        context, fingerprint = session_tls()
        self.token = token_urlsafe(32)
        self.session = ""
        self.phase = "Esperando emparejamiento"
        self.expires = monotonic() + 120
        self.qr = json.dumps({
            "url": f"https://{host}:18763", "pin": fingerprint, "code": self.token,
        }, separators=(",", ":"))
        self.inbox = Queue(maxsize=1)
        self.closed = Event()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_args):
                pass

            def reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                auth = self.headers.get("Authorization", "").encode()
                if bridge.closed.is_set():
                    return self.reply(503, {"error": "Sesión cerrada."})
                if self.path == "/pair":
                    if (bridge.session or monotonic() >= bridge.expires or
                            not hmac.compare_digest(auth, f"Bearer {bridge.token}".encode())):
                        return self.reply(401, {"error": "QR vencido, usado o inválido."})
                    device = self.headers.get("X-Device", "Android")[:120]
                    try:
                        bridge.audit("solicitud", device, self.client_address[0])
                        bridge.audit("conexión", device, self.client_address[0])
                    except OSError:
                        return self.reply(503, {"error": "No se pudo guardar el historial."})
                    bridge.session = token_urlsafe(32)
                    bridge.device = device
                    bridge.origin = self.client_address[0]
                    bridge.token = ""
                    bridge.phase = "Esperando datos"
                    return self.reply(200, {"session": bridge.session})
                if self.path not in ("/chat", "/status"):
                    return self.reply(404, {"error": "Ruta desconocida."})
                if not bridge.session or not hmac.compare_digest(
                        auth, f"Bearer {bridge.session}".encode()):
                    return self.reply(401, {"error": "Volvé a vincular el teléfono."})
                if self.path == "/status":
                    return self.reply(200, {"connected": True})
                bridge.phase = "Recibiendo datos"
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 32768:
                        raise ValueError()
                    body = json.loads(self.rfile.read(size))
                    message = body.get("message") if isinstance(body, dict) else None
                    if not isinstance(message, str) or not 0 < len(message.strip()) <= 4000:
                        raise ValueError()
                except ValueError:
                    bridge.phase = "Esperando datos"
                    return self.reply(400, {"error": "Mensaje inválido."})
                future = Future()
                try:
                    bridge.inbox.put_nowait((message.strip(), future))
                except Full:
                    return self.reply(409, {"error": "Companion está ocupado."})
                deadline = monotonic() + 90
                while not bridge.closed.is_set() and monotonic() < deadline:
                    try:
                        status, payload = future.result(timeout=0.25)
                    except TimeoutError:
                        continue
                    bridge.phase = "Enviando datos"
                    try:
                        return self.reply(status, payload)
                    finally:
                        bridge.phase = "Esperando datos"
                future.cancel()
                if bridge.closed.is_set():
                    return self.reply(503, {"error": "Sesión Android cerrada."})
                bridge.phase = "Esperando datos"
                self.reply(504, {"error": "Companion tardó demasiado."})


        self.server = SecureServer((host, 18763), Handler)
        self.server.context = context
        Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def audit(event, device, origin):
        folder = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CompanionIA"
        folder.mkdir(parents=True, exist_ok=True)
        entry = {"event": event, "at": datetime.now(timezone.utc).isoformat(),
                 "device": device, "origin": origin}
        with (folder / "android-connections.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
            log.flush()
            os.fsync(log.fileno())

    def close(self):
        self.closed.set()
        # A pending HTTP request may be waiting on Qt. Never join it on the GUI thread.
        Thread(target=self._close_server, daemon=True).start()

    def _close_server(self):
        self.server.shutdown()
        self.server.server_close()
        if self.session:
            self.audit("desconexión", self.device, self.origin)
