"""Local chat conversations in the shared, currently unencrypted memory archive.

Only user/assistant display text and short display labels belong here. Never
store API keys, prepared prompts, system instructions, hidden reasoning or logs.
Construction does no I/O. Every operation needs both conversation and memory
permissions; memory OFF/PRIVATE cannot read or create a chat archive.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from uuid import uuid4

from .memory_store import MemoryStore, MemoryStoreError

MAX_CONVERSATIONS = 500
MAX_MESSAGES = 2000
MAX_MESSAGE_CHARS = 40000
MAX_TEXT_BYTES = 20 * 1024 * 1024
_CONVERSATION_COLUMNS = "id,title,created_at,updated_at"
_MESSAGE_COLUMNS = "id,role,content,label,provider,created_at"


class ConversationStore(MemoryStore):
    def _conversation_run(self, operation, callback):
        lease = self._policy.begin("conversation." + operation)
        return self._policy.publish(lease, lambda: self._run("memory." + operation, callback))

    @staticmethod
    def _schema(connection, create=False):
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        own_tables = {"chat_conversations", "chat_messages"}
        if create:
            connection.execute("CREATE TABLE IF NOT EXISTS component_versions(name TEXT PRIMARY KEY,version INTEGER NOT NULL)")
        elif "component_versions" not in tables:
            if own_tables.intersection(tables):
                raise MemoryStoreError("El archivo de conversaciones no tiene un esquema compatible.")
            return False
        version = connection.execute(
            "SELECT version FROM component_versions WHERE name='conversations'").fetchone()
        if version is not None:
            if version[0] != 1:
                raise MemoryStoreError("La versión de conversaciones no es compatible.")
            connection.execute(f"SELECT {_CONVERSATION_COLUMNS} FROM chat_conversations LIMIT 0")
            connection.execute(f"SELECT {_MESSAGE_COLUMNS},conversation_id,sequence FROM chat_messages LIMIT 0")
            return True
        if own_tables.intersection(tables):
            raise MemoryStoreError("El archivo de conversaciones no tiene un esquema compatible.")
        if not create:
            return False
        connection.execute("""CREATE TABLE chat_conversations(
            id TEXT PRIMARY KEY, title TEXT NOT NULL CHECK(length(title)<=120),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE chat_messages(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE NOT NULL,
            conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('user','assistant','notice')),
            content TEXT NOT NULL CHECK(length(content)<=40000),
            label TEXT NOT NULL CHECK(length(label)<=120),
            provider TEXT NOT NULL CHECK(length(provider)<=128),
            created_at TEXT NOT NULL)""")
        connection.execute("CREATE INDEX chat_messages_order ON chat_messages(conversation_id,sequence)")
        connection.execute("INSERT INTO component_versions(name,version) VALUES('conversations',1)")
        return True

    def _existing_writer(self):
        """Open rw only: rename/delete must not recreate a removed archive."""
        connection = sqlite3.connect(self._path.absolute().as_uri() + "?mode=rw", uri=True, timeout=0.1)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA secure_delete=ON")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise MemoryStoreError("La versión del archivo de memoria no es compatible.")
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _optional_text(value, maximum, label):
        if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
            raise ValueError(label + " no válido.")
        return value

    @staticmethod
    def _usage(connection):
        # Count UTF-8 bytes of retained text, not the physical SQLite file size.
        titles = connection.execute(
            "SELECT COALESCE(SUM(length(CAST(title AS BLOB))),0) FROM chat_conversations").fetchone()[0]
        messages = connection.execute("""SELECT COALESCE(SUM(
            length(CAST(content AS BLOB))+length(CAST(label AS BLOB))+
            length(CAST(provider AS BLOB))),0) FROM chat_messages""").fetchone()[0]
        return titles + messages

    def create(self, title="Nueva conversación"):
        title = self._text(title, 120, "Título").strip()
        def save():
            now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            record = dict(id=str(uuid4()), title=title, created_at=now, updated_at=now)
            with closing(self._connect(create=True)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                self._schema(connection, create=True)
                if connection.execute("SELECT COUNT(*) FROM chat_conversations").fetchone()[0] >= MAX_CONVERSATIONS:
                    raise MemoryStoreError("Se alcanzó el máximo de 500 conversaciones guardadas.")
                if self._usage(connection) + len(title.encode("utf-8")) > MAX_TEXT_BYTES:
                    raise MemoryStoreError("La cuota de texto de conversaciones está completa.")
                connection.execute(
                    "INSERT INTO chat_conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                    tuple(record.values()))
            return record
        return self._conversation_run("write", save)

    def list_conversations(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= MAX_CONVERSATIONS:
            raise ValueError("El límite debe estar entre 1 y 500.")
        def read():
            if not self._path.exists():
                return []
            with closing(self._connect()) as connection:
                if not self._schema(connection):
                    return []
                return [dict(row) for row in connection.execute(
                    f"SELECT {_CONVERSATION_COLUMNS} FROM chat_conversations ORDER BY updated_at DESC,id LIMIT ?",
                    (limit,))]
        return self._conversation_run("read", read)

    def messages(self, conversation_id):
        self._text(conversation_id, 128, "Identificador")
        def read():
            if not self._path.exists():
                return []
            with closing(self._connect()) as connection:
                if not self._schema(connection):
                    return []
                return [dict(row) for row in connection.execute(
                    f"SELECT {_MESSAGE_COLUMNS} FROM chat_messages WHERE conversation_id=? ORDER BY sequence",
                    (conversation_id,))]
        return self._conversation_run("read", read)

    def append(self, conversation_id, role, content, label="", provider="", *, message_id=None):
        self._text(conversation_id, 128, "Identificador")
        if not isinstance(role, str) or role not in {"user", "assistant", "notice"}:
            raise ValueError("Rol de mensaje no válido.")
        content = self._text(content, MAX_MESSAGE_CHARS, "Mensaje")
        label = self._optional_text(label, 120, "Etiqueta")
        provider = self._optional_text(provider, 128, "Proveedor")
        if message_id is not None:
            self._text(message_id, 128, "Identificador de mensaje")
        def save():
            if not self._path.exists():
                raise MemoryStoreError("La conversación no está disponible.")
            now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            record = dict(id=message_id or str(uuid4()), role=role, content=content,
                          label=label, provider=provider, created_at=now)
            with closing(self._existing_writer()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if not self._schema(connection) or connection.execute(
                        "SELECT id FROM chat_conversations WHERE id=?", (conversation_id,)).fetchone() is None:
                    raise MemoryStoreError("La conversación no está disponible.")
                existing = connection.execute("SELECT conversation_id,role,content,label,provider,created_at FROM chat_messages WHERE id=?", (record["id"],)).fetchone()
                if existing is not None:
                    if tuple(existing[key] for key in ("conversation_id","role","content","label","provider")) != (conversation_id,role,content,label,provider):
                        raise MemoryStoreError("El identificador de mensaje ya pertenece a otro contenido.")
                    record["created_at"] = existing["created_at"]
                    return record
                count = connection.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE conversation_id=?", (conversation_id,)).fetchone()[0]
                if count >= MAX_MESSAGES:
                    raise MemoryStoreError("La conversación alcanzó el máximo de 2000 mensajes.")
                incoming = sum(len(value.encode("utf-8")) for value in (content, label, provider))
                if self._usage(connection) + incoming > MAX_TEXT_BYTES:
                    raise MemoryStoreError("La cuota de texto de conversaciones está completa.")
                connection.execute("""INSERT INTO chat_messages
                    (id,role,content,label,provider,created_at,conversation_id) VALUES(?,?,?,?,?,?,?)""",
                    (*record.values(), conversation_id))
                connection.execute(
                    "UPDATE chat_conversations SET updated_at=? WHERE id=?", (now, conversation_id))
            return record
        return self._conversation_run("write", save)

    def rename(self, conversation_id, title):
        self._text(conversation_id, 128, "Identificador")
        title = self._text(title, 120, "Título").strip()
        def save():
            if not self._path.exists():
                return False
            with closing(self._existing_writer()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if not self._schema(connection):
                    return False
                row = connection.execute(
                    "SELECT title FROM chat_conversations WHERE id=?", (conversation_id,)).fetchone()
                if row is None:
                    return False
                difference = len(title.encode("utf-8")) - len(row["title"].encode("utf-8"))
                if difference > 0 and self._usage(connection) + difference > MAX_TEXT_BYTES:
                    raise MemoryStoreError("La cuota de texto de conversaciones está completa.")
                return connection.execute("UPDATE chat_conversations SET title=?,updated_at=? WHERE id=?",
                    (title, datetime.now(timezone.utc).isoformat(timespec="microseconds"), conversation_id)).rowcount == 1
        return self._conversation_run("write", save)

    def delete(self, conversation_id):
        self._text(conversation_id, 128, "Identificador")
        def remove():
            if not self._path.exists():
                return False
            with closing(self._existing_writer()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if not self._schema(connection):
                    return False
                # FK cascade removes every message in the same transaction.
                return connection.execute(
                    "DELETE FROM chat_conversations WHERE id=?", (conversation_id,)).rowcount == 1
        return self._conversation_run("write", remove)
