"""Provider profiles and Windows-user-bound DPAPI keys, never plaintext keys."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
from uuid import uuid4


class AccountError(ValueError):
    pass


class WindowsProtector:
    def _crypt(self, value, decrypt=False):
        if os.name != "nt":
            raise AccountError("La clave persistente requiere Windows. Usá una clave solo para esta sesión.")
        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
        raw = ctypes.create_string_buffer(value)
        source = Blob(len(value), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
        result = Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        method = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        method.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        method.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise AccountError("No se pudo proteger o recuperar la clave de esta cuenta.")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            ctypes.memset(result.pbData, 0, result.cbData)
            kernel32.LocalFree(result.pbData)

    def protect(self, value):
        return base64.b64encode(self._crypt(value.encode("utf-8"))).decode("ascii")

    def unprotect(self, value):
        try:
            return self._crypt(base64.b64decode(value, validate=True), True).decode("utf-8")
        except (ValueError, UnicodeError):
            raise AccountError("No se pudo recuperar la clave de esta cuenta.") from None


class AccountStore:
    def __init__(self, path, policy, protector=None):
        self.path, self.policy = Path(path), policy
        self.protector = protector or WindowsProtector()

    def _load(self):
        if not self.path.exists():
            return []
        try:
            if self.path.stat().st_size > 256 * 1024:
                raise ValueError()
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved.get("version") != 1 or not isinstance(saved["accounts"], list) or len(saved["accounts"]) > 30:
                raise ValueError()
            accounts = saved["accounts"]
            for item in accounts:
                if item["provider"] not in ("DeepSeek", "OpenAI") or not re.fullmatch(r"[a-f0-9]{32}", item["id"]):
                    raise ValueError()
                if not isinstance(item["alias"], str) or not 1 <= len(item["alias"]) <= 40:
                    raise ValueError()
                self._model(item["model"])
                if "protected_key" in item and (not isinstance(item["protected_key"], str) or len(item["protected_key"]) > 32768):
                    raise ValueError()
            return accounts
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise AccountError("El archivo de cuentas no es válido; no se utilizaron sus claves.") from None

    @staticmethod
    def _model(model):
        if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9_./:-]{1,128}", model):
            raise AccountError("Identificador de modelo no válido.")

    def _save(self, records):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "accounts": records}), encoding="utf-8")
        temporary.replace(self.path)

    def list(self):
        lease = self.policy.begin("providers.read")
        return self.policy.publish(lease, lambda: [{k: v for k, v in item.items() if k != "protected_key"} for item in self._load()])

    def add(self, provider, alias, model, key="", *, persist_key=False):
        if provider not in ("DeepSeek", "OpenAI") or not isinstance(alias, str) or not alias.strip() or len(alias) > 40:
            raise AccountError("Proveedor o alias no válido.")
        self._model(model)
        if not isinstance(key, str) or len(key) > 1024 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise AccountError("Clave no válida.")
        lease = self.policy.begin("providers.write")
        def save():
            records = self._load()
            if len(records) >= 30:
                raise AccountError("Máximo de 30 cuentas en este preview.")
            record = dict(id=uuid4().hex, provider=provider, alias=alias.strip(), model=model)
            if key and persist_key:
                record["protected_key"] = self.protector.protect(key)
            records.append(record)
            self._save(records)
            return {k: v for k, v in record.items() if k != "protected_key"}
        return self.policy.publish(lease, save)

    def key(self, account_id):
        lease = self.policy.begin("providers.read")
        def read():
            for record in self._load():
                if record["id"] == account_id:
                    return self.protector.unprotect(record["protected_key"]) if record.get("protected_key") else ""
            raise AccountError("Cuenta no disponible.")
        return self.policy.publish(lease, read)

    def delete(self, account_id):
        lease = self.policy.begin("providers.write")
        def remove():
            records = self._load()
            self._save([record for record in records if record["id"] != account_id])
        return self.policy.publish(lease, remove)
