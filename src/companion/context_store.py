"""Versioned, consented context cards and explicitly reviewed learning.

The SQLite archive is shared with notes. OFF uses RAM only and never reads the
archive. This module neither learns from arbitrary model text nor grants sensors.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from uuid import uuid4

from .memory_store import MemoryStore, MemoryStoreError
from .policy import Denied, State

TEMPLATES = {
    "Identidad": "Cómo querés que te llamen y qué datos generales elegís compartir.",
    "Preferencias": "Formas de conversar, intereses y preferencias que querés recordar.",
    "Personas": "Una persona, su vínculo y hechos que vos confirmaste.",
    "Mascotas": "Una mascota, sus características y cuidados informados.",
    "Proyectos": "Objetivo, estado, decisiones y próximos pasos de un proyecto.",
    "Rutinas": "Hábitos y actividades. Esto no programa recordatorios.",
    "Lugares": "Un lugar y por qué resulta relevante para vos.",
    "Temas": "Un tema de estudio, trabajo o interés y sus fuentes.",
}


def normalize_phrase(value):
    """Unicode/case/accent normalized complete words, never substring keys."""
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", text, flags=re.UNICODE))


def _now():
    return datetime.now(timezone.utc).isoformat()


class ContextStore(MemoryStore):
    def __init__(self, path: Path, policy):
        super().__init__(path, policy)
        self.enabled = False
        self._cards = {}
        self._proposals = {}

    def set_enabled(self, enabled):
        if self._policy.state is not State.ACTIVE:
            self.clear_session()
            self.enabled = False
            return
        value = bool(enabled)
        if value != self.enabled:
            self.clear_session()
        self.enabled = value

    def clear_session(self):
        self._cards.clear()
        self._proposals.clear()

    def _access(self, write, callback):
        if self._policy.state is not State.ACTIVE:
            raise Denied("Las fichas no están disponibles en privado o con el núcleo bloqueado.")
        if self.enabled:
            return self._run("memory.write" if write else "memory.read", callback)
        # A separate gate also revokes results if state changes before publish.
        self._policy.grant("context.session")
        lease = self._policy.begin("context.session")
        return self._policy.publish(lease, callback)

    @staticmethod
    def _schema(connection, create=False):
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='component_versions'").fetchone()
        if not exists:
            if not create:
                return False
            connection.execute("CREATE TABLE component_versions(name TEXT PRIMARY KEY, version INTEGER NOT NULL)")
        row = connection.execute("SELECT version FROM component_versions WHERE name='context_cards'").fetchone()
        if row is not None:
            if row[0] != 1:
                raise MemoryStoreError("La versión de fichas no es compatible.")
            return True
        if not create:
            return False
        connection.execute("""CREATE TABLE context_cards(
            id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version=1),
            category TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
            source TEXT NOT NULL, source_ref TEXT NOT NULL,
            version INTEGER NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE context_bindings(
            card_id TEXT NOT NULL REFERENCES context_cards(id) ON DELETE CASCADE,
            phrase TEXT NOT NULL, normalized TEXT NOT NULL,
            PRIMARY KEY(card_id, normalized))""")
        connection.execute("CREATE INDEX context_binding_lookup ON context_bindings(normalized)")
        connection.execute("""CREATE TABLE context_proposals(
            id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES context_cards(id) ON DELETE CASCADE,
            body TEXT NOT NULL, source_ref TEXT NOT NULL, base_version INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('proposed','accepted','rejected')),
            fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, reviewed_at TEXT)""")
        connection.execute("""CREATE TABLE context_revisions(
            card_id TEXT NOT NULL REFERENCES context_cards(id) ON DELETE CASCADE,
            version INTEGER NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(card_id,version))""")
        connection.execute("INSERT INTO component_versions VALUES('context_cards',1)")
        return True

    @staticmethod
    def _validate(category, title, body, keywords, source_ref):
        if category not in TEMPLATES:
            raise ValueError("Elegí una categoría de ficha.")
        MemoryStore._text(title, 120, "Título")
        MemoryStore._text(body, 8000, "Contenido")
        MemoryStore._text(source_ref, 500, "Fuente")
        if not isinstance(keywords, (list, tuple)) or not 1 <= len(keywords) <= 24:
            raise ValueError("Indicá entre 1 y 24 palabras o frases clave.")
        unique = {}
        for phrase in keywords:
            MemoryStore._text(phrase, 128, "Palabra clave")
            normalized = normalize_phrase(phrase)
            if not normalized or len(normalized.split()) > 16:
                raise ValueError("La clave debe tener entre 1 y 16 palabras.")
            unique.setdefault(normalized, phrase.strip())
        return list(unique.values())

    @staticmethod
    def _decode(connection, row):
        card = dict(row)
        card["enabled"] = bool(card["enabled"])
        card["keywords"] = [item[0] for item in connection.execute(
            "SELECT phrase FROM context_bindings WHERE card_id=? ORDER BY normalized", (card["id"],))]
        return card

    def _all(self, connection):
        return [self._decode(connection, row) for row in connection.execute(
            "SELECT * FROM context_cards ORDER BY title COLLATE NOCASE,id")]

    def list_cards(self):
        def read():
            if not self.enabled:
                return deepcopy(sorted(self._cards.values(), key=lambda c: (c["title"].casefold(), c["id"])))
            if not self._path.exists():
                return []
            with closing(self._connect()) as connection:
                if not self._schema(connection):
                    return []
                return self._all(connection)
        return self._access(False, read)

    @staticmethod
    def _write_card(connection, card, *, existing=False):
        columns = ("id", "schema_version", "category", "title", "body", "source", "source_ref", "version", "enabled", "created_at", "updated_at")
        if existing:
            connection.execute("UPDATE context_cards SET " + ",".join(key + "=?" for key in columns[1:]) + " WHERE id=?",
                               tuple(card[key] for key in columns[1:]) + (card["id"],))
            connection.execute("DELETE FROM context_bindings WHERE card_id=?", (card["id"],))
        else:
            connection.execute("INSERT INTO context_cards VALUES(" + ",".join("?" for _ in columns) + ")",
                               tuple(card[key] for key in columns))
        connection.executemany("INSERT INTO context_bindings VALUES(?,?,?)",
                               [(card["id"], phrase, normalize_phrase(phrase)) for phrase in card["keywords"]])
        connection.execute("INSERT INTO context_revisions VALUES(?,?,?,?)",
                           (card["id"], card["version"], json.dumps(card, ensure_ascii=False), card["updated_at"]))

    def save_card(self, category, title, body, keywords, *, source_ref="Declaración confirmada por el usuario", card_id=None, expected_version=None, enabled=True):
        keywords = self._validate(category, title, body, keywords, source_ref)
        if type(enabled) is not bool:
            raise ValueError("Estado de ficha no válido.")
        def save():
            if not self.enabled:
                previous = self._cards.get(card_id) if card_id else None
                if card_id and previous is None:
                    raise ValueError("La ficha ya no existe en esta sesión.")
                card = build(previous)
                self._cards[card["id"]] = card
                return deepcopy(card)
            with closing(self._connect(create=True)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                self._schema(connection, create=True)
                previous = None
                if card_id:
                    row = connection.execute("SELECT * FROM context_cards WHERE id=?", (card_id,)).fetchone()
                    if row is None:
                        raise ValueError("La ficha ya no existe.")
                    previous = self._decode(connection, row)
                card = build(previous)
                self._write_card(connection, card, existing=previous is not None)
                return card
        def build(previous):
            if previous is not None and expected_version != previous["version"]:
                raise ValueError("La ficha cambió. Volvé a abrirla antes de guardar.")
            now = _now()
            return dict(id=previous["id"] if previous else str(uuid4()), schema_version=1,
                        category=category, title=title.strip(), body=body.strip(),
                        source="user_confirmed", source_ref=source_ref.strip(),
                        version=previous["version"] + 1 if previous else 1, enabled=enabled,
                        created_at=previous["created_at"] if previous else now, updated_at=now,
                        keywords=keywords)
        return self._access(True, save)

    def delete_card(self, card_id):
        self._text(card_id, 128, "Identificador")
        def delete():
            if not self.enabled:
                removed = self._cards.pop(card_id, None)
                self._proposals = {key: p for key, p in self._proposals.items() if p["card_id"] != card_id}
                return removed is not None
            if not self._path.exists():
                return False
            with closing(self._connect(create=True)) as connection, connection:
                if not self._schema(connection):
                    return False
                return connection.execute("DELETE FROM context_cards WHERE id=?", (card_id,)).rowcount > 0
        return self._access(True, delete)

    def retrieve(self, text, selections=None):
        if not isinstance(text, str) or len(text) > 4000:
            raise ValueError("Consulta de fichas no válida.")
        selections = selections or {}
        if not isinstance(selections, dict):
            raise ValueError("Selección de fichas no válida.")
        normalized = normalize_phrase(text)
        tokens = normalized.split()
        # All bounded complete phrases, queried through the binding index.
        candidates = {" ".join(tokens[start:end]) for start in range(len(tokens))
                      for end in range(start + 1, min(len(tokens), start + 16) + 1)}
        def read():
            if not self.enabled:
                cards = deepcopy(list(self._cards.values()))
            else:
                if not self._path.exists() or not candidates:
                    return {"matches": [], "ambiguities": []}
                with closing(self._connect()) as connection:
                    if not self._schema(connection):
                        return {"matches": [], "ambiguities": []}
                    identifiers = set()
                    ordered = sorted(candidates)
                    for offset in range(0, len(ordered), 200):
                        batch = ordered[offset:offset + 200]
                        identifiers.update(row[0] for row in connection.execute(
                            "SELECT DISTINCT card_id FROM context_bindings WHERE normalized IN (" + ",".join("?" for _ in batch) + ")", batch))
                    cards = []
                    for identifier in identifiers:
                        row = connection.execute("SELECT * FROM context_cards WHERE id=? AND enabled=1", (identifier,)).fetchone()
                        if row:
                            cards.append(self._decode(connection, row))
            by_phrase = {}
            by_id = {}
            for card in cards:
                if not card["enabled"]:
                    continue
                by_id[card["id"]] = card
                for phrase in card["keywords"]:
                    key = normalize_phrase(phrase)
                    if key in candidates:
                        by_phrase.setdefault(key, set()).add(card["id"])
            occurrences = []
            for key in by_phrase:
                words = key.split()
                for start in range(len(tokens) - len(words) + 1):
                    if tokens[start:start + len(words)] == words:
                        occurrences.append((start, start + len(words), key))
            occupied, selected = set(), []
            for start, end, key in sorted(occurrences, key=lambda entry: (-(entry[1]-entry[0]), entry[0], entry[2])):
                span = set(range(start, end))
                if occupied.isdisjoint(span):
                    occupied.update(span)
                    if key not in selected:
                        selected.append(key)
            matches, ambiguous, used = [], [], set()
            for key in selected:
                identifiers = sorted(by_phrase[key])
                chosen = selections.get(key)
                if len(identifiers) > 1 and chosen in identifiers:
                    identifiers = [chosen]
                if len(identifiers) > 1:
                    ambiguous.append(dict(keyword=key, candidates=[dict(id=i, title=by_id[i]["title"]) for i in identifiers]))
                    continue
                card = by_id[identifiers[0]]
                if card["id"] in used:
                    continue
                used.add(card["id"])
                matches.append(dict(id=card["id"], title=card["title"],
                    body=card["body"], source="context_card", source_ref=card["source_ref"],
                    version=card["version"], matched_keywords=[phrase for phrase in selected if card["id"] in by_phrase[phrase] and (len(by_phrase[phrase]) == 1 or selections.get(phrase) == card["id"])],
                    privacy_class="general", status="confirmed"))
            return {"matches": matches, "ambiguities": ambiguous}
        return self._access(False, read)

    def propose(self, card_id, body, *, source_ref):
        self._text(card_id, 128, "Identificador")
        self._text(body, 8000, "Propuesta")
        self._text(source_ref, 500, "Fuente")
        fingerprint = hashlib.sha256((card_id + "\0" + body.strip()).encode("utf-8")).hexdigest()
        def proposal(card):
            return dict(id=str(uuid4()), card_id=card_id, body=body.strip(), source_ref=source_ref.strip(),
                        base_version=card["version"], status="proposed", fingerprint=fingerprint,
                        created_at=_now(), reviewed_at=None)
        def save():
            if not self.enabled:
                if card_id not in self._cards:
                    raise ValueError("La ficha ya no existe.")
                for item in self._proposals.values():
                    if item["fingerprint"] == fingerprint:
                        return deepcopy(item)
                item = proposal(self._cards[card_id])
                self._proposals[item["id"]] = item
                return deepcopy(item)
            with closing(self._connect(create=True)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                self._schema(connection, create=True)
                row = connection.execute("SELECT * FROM context_proposals WHERE fingerprint=?", (fingerprint,)).fetchone()
                if row:
                    return dict(row)
                card = connection.execute("SELECT * FROM context_cards WHERE id=?", (card_id,)).fetchone()
                if card is None:
                    raise ValueError("La ficha ya no existe.")
                item = proposal(card)
                connection.execute("INSERT INTO context_proposals VALUES(?,?,?,?,?,?,?,?,?)", tuple(item.values()))
                return item
        return self._access(True, save)

    def list_proposals(self, *, include_reviewed=False):
        def read():
            if not self.enabled:
                return deepcopy([item for item in self._proposals.values() if include_reviewed or item["status"] == "proposed"])
            if not self._path.exists():
                return []
            with closing(self._connect()) as connection:
                if not self._schema(connection):
                    return []
                query = "SELECT * FROM context_proposals" + ("" if include_reviewed else " WHERE status='proposed'") + " ORDER BY created_at,id"
                return [dict(row) for row in connection.execute(query)]
        return self._access(False, read)

    def review(self, proposal_id, *, accept, edited_body=None):
        self._text(proposal_id, 128, "Identificador")
        if type(accept) is not bool:
            raise ValueError("Revisión no válida.")
        if edited_body is not None:
            self._text(edited_body, 8000, "Propuesta revisada")
        def update(item, card):
            if item["status"] != "proposed":
                raise ValueError("La propuesta ya fue revisada.")
            if accept:
                if card is None or card["version"] != item["base_version"]:
                    raise ValueError("La ficha cambió desde la propuesta. Revisala y creá una propuesta nueva.")
                card = deepcopy(card)
                card.update(body=(edited_body if edited_body is not None else item["body"]).strip(),
                            source_ref=item["source_ref"], version=card["version"] + 1, updated_at=_now())
            item = dict(item, status="accepted" if accept else "rejected", reviewed_at=_now())
            return item, card
        def save():
            if not self.enabled:
                if proposal_id not in self._proposals:
                    raise ValueError("La propuesta ya no existe.")
                item = self._proposals[proposal_id]
                reviewed, card = update(item, self._cards.get(item["card_id"]))
                self._proposals[proposal_id] = reviewed
                if accept:
                    self._cards[card["id"]] = card
                return deepcopy(reviewed)
            if not self._path.exists():
                raise ValueError("La propuesta ya no existe.")
            with closing(self._connect(create=True)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if not self._schema(connection):
                    raise ValueError("La propuesta ya no existe.")
                item = connection.execute("SELECT * FROM context_proposals WHERE id=?", (proposal_id,)).fetchone()
                if item is None:
                    raise ValueError("La propuesta ya no existe.")
                row = connection.execute("SELECT * FROM context_cards WHERE id=?", (item["card_id"],)).fetchone()
                card = self._decode(connection, row) if row else None
                reviewed, card = update(dict(item), card)
                if accept:
                    self._write_card(connection, card, existing=True)
                connection.execute("UPDATE context_proposals SET status=?,reviewed_at=? WHERE id=?",
                                   (reviewed["status"], reviewed["reviewed_at"], proposal_id))
                return reviewed
        return self._access(True, save)
