"""Versioned portable folder snapshots and restart-only restore, without secrets.

All archive access needs memory permission. Data stays in the user data directory;
construction performs no I/O. Models, runtime, API credentials and privacy latch
are deliberately never portable. Restores replace the managed archive, not merge it.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import threading
from uuid import uuid4

from .policy import Policy, Denied, State
from .preferences import Preferences

CONFIG_FILES = frozenset({"preferences.json", "personality.json", "privacy-profiles.json",
    "active-privacy-profile.json", "history-scope.json", "onboarding.json", "provider-options.json"})
MANAGED = tuple(sorted(CONFIG_FILES)) + ("memory.sqlite", "media")
MAX_TOTAL = 768 * 1024 * 1024
MAX_DATABASE = 256 * 1024 * 1024
MAX_FILES = 4096
CHUNK = 128 * 1024


class ArchiveError(ValueError):
    pass


ArchiveTransferError = ArchiveError


class ArchiveCancelled(ArchiveError):
    pass


def _ordinary(path):
    """Reject links/junctions including Windows reparse points and hardlinks."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ArchiveError("La carpeta contiene enlaces no admitidos.")
    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        raise ArchiveError("La carpeta contiene archivos enlazados no admitidos.")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise ArchiveError("La carpeta contiene un tipo de archivo no admitido.")


def _safe(root, relative):
    relative = str(relative)
    parts = PurePosixPath(relative).parts
    if (not parts or "\\" in relative or ":" in relative or
            PurePosixPath(relative).is_absolute() or any(part in (".", "..", "") for part in relative.split("/"))):
        raise ArchiveError("El respaldo contiene una ruta no válida.")
    candidate = root.joinpath(*parts)
    current = root
    if current.exists():
        _ordinary(current)
    for part in parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _ordinary(current)
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ArchiveError("El respaldo contiene una ruta fuera de su carpeta.")
    return candidate


def _allowed(name):
    return name == "memory.sqlite" or name in CONFIG_FILES or bool(re.fullmatch(
        r"media/[a-f0-9]{64}(?:\.(?:jpg|png|webp)|\.thumb\.png)", name))


