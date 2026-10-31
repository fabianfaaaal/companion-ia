"""Explicit public search requests, bounded responses, no arbitrary URL fetching."""
import html
import http.client
import ipaddress
import json
import re
import socket
import threading
import time
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

MAX_BYTES = 1024 * 1024


class SearchError(RuntimeError):
    pass


def public_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            return False
        if not host or "." not in host or host.lower().endswith((".local", ".localhost")):
            return False
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_global
        except ValueError:
            return True
    except ValueError:
        return False


def clean(text, limit=1000):
    return html.unescape(re.sub("<[^>]+>", "", str(text)))[:limit]


def parse_results(raw, provider):
    if len(raw) > MAX_BYTES:
        raise SearchError("La respuesta supera el límite.")
    result = []
    try:
        if provider == "Bing":
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ValueError
            root = ElementTree.fromstring(raw)
            for item in root.findall("./channel/item"):
                url = item.findtext("link", "")
                title = clean(item.findtext("title", ""), 240)
                if title and public_url(url):
                    result.append(dict(title=title, url=url, snippet=clean(item.findtext("description", "")), provider="Bing"))
        elif provider == "Wikipedia":
            payload = json.loads(raw)
            for item in payload.get("query", {}).get("search", []):
                if type(item.get("pageid")) is not int:
                    continue
                result.append(dict(title=clean(item.get("title", ""), 240),
                    url="https://es.wikipedia.org/?curid=" + str(item["pageid"]),
                    snippet=clean(item.get("snippet", "")), provider="Wikipedia"))
        else:
            raise ValueError
    except (ValueError, TypeError, AttributeError, ElementTree.ParseError):
        raise SearchError("El proveedor devolvió una respuesta no válida.") from None
    return result[:5]


class SearchClient:
    def __init__(self):
        self.cancelled = threading.Event()
        self.connection = None
        self.sock = None

    def cancel(self):
        self.cancelled.set()
        sock = self.sock
        if sock:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        if self.connection:
            self.connection.close()

    def search(self, query, provider="Bing"):
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise SearchError("Escribí una consulta de hasta 500 caracteres.")
        if provider == "Bing":
            host, path = "www.bing.com", "/search?" + urlencode({"format": "rss", "q": query, "setlang": "es"})
        elif provider == "Wikipedia":
            host, path = "es.wikipedia.org", "/w/api.php?" + urlencode({
                "action": "query", "list": "search", "srsearch": query, "srlimit": 5, "format": "json"})
        else:
            raise SearchError("Proveedor no permitido.")
        if self.cancelled.is_set():
            raise SearchError("Búsqueda cancelada.")
        connection = http.client.HTTPSConnection(host, timeout=3)
        self.connection = connection
        try:
            connection.connect()
            self.sock = connection.sock
            if self.cancelled.is_set():
                raise SearchError("Búsqueda cancelada.")
            self.sock.settimeout(2)
            connection.request("GET", path, headers={"User-Agent": "CompanionIA/0.0.4", "Accept": "application/json,application/rss+xml,text/xml"})
            response = connection.getresponse()
            if response.status != 200:
                raise SearchError("El proveedor no respondió correctamente. Podés probar otro.")
            parts, size, deadline = [], 0, time.monotonic() + 5
            while True:
                if self.cancelled.is_set() or time.monotonic() > deadline:
                    raise SearchError("Búsqueda cancelada o demorada.")
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                parts.append(chunk); size += len(chunk)
                if size > MAX_BYTES:
                    raise SearchError("La respuesta supera el límite.")
            return parse_results(b"".join(parts), provider)
        except (OSError, http.client.HTTPException):
            raise SearchError("No se pudo conectar al proveedor web.") from None
        finally:
            connection.close()
            self.connection = self.sock = None
