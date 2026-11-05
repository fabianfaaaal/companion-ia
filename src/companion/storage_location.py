"""Locate a user-selected personal archive, independently of app and engines.

The locator contains a directory only. Selecting never moves or removes an old
archive. Invalid locators fail visibly rather than silently opening an empty one.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile


class StorageLocationError(ValueError):
    pass


IDENTITY = {"application": "companion-ia", "version": 1}
LEGACY_FILES = ("memory.sqlite", "privacy.json", "preferences.json", "personality.json")


def _read_json(path, maximum=16384):
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise StorageLocationError("La configuración del archivo no puede ser un enlace.")
    if not path.is_file() or path.stat().st_size > maximum:
        raise StorageLocationError("La configuración del archivo no es válida.")
    return json.loads(path.read_text(encoding="utf-8"))


class StorageLocation:
    def __init__(self, control_dir, forbidden_roots=()):
        self.control_dir = Path(control_dir).absolute()
        self.path = self.control_dir / "archive-location.json"
        self.forbidden_roots = tuple(Path(root).absolute() for root in forbidden_roots)

    @property
    def default_directory(self):
        return self.control_dir / "archivo"

    def _legacy(self):
        # Even a malformed old latch must remain in place so Policy can block it.
        return any((self.control_dir / name).exists() or (self.control_dir / name).is_symlink() for name in LEGACY_FILES)

    def is_configured(self):
        if self.path.exists() or self.path.is_symlink():
            self.resolve()  # Invalid/missing destinations must surface an error.
            return True
        return self._legacy()

    @staticmethod
    def _no_links(path):
        for candidate in (path, *path.parents):
            if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
                raise StorageLocationError("Elegí una carpeta local directa, sin enlaces ni uniones de directorios.")

    def _safe(self, directory, *, legacy=False):
        try:
            raw = os.fspath(directory)
            if not isinstance(raw, str) or not raw or "\0" in raw or raw.startswith(("\\\\", "//")):
                raise StorageLocationError("Elegí una carpeta local, no una ubicación de red.")
            candidate = Path(raw)
            if not candidate.is_absolute():
                raise StorageLocationError("La ubicación debe ser una carpeta absoluta.")
            self._no_links(candidate)
            candidate = candidate.resolve()
            control = self.control_dir.resolve()
            if not legacy and (candidate == control or control.is_relative_to(candidate)):
                raise StorageLocationError("Elegí una subcarpeta propia para el archivo, separada de la configuración del programa.")
            for root in self.forbidden_roots:
                root = root.resolve()
                if candidate == root or candidate.is_relative_to(root) or root.is_relative_to(candidate):
                    raise StorageLocationError("El archivo debe quedar separado del programa, su código y los motores.")
            if candidate.exists() and not candidate.is_dir():
                raise StorageLocationError("La ubicación elegida no es una carpeta.")
            return candidate
        except StorageLocationError:
            raise
        except (OSError, ValueError, TypeError):
            raise StorageLocationError("No se pudo validar la carpeta del archivo.") from None

    @staticmethod
    def _recognized(directory):
        identity = directory / "archive-identity.json"
        if identity.exists() or identity.is_symlink():
            try:
                value = _read_json(identity, 1024)
                return value == IDENTITY and type(value.get("version")) is int
            except (OSError, ValueError, TypeError, AttributeError):
                return False
        latch = directory / "privacy.json"
        if latch.exists():
            try:
                value = _read_json(latch, 1024)
                if isinstance(value, dict) and type(value.get("private")) is bool:
                    return True
            except (OSError, ValueError, TypeError):
                return False
        database = directory / "memory.sqlite"
        if database.exists():
            try:
                StorageLocation._no_links(database)
                with closing(sqlite3.connect(database.absolute().as_uri() + "?mode=ro", uri=True, timeout=0.1)) as connection:
                    connection.execute("PRAGMA trusted_schema=OFF")
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    return version == 1 and {"notes", "consent"}.issubset(tables)
            except (OSError, ValueError, sqlite3.Error):
                return False
        return False

    def validate(self, directory):
        """Validate a candidate without creating directories or saving a locator."""
        candidate = self._safe(directory)
        try:
            if candidate.exists() and any(candidate.iterdir()) and not self._recognized(candidate):
                raise StorageLocationError("La carpeta contiene archivos ajenos o no es un archivo Companion reconocido. Elegí una carpeta vacía o una copia Companion.")
            return candidate
        except OSError:
            raise StorageLocationError("No se pudo acceder a la carpeta elegida.") from None

    def resolve(self):
        try:
            if not self.path.exists() and not self.path.is_symlink():
                if self._legacy():
                    return self._safe(self.control_dir, legacy=True)
                return self._safe(self.default_directory)
            self._no_links(self.control_dir)
            value = _read_json(self.path, 4096)
            if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1 or not isinstance(value.get("directory"), str):
                raise StorageLocationError("La ubicación guardada no es válida. Elegí la carpeta correcta; no se cambió tu archivo.")
            candidate = self._safe(value["directory"])
            if not candidate.exists() or not self._recognized(candidate):
                raise StorageLocationError("No se encontró el archivo Companion configurado. Conectá su unidad o elegí su carpeta; no se creó un archivo vacío.")
            return candidate
        except StorageLocationError:
            raise
        except (OSError, ValueError, TypeError):
            raise StorageLocationError("No se pudo leer la ubicación guardada. No se cambió tu archivo.") from None

    @staticmethod
    def _atomic_json(path, value):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".companion-location-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def select(self, directory):
        candidate = self.validate(directory)
        try:
            self._no_links(self.control_dir)
            self.control_dir.mkdir(parents=True, exist_ok=True)
            candidate.mkdir(parents=True, exist_ok=True)
            # Validate again immediately before writing, including new ancestors.
            candidate = self.validate(candidate)
            identity = candidate / "archive-identity.json"
            if not identity.exists():
                self._atomic_json(identity, IDENTITY)
            self._atomic_json(self.path, {"version": 1, "directory": str(candidate)})
            return candidate
        except StorageLocationError:
            raise
        except OSError:
            raise StorageLocationError("No se pudo guardar la ubicación. Tu archivo anterior no fue movido ni eliminado.") from None