class ArchiveTransfer:
    def __init__(self, data_dir, policy, cancel_event=None):
        self.directory = Path(data_dir)
        self.policy = policy
        self.cancel_event = cancel_event or threading.Event()
        self.transfer = self.directory / "transfer"
        self.incoming = self.transfer / "incoming"
        self.outgoing = self.transfer / "outgoing"

    def cancel(self):
        self.cancel_event.set()

    def _check(self, lease):
        if self.cancel_event.is_set():
            raise ArchiveCancelled("Operación cancelada.")
        return self.policy.publish(lease, lambda: None)

    def _effect(self, lease, callback):
        self._check(lease)
        return self.policy.publish(lease, callback)

    def _mkdir(self, path, lease):
        if path == self.directory:
            _ordinary(self.directory)
        else:
            _safe(self.directory, path.relative_to(self.directory).as_posix())
        self._effect(lease, lambda: path.mkdir(parents=True, exist_ok=True))

    def folders(self):
        lease = self.policy.begin("memory.read")
        self._mkdir(self.incoming, lease)
        self._mkdir(self.outgoing, lease)
        return self.incoming, self.outgoing

    def _copy(self, source, target, lease):
        self._effect(lease, lambda: _ordinary(source))
        if source.stat().st_size > MAX_DATABASE:
            raise ArchiveError("Un archivo supera el límite de transferencia.")
        self._mkdir(target.parent, lease)
        # Each short read/write revalidates the epoch; no lock across a long copy.
        with self._effect(lease, lambda: source.open("rb")) as reader:
            with self._effect(lease, lambda: target.open("xb")) as writer:
                while True:
                    chunk = self._effect(lease, lambda: reader.read(CHUNK))
                    if not chunk:
                        break
                    self._effect(lease, lambda: writer.write(chunk))
                self._effect(lease, writer.flush)

    def _digest(self, path, lease):
        digest = hashlib.sha256()
        with self._effect(lease, lambda: path.open("rb")) as stream:
            while True:
                chunk = self._effect(lease, lambda: stream.read(CHUNK))
                if not chunk:
                    return digest.hexdigest()
                digest.update(chunk)

    def _json(self, path, lease, limit=1024 * 1024):
        def read():
            _ordinary(path)
            if path.stat().st_size > limit:
                raise ArchiveError("Un archivo de configuración supera el límite.")
            return json.loads(path.read_text(encoding="utf-8"))
        try:
            return self._effect(lease, read)
        except (ValueError, UnicodeError, OSError):
            raise ArchiveError("No se pudo leer un archivo del respaldo.") from None

    def _write_json(self, path, payload, lease):
        self._mkdir(path.parent, lease)
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        def write():
            temporary = path.with_suffix(path.suffix + ".tmp")
            if temporary.exists():
                _ordinary(temporary)
            temporary.write_bytes(raw)
            temporary.replace(path)
        self._effect(lease, write)

    def _discard(self, path):
        # Only discard a unique task-owned directory below transfer, never a user root.
        if path.parent.resolve().is_relative_to(self.transfer.resolve()) and path.exists():
            _safe(self.directory,path.relative_to(self.directory).as_posix())
            _ordinary(path)
            for item in path.rglob("*"):
                _ordinary(item)
            shutil.rmtree(path)

    def _database(self, path, lease, bundle=None):
        if path.stat().st_size > MAX_DATABASE:
            raise ArchiveError("La base supera el límite de 256 MiB.")
        try:
            with closing(sqlite3.connect(path.absolute().as_uri()+"?mode=ro&immutable=1", uri=True)) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                db.set_progress_handler(lambda: self._progress(lease), 1000)
                self._check(lease)
                if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ArchiveError("La versión del archivo no es compatible.")
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ArchiveError("La base de datos no supera la comprobación de integridad.")
                if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():
                    raise ArchiveError("El archivo contiene un esquema no admitido.")
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"notes", "consent", "notes_fts"}.issubset(tables):
                    raise ArchiveError("Faltan tablas obligatorias del archivo.")
                db.execute("SELECT id,schema_version,title,body,created_at,privacy_class,source,consent_id FROM notes LIMIT 0")
                expected = {"notes", "consent", "component_versions", "media_assets", "moments", "reminders",
                    "external_conversations", "chat_conversations", "chat_messages", "sqlite_sequence",
                    "context_cards", "context_bindings", "context_proposals", "context_revisions"}
                fts_roots = {"notes_fts", "moments_fts", "history_fts"}
                expected |= {root+suffix for root in fts_roots for suffix in ("", "_data", "_idx", "_content", "_docsize", "_config")}
                if tables - expected:
                    raise ArchiveError("La base contiene tablas no admitidas por esta versión.")
                for name, sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"):
                    if "CREATE VIRTUAL TABLE" in sql.upper() and (name not in fts_roots or not re.search(r"USING\s+fts5\s*\(",sql,re.I)):
                        raise ArchiveError("La base contiene un módulo no admitido.")
                if "component_versions" in tables:
                    for name, version in db.execute("SELECT name,version FROM component_versions"):
                        if name not in {"agenda", "media", "history", "conversations", "context", "context_cards"} or version != 1:
                            raise ArchiveError("Un componente necesita otra versión de Companion.")
                if "media_assets" in tables and bundle is not None:
                    for digest, size, relative, thumb in db.execute("SELECT sha256,byte_size,relpath,thumbrelpath FROM media_assets"):
                        for relative_name in (relative, thumb):
                            name = "media/" + relative_name
                            if not _allowed(name):
                                raise ArchiveError("Una fotografía tiene una referencia no válida.")
                            file = _safe(bundle, name)
                            if not file.is_file():
                                raise ArchiveError("Falta un original o miniatura del archivo.")
                        original = _safe(bundle, "media/"+relative)
                        if original.stat().st_size != size or self._digest(original, lease) != digest:
                            raise ArchiveError("Una fotografía no supera la comprobación de integridad.")
        except sqlite3.Error:
            self._check(lease)
            raise ArchiveError("La base de datos no es un archivo compatible.") from None

    def _progress(self, lease):
        try:
            self._check(lease)
            return 0
        except (Denied, ArchiveCancelled):
            return 1

    def _snapshot(self, target, lease):
        self._mkdir(target, lease)
        source = self.directory / "memory.sqlite"
        if source.exists():
            self._effect(lease, lambda: _ordinary(source))
            if source.stat().st_size > MAX_DATABASE:
                raise ArchiveError("La base supera el límite de exportación.")
            destination = target / "memory.sqlite"
            with closing(sqlite3.connect(source.absolute().as_uri()+"?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(destination)) as dst:
                    src.backup(dst, pages=32, progress=lambda *_: self._check(lease), sleep=0.01)
        for name in CONFIG_FILES:
            source = self.directory / name
            if source.exists():
                value = self._json(source, lease)
                if not isinstance(value, dict):
                    raise ArchiveError("Una configuración no es válida.")
                self._write_json(target/name, value, lease)
        media = self.directory / "media"
        if media.exists():
            self._effect(lease, lambda: _ordinary(media))
            media_files = sorted(media.iterdir())
            if len(media_files)>MAX_FILES:
                raise ArchiveError("Demasiados archivos multimedia.")
            total = (source.stat().st_size if source.exists() else 0)
            for source in media_files:
                _ordinary(source)
                total += source.stat().st_size
                if total>MAX_TOTAL:
                    raise ArchiveError("El archivo multimedia supera el límite de transferencia.")
                name = "media/"+source.name
                if not _allowed(name) or not source.is_file():
                    raise ArchiveError("El archivo multimedia contiene elementos no admitidos.")
                self._copy(source, target/name, lease)
        entries = []
        total = 0
        for file in sorted(target.rglob("*")):
            _ordinary(file)
            if file.is_file():
                size = file.stat().st_size
                total += size
                if total > MAX_TOTAL or len(entries) >= MAX_FILES:
                    raise ArchiveError("El respaldo supera los límites permitidos.")
                entries.append(dict(path=file.relative_to(target).as_posix(), size=size, sha256=self._digest(file, lease)))
        self._write_json(target/"manifest.json", dict(format="companion-folder", version=1,
            created_at=datetime.now(timezone.utc).isoformat(), files=entries,
            excluded=["API credentials", "models", "runtime", "private latch"]), lease)
        self.validate_bundle(target)
        return target

    def export(self):
        lease = self.policy.begin("memory.read")
        self.folders()
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid4().hex
        staging = self.transfer / "working" / name
        final = self.outgoing / name
        try:
            self._snapshot(staging, lease)
            self._effect(lease, lambda: staging.replace(final))
            return final
        finally:
            self._discard(staging)

    def validate_bundle(self, folder):
        lease = self.policy.begin("memory.read")
        folder = Path(folder)
        _safe(self.directory, folder.relative_to(self.directory).as_posix())
        manifest = self._json(folder/"manifest.json", lease)
        if (not isinstance(manifest, dict) or manifest.get("format") != "companion-folder" or
                type(manifest.get("version")) is not int or manifest["version"] != 1 or
                not isinstance(manifest.get("files"), list) or len(manifest["files"]) > MAX_FILES):
            raise ArchiveError("La carpeta no es un respaldo compatible.")
        seen, total = set(), 0
        for entry in manifest["files"]:
            self._check(lease)
            if (not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"} or
                    not isinstance(entry["path"], str) or not _allowed(entry["path"]) or
                    type(entry["size"]) is not int or entry["size"] < 0 or
                    not isinstance(entry["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}",entry["sha256"])):
                raise ArchiveError("El manifiesto contiene un elemento no válido.")
            name = entry["path"]
            if name.casefold() in seen:
                raise ArchiveError("El manifiesto contiene rutas duplicadas.")
            seen.add(name.casefold())
            total += entry["size"]
            if total > MAX_TOTAL:
                raise ArchiveError("El respaldo supera el límite permitido.")
            file = _safe(folder, name)
            if not file.is_file() or file.stat().st_size != entry["size"] or self._digest(file,lease) != entry["sha256"]:
                raise ArchiveError("La copia está incompleta o fue modificada.")
            if name in CONFIG_FILES and not isinstance(self._json(file,lease),dict):
                raise ArchiveError("Una configuración no es válida.")
        actual = set()
        for item in folder.rglob("*"):
            self._check(lease)
            _ordinary(item)
            if item.is_file():
                actual.add(item.relative_to(folder).as_posix().casefold())
        if actual != seen | {"manifest.json"}:
            raise ArchiveError("La carpeta contiene archivos no declarados.")
        if "memory.sqlite" in seen:
            self._database(folder/"memory.sqlite", lease, folder)
        elif any(name.startswith("media/") for name in seen):
            raise ArchiveError("Las fotos no tienen una base de datos asociada.")
        self._check(lease)
        return manifest

    def scan_incoming(self):
        lease = self.policy.begin("memory.read")
        self.folders()
        result = []
        for folder in sorted(self.incoming.iterdir()):
            self._check(lease)
            _ordinary(folder)
            if folder.is_dir() and (folder/"manifest.json").is_file():
                try:
                    manifest = self.validate_bundle(folder)
                    result.append(dict(name=folder.name, path=folder, files=len(manifest["files"]),
                        bytes=sum(item["size"] for item in manifest["files"])))
                except ArchiveError:
                    # An incomplete pasted folder is ignored until copying completes.
                    continue
        return result

    def prepare_restore(self, folder):
        lease = self.policy.begin("memory.write")
        folder = Path(folder)
        if folder.parent.resolve() != self.incoming.resolve():
            raise ArchiveError("Pegá primero la copia en la carpeta de entrada de Companion.")
        manifest = self.validate_bundle(folder)
        name = uuid4().hex
        staging = self.transfer/"prepared"/name
        try:
            self._mkdir(staging,lease)
            for entry in manifest["files"]:
                self._copy(_safe(folder,entry["path"]),_safe(staging,entry["path"]),lease)
            self._write_json(staging/"manifest.json",manifest,lease)
            self.validate_bundle(staging)
            self._write_json(self.transfer/"pending-restore.json",dict(version=1,prepared=name,state="prepared"),lease)
            return name
        except BaseException:
            self._discard(staging)
            raise

    def apply_pending(self):
        """Startup only, before any stores/widgets are opened."""
        lease = self.policy.begin("memory.write")
        pending = self.transfer/"pending-restore.json"
        if not pending.exists():
            return None
        _safe(self.directory,pending.relative_to(self.directory).as_posix())
        journal = self._json(pending,lease,4096)
        if (not isinstance(journal,dict) or journal.get("version") != 1 or
                not isinstance(journal.get("prepared"),str) or not re.fullmatch(r"[a-f0-9]{32}",journal["prepared"]) or
                journal.get("state") not in {"prepared","applying"}):
            raise ArchiveError("La restauración pendiente no es válida.")
        staging = self.transfer/"prepared"/journal["prepared"]
        rollback = self.transfer/"rollback"/journal["prepared"]
        if journal["state"] == "applying":
            # Any interrupted swap restores exactly the pre-restore managed set.
            self._recover(rollback,lease)
            self._effect(lease,pending.unlink)
            return "Se recuperó automáticamente el archivo anterior tras una restauración interrumpida."
        self.validate_bundle(staging)
        if rollback.exists():
            self._discard(rollback)
        self._mkdir(rollback,lease)
        present = []
        for name in MANAGED:
            source = self.directory/name
            if source.exists():
                _safe(self.directory,name)
                present.append(name)
        self._write_json(rollback/"original.json",dict(version=1,files=present),lease)
        # Copy rollback before any active file is changed. Includes byte-exact configs.
        for name in present:
            source = self.directory/name
            if source.is_dir():
                for file in source.rglob("*"):
                    _ordinary(file)
                    if file.is_file():
                        relative_name=(Path(name)/file.relative_to(source)).as_posix()
                        if not _allowed(relative_name):
                            raise ArchiveError("La copia de recuperación contiene un archivo no admitido.")
                        self._copy(file,rollback/name/file.relative_to(source),lease)
            elif name == "memory.sqlite":
                with closing(sqlite3.connect(source.absolute().as_uri()+"?mode=ro",uri=True)) as src:
                    with closing(sqlite3.connect(rollback/name)) as dst:
                        src.backup(dst,pages=32,progress=lambda *_:self._check(lease),sleep=0.01)
            else:
                self._copy(source,rollback/name,lease)
        checks = []
        for file in sorted(rollback.rglob("*")):
            _ordinary(file)
            if file.is_file():
                checks.append(dict(path=file.relative_to(rollback).as_posix(),size=file.stat().st_size,
                    sha256=self._digest(file,lease)))
        self._write_json(rollback/"recovery-manifest.json",dict(version=1,files=checks),lease)
        self._write_json(pending,dict(journal,state="applying"),lease)
        try:
            self._clear_sidecars(lease)
            for name in MANAGED:
                target = self.directory/name
                if target.exists():
                    if target.is_dir():
                        self._effect(lease,lambda target=target: shutil.rmtree(target))
                    else:
                        self._effect(lease,target.unlink)
            for name in MANAGED:
                source = staging/name
                if source.exists():
                    self._effect(lease,lambda source=source,name=name: source.replace(self.directory/name))
            self._effect(lease,pending.unlink)
            return "Restauración completada. Se conservó una copia de recuperación del archivo anterior."
        except BaseException:
            self._recover(rollback,lease)
            self._effect(lease,pending.unlink)
            raise ArchiveError("La restauración falló; se recuperó el archivo anterior.") from None

    def _clear_sidecars(self,lease):
        for suffix in ("-wal","-shm","-journal"):
            sidecar = self.directory/("memory.sqlite"+suffix)
            if sidecar.exists():
                _ordinary(sidecar)
                self._effect(lease,sidecar.unlink)

    def _recover(self, rollback,lease):
        _safe(self.directory,rollback.relative_to(self.directory).as_posix())
        recovery = self._json(rollback/"recovery-manifest.json",lease)
        entries = recovery.get("files") if isinstance(recovery,dict) else None
        if not isinstance(entries,list) or len(entries)>MAX_FILES:
            raise ArchiveError("La copia de recuperación no es válida.")
        seen=set()
        for entry in entries:
            if (not isinstance(entry,dict) or set(entry)!={"path","size","sha256"} or not isinstance(entry.get("path"),str) or
                    not (entry["path"]=="original.json" or _allowed(entry["path"]))):
                raise ArchiveError("La copia de recuperación contiene una ruta no válida.")
            if entry["path"].casefold() in seen:
                raise ArchiveError("La copia de recuperación contiene rutas duplicadas.")
            seen.add(entry["path"].casefold())
            file=_safe(rollback,entry["path"])
            if not file.is_file() or file.stat().st_size!=entry.get("size") or self._digest(file,lease)!=entry.get("sha256"):
                raise ArchiveError("La copia de recuperación está incompleta; no se modificó el archivo.")
        actual=set()
        for file in rollback.rglob("*"):
            _ordinary(file)
            if file.is_file():
                actual.add(file.relative_to(rollback).as_posix().casefold())
        if "original.json" not in seen or actual != seen|{"recovery-manifest.json"}:
            raise ArchiveError("La copia de recuperación no tiene un inventario completo.")
        original = self._json(rollback/"original.json",lease,4096)
        names = original.get("files") if isinstance(original,dict) else None
        if not isinstance(names,list) or any(name not in MANAGED for name in names):
            raise ArchiveError("No se pudo validar la copia de recuperación.")
        self._clear_sidecars(lease)
        for name in MANAGED:
            target = self.directory/name
            if target.exists():
                _safe(self.directory,name)
                if target.is_dir():
                    self._effect(lease,lambda target=target:shutil.rmtree(target))
                else:
                    self._effect(lease,target.unlink)
            if name in names:
                source = _safe(rollback,name)
                if source.is_dir():
                    for file in source.rglob("*"):
                        _ordinary(file)
                        if file.is_file():
                            self._copy(file,target/file.relative_to(source),lease)
                else:
                    self._copy(source,target,lease)


def apply_pending_restore(data_dir):
    """Finish consented repair first; admit new restore only with memory enabled.

    An interrupted swap may itself have replaced preferences with memory OFF.
    Rolling that already-authorized transaction back restores the prior settings;
    it never opens those contents to the running assistant. PRIVATE remains closed.
    """
    directory = Path(data_dir)
    policy = Policy(directory/"privacy.json")
    pending=directory/"transfer/pending-restore.json"
    if not pending.exists():
        return None
    _safe(directory,"transfer/pending-restore.json")
    if pending.stat().st_size>4096:
        raise ArchiveError("La restauración pendiente no es válida.")
    try:
        journal=json.loads(pending.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        raise ArchiveError("La restauración pendiente no es válida.") from None
    interrupted=isinstance(journal,dict) and journal.get("state")=="applying"
    if policy.state is not State.ACTIVE:
        if interrupted:
            raise ArchiveError("Una restauración requiere recuperación; privado permanece bloqueado.")
        return None
    if not interrupted:
        if not Preferences(directory/"preferences.json").load()["memory"]:
            return None
        profile = directory/"active-privacy-profile.json"
        if profile.exists():
            try:
                saved = json.loads(profile.read_text(encoding="utf-8"))
                if saved.get("memory") is not True:
                    return None
            except (OSError,ValueError,AttributeError):
                return None
    policy.grant("memory.read")
    policy.grant("memory.write")
    policy.set_memory(True)
    return ArchiveTransfer(directory,policy).apply_pending()
