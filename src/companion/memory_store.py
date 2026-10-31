"""Opt-in manual general notes only; no automatic conversation persistence.

Constructing a store does no I/O. Every disk access runs inside a short Policy
publication; callers must choose a private OS data path outside source/docs.
SQLite is not encrypted. Logical deletion does not erase backups or guarantee
physical erasure from storage devices.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .policy import Policy


class MemoryStoreError(RuntimeError):
    """Safe storage failure without note contents, paths or raw SQL."""


_COLUMNS = "id, schema_version, title, body, created_at, privacy_class, source, consent_id"


class MemoryStore:
    def __init__(self, path: Path, policy: Policy):
        self._path = Path(path)
        self._policy = policy

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("El límite debe estar entre 1 y 100.")
        return limit

    @staticmethod
    def _text(value, maximum, label):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
            raise ValueError(f"{label} no válido.")
        return value

    def _connect(self, *, create=False):
        if create:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        uri = self._path.absolute().as_uri() + ("?mode=rwc" if create else "?mode=ro")
        connection = sqlite3.connect(uri, uri=True, timeout=0.1)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            if create:
                connection.execute("PRAGMA secure_delete=ON")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 1 or version < 0:
                raise MemoryStoreError("La versión del archivo de memoria no es compatible.")
            if version == 0:
                existing = connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()
                if existing or not create:
                    raise MemoryStoreError("El archivo de memoria no tiene un esquema compatible.")
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("""CREATE TABLE consent (
                        id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                        scope TEXT NOT NULL CHECK(scope='manual_general_notes'),
                        action TEXT NOT NULL CHECK(action='user_saved_note'))""")
                    connection.execute("""CREATE TABLE notes (
                        id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version=1),
                        title TEXT NOT NULL CHECK(length(title)<=120),
                        body TEXT NOT NULL CHECK(length(body)<=8000),
                        created_at TEXT NOT NULL,
                        privacy_class TEXT NOT NULL CHECK(privacy_class='general'),
                        source TEXT NOT NULL CHECK(source='user_note'),
                        consent_id TEXT NOT NULL REFERENCES consent(id))""")
                    connection.execute("CREATE VIRTUAL TABLE notes_fts USING fts5(note_id UNINDEXED, title, body)")
                    connection.execute("PRAGMA user_version=1")
            connection.execute(f"SELECT {_COLUMNS} FROM notes LIMIT 0")
            connection.execute("SELECT id, created_at, scope, action FROM consent LIMIT 0")
            connection.execute("SELECT note_id, title, body FROM notes_fts LIMIT 0")
            return connection
        except BaseException:
            connection.close()
            raise

    def _run(self, operation, callback):
        lease = self._policy.begin(operation)
        def effect():
            try:
                return callback()
            except (sqlite3.Error, OSError):
                raise MemoryStoreError("No se pudo acceder al archivo de memoria.") from None
        return self._policy.publish(lease, effect)

    def save_note(self, title: str, body: str) -> dict:
        title = self._text(title, 120, "Título")
        body = self._text(body, 8000, "Texto")
        def save():
            created_at = datetime.now(timezone.utc).isoformat()
            note = dict(id=str(uuid4()), schema_version=1, title=title, body=body,
                        created_at=created_at, privacy_class="general", source="user_note", consent_id=str(uuid4()))
            with closing(self._connect(create=True)) as connection, connection:
                connection.execute("INSERT INTO consent VALUES (?, ?, ?, ?)",
                                   (note["consent_id"], created_at, "manual_general_notes", "user_saved_note"))
                connection.execute(f"INSERT INTO notes ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tuple(note.values()))
                connection.execute("INSERT INTO notes_fts (note_id, title, body) VALUES (?, ?, ?)",
                                   (note["id"], title, body))
            return note
        return self._run("memory.write", save)

    def list_notes(self, limit=100) -> list[dict]:
        limit = self._limit(limit)
        def read():
            if not self._path.exists():
                return []
            with closing(self._connect()) as connection:
                return [dict(row) for row in connection.execute(
                    f"SELECT {_COLUMNS} FROM notes ORDER BY created_at DESC, id LIMIT ?", (limit,))]
        return self._run("memory.read", read)

    def search(self, query: str, limit=5) -> list[dict]:
        limit = self._limit(limit)
        if not isinstance(query, str) or len(query) > 4000 or "\x00" in query:
            raise ValueError("Consulta no válida.")
        # Literal Unicode words, never raw FTS syntax. Bound expression complexity.
        terms = list(dict.fromkeys(re.findall(r"\w+", query, flags=re.UNICODE)))[:64]
        expression = " OR ".join('"' + term + '"' for term in terms)
        def read():
            if not expression or not self._path.exists():
                return []
            with closing(self._connect()) as connection:
                columns = ", ".join("notes." + column.strip() for column in _COLUMNS.split(","))
                rows = connection.execute(
                    f"SELECT {columns} FROM notes_fts JOIN notes ON notes.id=notes_fts.note_id "
                    "WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?", (expression, limit))
                return [dict(row) for row in rows]
        return self._run("memory.read", read)

    def delete_note(self, note_id: str) -> bool:
        self._text(note_id, 128, "Identificador")
        def delete():
            if not self._path.exists():
                return False
            # Existing DB only: deletion must not create a new archive.
            uri = self._path.absolute().as_uri() + "?mode=rw"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as connection, connection:
                connection.execute("PRAGMA secure_delete=ON")
                if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise MemoryStoreError("La versión del archivo de memoria no es compatible.")
                row = connection.execute("SELECT consent_id FROM notes WHERE id=?", (note_id,)).fetchone()
                if row is None:
                    return False
                connection.execute("DELETE FROM notes_fts WHERE note_id=?", (note_id,))
                connection.execute("DELETE FROM notes WHERE id=?", (note_id,))
                connection.execute("DELETE FROM consent WHERE id=?", (row[0],))
                return True
        return self._run("memory.write", delete)
