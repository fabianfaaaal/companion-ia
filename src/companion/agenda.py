"""Manual, opt-in schedules in the shared memory archive. No background service."""
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from .memory_store import MemoryStore, MemoryStoreError

_COLUMNS = "id,title,body,due_at,status,kind,sound,created_at,source"


class AgendaStore(MemoryStore):
    def _agenda_run(self, operation, callback):
        lease = self._policy.begin("agenda." + operation)
        return self._policy.publish(lease, lambda: self._run("memory." + operation, callback))

    @staticmethod
    def _time(value):
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("Fecha no válida.") from None
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("La fecha debe incluir zona horaria.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _stamp(value):
        return value.isoformat(timespec="microseconds")

    @staticmethod
    def _schema(connection, create=False):
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if create:
            connection.execute("CREATE TABLE IF NOT EXISTS component_versions (name TEXT PRIMARY KEY, version INTEGER NOT NULL)")
        elif not {"component_versions", "reminders"}.issubset(tables):
            return False
        version = connection.execute("SELECT version FROM component_versions WHERE name='agenda'").fetchone()
        if version is not None and version[0] != 1:
            raise MemoryStoreError("La versión de agenda no es compatible.")
        if version is None:
            if not create:
                return False
            if "reminders" in tables:
                raise MemoryStoreError("La agenda no tiene un esquema compatible.")
            connection.execute("""CREATE TABLE reminders (
                id TEXT PRIMARY KEY, title TEXT NOT NULL CHECK(length(title)<=120),
                body TEXT NOT NULL CHECK(length(body)<=8000), due_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','delivered','cancelled')),
                kind TEXT NOT NULL CHECK(kind IN ('reminder','alarm')),
                sound INTEGER NOT NULL CHECK(sound IN (0,1)), created_at TEXT NOT NULL,
                source TEXT NOT NULL CHECK(source='user_schedule'))""")
            connection.execute("CREATE INDEX reminders_due ON reminders(status,due_at,id)")
            connection.execute("INSERT INTO component_versions VALUES ('agenda',1)")
        connection.execute(f"SELECT {_COLUMNS} FROM reminders LIMIT 0")
        return True

    @staticmethod
    def _item(row):
        item = dict(row)
        item["sound"] = bool(item["sound"])
        return item

    def add(self, title, body, due_at, kind="reminder", sound=False):
        title = self._text(title, 120, "Título")
        if not isinstance(body, str) or len(body) > 8000 or "\x00" in body:
            raise ValueError("Texto no válido.")
        due = self._time(due_at)
        if kind not in {"reminder", "alarm"} or type(sound) is not bool:
            raise ValueError("Tipo o sonido no válido.")
        def save():
            now = datetime.now(timezone.utc)
            if due <= now:
                raise ValueError("Elegí una fecha futura.")
            item = dict(id=str(uuid4()), title=title, body=body, due_at=self._stamp(due),
                        status="pending", kind=kind, sound=sound, created_at=self._stamp(now), source="user_schedule")
            with closing(self._connect(create=True)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                self._schema(connection, create=True)
                connection.execute(f"INSERT INTO reminders ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?)", tuple(item.values()))
            return item
        return self._agenda_run("write", save)

    def _read(self, where="", parameters=(), limit=100):
        def read():
            if not self._path.exists():
                return []
            with closing(self._connect()) as connection:
                if not self._schema(connection):
                    return []
                return [self._item(row) for row in connection.execute(
                    f"SELECT {_COLUMNS} FROM reminders {where} ORDER BY due_at,id LIMIT ?", (*parameters, limit))]
        return self._agenda_run("read", read)

    def list_items(self, limit=100):
        return self._read(limit=self._limit(limit))

    def due(self, now):
        return self._read("WHERE status='pending' AND due_at<=?", (self._stamp(self._time(now)),))

    def _update(self, item_id, status):
        self._text(item_id, 128, "Identificador")
        def update():
            if not self._path.exists():
                return False
            with closing(self._connect(create=True)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if not self._schema(connection):
                    return False
                return connection.execute("UPDATE reminders SET status=? WHERE id=? AND status='pending'",
                                          (status, item_id)).rowcount == 1
        return self._agenda_run("write", update)

    def mark_delivered(self, item_id):
        return self._update(item_id, "delivered")

    def cancel(self, item_id):
        return self._update(item_id, "cancelled")
