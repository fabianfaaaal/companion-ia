"""Manual photo moments, sharing the note database; originals never go to an LLM.

Photos are user-selected general material. Metadata here is supplied by the user,
not visual recognition. No GPS/EXIF extraction, automatic capture or encryption.
"""
from __future__ import annotations
import hashlib
import io
import re
import warnings
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from .memory_store import MemoryStore, MemoryStoreError

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 20_000_000
QUOTA_BYTES = 256 * 1024 * 1024


class MediaStore(MemoryStore):
    def _media_run(self, kind, callback):
        lease = self._policy.begin("media." + kind)
        return self._run("memory." + kind, lambda: self._policy.publish(lease, callback))

    @property
    def _root(self):
        root = self._path.parent / "media"
        if root.is_symlink():
            raise MemoryStoreError("El directorio multimedia no es seguro.")
        return root

    def _safe_path(self, relative):
        if not isinstance(relative, str) or not relative:
            raise MemoryStoreError("La referencia multimedia no es válida.")
        root = self._root.resolve()
        candidate = self._root / relative
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
            raise MemoryStoreError("La referencia multimedia no es segura.")
        return candidate

    @staticmethod
    def _schema(connection, create=False):
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='component_versions'").fetchone()
        if not exists:
            if not create:
                return False
            connection.execute("CREATE TABLE IF NOT EXISTS component_versions(name TEXT PRIMARY KEY, version INTEGER NOT NULL)")
        row = connection.execute("SELECT version FROM component_versions WHERE name='media'").fetchone()
        if row is not None:
            if row[0] != 1:
                raise MemoryStoreError("La versión multimedia no es compatible.")
            return True
        if not create:
            return False
        connection.execute("""CREATE TABLE media_assets(
            id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, mime TEXT NOT NULL,
            byte_size INTEGER NOT NULL, relpath TEXT NOT NULL, thumbrelpath TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE moments(
            id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL CHECK(schema_version=1),
            title TEXT NOT NULL CHECK(length(title)<=120), body TEXT NOT NULL CHECK(length(body)<=8000),
            observed_at TEXT NOT NULL, created_at TEXT NOT NULL,
            asset_id TEXT NOT NULL REFERENCES media_assets(id), source TEXT NOT NULL CHECK(source='user_import'))""")
        connection.execute("CREATE VIRTUAL TABLE moments_fts USING fts5(moment_id UNINDEXED,title,body)")
        connection.execute("INSERT INTO component_versions(name,version) VALUES('media',1)")
        return True

    def _usage(self):
        if not self._root.exists():
            return 0
        total = 0
        for path in self._root.rglob("*"):
            if path.is_symlink():
                raise MemoryStoreError("El archivo multimedia contiene referencias no seguras.")
            if path.is_file():
                total += path.stat().st_size
        return total

    @staticmethod
    def _photo(raw):
        try:
            from PIL import Image
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    format_name = image.format
                    if format_name not in {"JPEG", "PNG", "WEBP"}:
                        raise ValueError
                    width, height = image.size
                    if width < 1 or height < 1 or max(width,height) > 8000 or width*height > MAX_PIXELS:
                        raise ValueError
                    if getattr(image, "n_frames", 1) != 1:
                        raise ValueError
                    image.verify()
                with Image.open(io.BytesIO(raw)) as image:
                    image.load()
                    image.thumbnail((320,320))
                    clean = Image.new("RGB", image.size)
                    clean.paste(image.convert("RGB"))
                    thumbnail = io.BytesIO()
                    clean.save(thumbnail, format="PNG")
            mime, suffix = {"JPEG":("image/jpeg","jpg"),"PNG":("image/png","png"),"WEBP":("image/webp","webp")}[format_name]
            return mime, suffix, thumbnail.getvalue()
        except ImportError:
            raise MemoryStoreError("El soporte de fotografías no está instalado.") from None
        except Exception:
            raise MemoryStoreError("La fotografía no es válida o supera los límites permitidos.") from None

    def import_photo(self, path: Path, title: str, body: str) -> dict:
        title = self._text(title,120,"Título")
        if not isinstance(body,str) or len(body)>8000 or "\x00" in body:
            raise ValueError("Descripción no válida.")
        source = Path(path)
        def save():
            if not source.is_file() or source.is_symlink() or source.stat().st_size > MAX_BYTES:
                raise MemoryStoreError("La fotografía no es válida o supera el límite de 20 MiB.")
            with source.open("rb") as stream:
                raw = stream.read(MAX_BYTES+1)
            if not raw or len(raw)>MAX_BYTES:
                raise MemoryStoreError("La fotografía supera el límite permitido.")
            mime,suffix,thumbnail = self._photo(raw)
            digest = hashlib.sha256(raw).hexdigest()
            created = []
            try:
                with closing(self._connect(create=True)) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self._schema(connection, create=True)
                    asset = connection.execute("SELECT * FROM media_assets WHERE sha256=?",(digest,)).fetchone()
                    if asset is None:
                        if self._usage()+len(raw)+len(thumbnail)>QUOTA_BYTES:
                            raise MemoryStoreError("La cuota multimedia de 256 MiB está completa.")
                        self._root.mkdir(parents=True,exist_ok=True)
                        relative,thumbrelative = digest+"."+suffix,digest+".thumb.png"
                        for name,data in ((relative,raw),(thumbrelative,thumbnail)):
                            target=self._safe_path(name)
                            with target.open("xb") as stream:
                                created.append(target)
                                stream.write(data)
                        asset_id=str(uuid4())
                        connection.execute("INSERT INTO media_assets VALUES(?,?,?,?,?,?)",(asset_id,digest,mime,len(raw),relative,thumbrelative))
                    else:
                        asset_id=asset["id"]
                        original=self._safe_path(asset["relpath"])
                        thumb=self._safe_path(asset["thumbrelpath"])
                        if not original.is_file() or not thumb.is_file() or original.stat().st_size>MAX_BYTES or hashlib.sha256(original.read_bytes()).hexdigest()!=digest:
                            raise MemoryStoreError("La fotografía guardada no supera la comprobación de integridad.")
                    now=datetime.now(timezone.utc).isoformat()
                    moment=dict(id=str(uuid4()),schema_version=1,title=title,body=body,observed_at=now,created_at=now,asset_id=asset_id,source="user_import")
                    connection.execute("INSERT INTO moments VALUES(?,?,?,?,?,?,?,?)",tuple(moment.values()))
                    connection.execute("INSERT INTO moments_fts VALUES(?,?,?)",(moment["id"],title,body))
                moment["thumb_path"]=str(self._safe_path(digest+".thumb.png"))
                return moment
            except BaseException:
                for target in reversed(created):
                    target.unlink(missing_ok=True)
                raise
        return self._media_run("write",save)

    def list_moments(self,limit=25,offset=0):
        limit=self._limit(limit)
        if type(offset) is not int or offset<0 or offset>1_000_000:
            raise ValueError("Página no válida.")
        def read():
            if not self._path.exists(): return []
            with closing(self._connect()) as connection:
                if not self._schema(connection): return []
                rows=connection.execute("SELECT moments.*,media_assets.thumbrelpath FROM moments JOIN media_assets ON media_assets.id=moments.asset_id ORDER BY created_at DESC,id LIMIT ? OFFSET ?",(limit,offset))
                result=[]
                for row in rows:
                    item=dict(row)
                    item["thumb_path"]=str(self._safe_path(item.pop("thumbrelpath")))
                    result.append(item)
                return result
        return self._media_run("read",read)

    def search(self,query,limit=3):
        limit=self._limit(limit)
        if not isinstance(query,str) or len(query)>4000 or "\x00" in query: raise ValueError("Consulta no válida.")
        terms=list(dict.fromkeys(re.findall(r"\w+",query)))[:64]
        expression=" OR ".join('"'+term+'"' for term in terms)
        def read():
            if not expression or not self._path.exists(): return []
            with closing(self._connect()) as connection:
                if not self._schema(connection): return []
                return [dict(row) for row in connection.execute("SELECT moments.id,moments.title,moments.body FROM moments_fts JOIN moments ON moments.id=moments_fts.moment_id WHERE moments_fts MATCH ? ORDER BY rank LIMIT ?",(expression,limit))]
        return self._media_run("read",read)

    def asset_path(self,moment_id,thumbnail=False):
        self._text(moment_id,128,"Identificador")
        if type(thumbnail) is not bool: raise ValueError("Tipo de archivo no válido.")
        def read():
            if not self._path.exists(): raise MemoryStoreError("El recuerdo no existe.")
            with closing(self._connect()) as connection:
                if not self._schema(connection): raise MemoryStoreError("El recuerdo no existe.")
                row=connection.execute("SELECT relpath,thumbrelpath FROM media_assets JOIN moments ON moments.asset_id=media_assets.id WHERE moments.id=?",(moment_id,)).fetchone()
                if row is None: raise MemoryStoreError("El recuerdo no existe.")
                result=self._safe_path(row[1 if thumbnail else 0])
                if not result.is_file(): raise MemoryStoreError("El archivo no está disponible.")
                return result
        return self._media_run("read",read)

    def delete_moment(self,moment_id):
        self._text(moment_id,128,"Identificador")
        def delete():
            if not self._path.exists(): return False
            staged=[]
            try:
                with closing(self._connect(create=True)) as connection,connection:
                    connection.execute("BEGIN IMMEDIATE")
                    if not self._schema(connection): return False
                    row=connection.execute("SELECT asset_id FROM moments WHERE id=?",(moment_id,)).fetchone()
                    if row is None: return False
                    asset_id=row[0]
                    connection.execute("DELETE FROM moments_fts WHERE moment_id=?",(moment_id,))
                    connection.execute("DELETE FROM moments WHERE id=?",(moment_id,))
                    if connection.execute("SELECT 1 FROM moments WHERE asset_id=?",(asset_id,)).fetchone() is None:
                        asset=connection.execute("SELECT relpath,thumbrelpath FROM media_assets WHERE id=?",(asset_id,)).fetchone()
                        for relative in asset:
                            original=self._safe_path(relative)
                            if original.exists():
                                temporary=self._safe_path(str(uuid4())+".deleting")
                                original.rename(temporary)
                                staged.append((original,temporary))
                        connection.execute("DELETE FROM media_assets WHERE id=?",(asset_id,))
            except BaseException:
                for original,temporary in reversed(staged):
                    temporary.rename(original)
                raise
            for _,temporary in staged: temporary.unlink()
            return True
        return self._media_run("write",delete)
