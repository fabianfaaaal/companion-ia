"""Named boolean privacy presets. They never authorize API charges or intimacy.

The caller applies selected flags through its normal permission controls. Opening,
editing or saving a preset performs no capture, inference or content persistence.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)
from .policy import Denied, State

PROFILE_FLAGS = (
    "memory", "voice", "microphone", "foreground", "environment_context",
    "notices", "history_context", "share_history", "share_memory",
    "share_imports", "share_environment",
)
PROFILE_LABELS = {
    "memory": "Memoria local",
    "voice": "Leer respuestas con voz",
    "microphone": "Permitir dictado por botón",
    "foreground": "Observar títulos de ventanas",
    "environment_context": "Usar contexto del entorno al conversar",
    "notices": "Avisos de agenda con la aplicación abierta",
    "history_context": "Usar historiales importados como contexto local",
    "share_history": "Permitir enviar conversación reciente al proveedor elegido",
    "share_memory": "Permitir enviar notas y descripciones de fotos",
    "share_imports": "Permitir enviar fragmentos de historiales importados",
    "share_environment": "Permitir enviar contexto temporal de Web y Entorno",
}
PROFILE_PRESETS = {
    "Completo": dict.fromkeys(PROFILE_FLAGS, True),
    "Concentrado": {key: key in ("memory", "notices") for key in PROFILE_FLAGS},
    "Solo chat": dict.fromkeys(PROFILE_FLAGS, False),
}
MAX_PROFILES = 20
MAX_BYTES = 64 * 1024


class ProfileError(ValueError):
    """A safe preset/configuration error without file contents or paths."""


def validate_flags(flags):
    if not isinstance(flags, dict) or set(flags) != set(PROFILE_FLAGS):
        raise ProfileError("El perfil no contiene los controles esperados.")
    if any(type(value) is not bool for value in flags.values()):
        raise ProfileError("Los controles del perfil deben ser activados o desactivados.")
    return {key: flags[key] for key in PROFILE_FLAGS}


def validate_name(name):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
        raise ProfileError("Escribí un nombre de entre 1 y 40 caracteres.")
    name = name.strip()
    if not name.isprintable():
        raise ProfileError("El nombre no puede contener saltos de línea ni caracteres de control.")
    return name


class PrivacyProfileStore:
    """Technical settings only. Construction does no filesystem work."""
    def __init__(self, path, policy):
        self.path = Path(path)
        self.policy = policy

    def _load(self):
        if not self.path.exists():
            return [dict(name=name, flags=dict(flags)) for name, flags in PROFILE_PRESETS.items()]
        try:
            if self.path.is_symlink() or self.path.stat().st_size > MAX_BYTES:
                raise ValueError()
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"version", "profiles"} or type(payload["version"]) is not int or payload["version"] != 1:
                raise ValueError()
            records = payload["profiles"]
            if not isinstance(records, list) or len(records) > MAX_PROFILES:
                raise ValueError()
            result, seen = [], set()
            for record in records:
                if not isinstance(record, dict) or set(record) != {"name", "flags"}:
                    raise ValueError()
                name = validate_name(record["name"])
                if name.casefold() in seen:
                    raise ValueError()
                seen.add(name.casefold())
                result.append(dict(name=name, flags=validate_flags(record["flags"])))
            return result
        except (OSError, ValueError, TypeError, KeyError):
            raise ProfileError("No se pudieron leer los perfiles. No se aplicó ninguna configuración.") from None

    def _write(self, records):
        if self.path.is_symlink():
            raise ProfileError("No se guardaron perfiles en una referencia de archivo no segura.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=self.path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"version": 1, "profiles": records}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise ProfileError("No se pudieron guardar los perfiles. No se aplicó ninguna configuración.") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def list_profiles(self):
        lease = self.policy.begin("profiles.read")
        return self.policy.publish(lease, self._load)

    def get(self, name):
        name = validate_name(name)
        lease = self.policy.begin("profiles.read")
        def read():
            for record in self._load():
                if record["name"].casefold() == name.casefold():
                    return dict(record["flags"])
            raise ProfileError("Ese perfil ya no está disponible.")
        return self.policy.publish(lease, read)

    def save(self, name, flags):
        name, flags = validate_name(name), validate_flags(flags)
        lease = self.policy.begin("profiles.write")
        def save():
            records = self._load()
            matches = [index for index, record in enumerate(records) if record["name"].casefold() == name.casefold()]
            record = dict(name=name, flags=flags)
            if matches:
                records[matches[0]] = record
            elif len(records) < MAX_PROFILES:
                records.append(record)
            else:
                raise ProfileError("Ya hay 20 perfiles; eliminá uno o sobrescribí uno existente.")
            self._write(records)
            return dict(name=name, flags=dict(flags))
        return self.policy.publish(lease, save)

    def delete(self, name):
        name = validate_name(name)
        lease = self.policy.begin("profiles.write")
        def remove():
            records = self._load()
            remaining = [record for record in records if record["name"].casefold() != name.casefold()]
            if len(remaining) == len(records):
                return False
            self._write(remaining)
            return True
        return self.policy.publish(lease, remove)


# Short alias for callers that prefer the generic name.
ProfileStore = PrivacyProfileStore


class PrivacyProfilesDialog(QDialog):
    """Return selected_flags only after explicit Apply while policy is ACTIVE."""
    applied = Signal(dict)

    def __init__(self, policy, path, currentflags, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.store = PrivacyProfileStore(path, policy)
        self.selected_flags = None
        self._profiles = []
        self._active = policy.state is State.ACTIVE
        self.setWindowTitle("Perfiles de privacidad")
        self.resize(660, 660)
        layout = QVBoxLayout(self)
        intro = QLabel("Elegí un perfil o ajustá sus controles. Guardar conserva el perfil; Aplicar cambia los controles del programa.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.profile = QComboBox()
        self.profile.setAccessibleName("Perfil de privacidad")
        layout.addWidget(self.profile)
        self.name = QLineEdit()
        self.name.setMaxLength(40)
        self.name.setPlaceholderText("Nombre para guardar este perfil")
        self.name.setAccessibleName("Nombre del perfil")
        layout.addWidget(self.name)
        self.checkboxes = {}
        for key in PROFILE_FLAGS:
            control = QCheckBox(PROFILE_LABELS[key])
            self.checkboxes[key] = control
            layout.addWidget(control)
        actions = QHBoxLayout()
        self.save_button = QPushButton("Guardar perfil")
        self.delete_button = QPushButton("Eliminar perfil")
        self.save_button.clicked.connect(self.save_profile)
        self.delete_button.clicked.connect(self.delete_profile)
        actions.addWidget(self.save_button)
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)
        self.status = QLabel("Configuración actual: todavía no se aplicó otro perfil.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        warning = QLabel("Un perfil no autoriza cargos de API ni desbloquea recuerdos íntimos. El permiso de micrófono solo habilita Dictar; no inicia una captura. El programa sigue aplicando memoria OFF y modo privado.")
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#9cb3c7; font-size:12px;")
        layout.addWidget(warning)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText("Aplicar controles")
        self.apply_button.clicked.connect(self.apply_profile)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.profile.activated.connect(self.select_profile)
        try:
            flags = validate_flags(currentflags)
        except ProfileError:
            flags = dict.fromkeys(PROFILE_FLAGS, False)
            self.status.setText("La configuración recibida no es válida; revisá los controles antes de aplicar.")
        self._current_flags = dict(flags) if self._active else dict.fromkeys(PROFILE_FLAGS, False)
        self.set_flags(self._current_flags)
        if self._active:
            self.policy.grant("profiles.read")
            self.policy.grant("profiles.write")
            self.reload_profiles()
        else:
            self.profile.addItem("Perfiles bloqueados en modo privado", None)
            self.status.setText("Modo privado: perfiles bloqueados. No se leyó ni aplicó ningún perfil.")
        self._enable(self._active)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.refresh_state)
        self.timer.start()
        self.finished.connect(lambda _: self.timer.stop())

    def _enable(self, active):
        for control in (self.profile, self.name, self.save_button, self.delete_button, self.apply_button, *self.checkboxes.values()):
            control.setEnabled(active)

    def refresh_state(self):
        active = self.policy.state is State.ACTIVE
        self._enable(active)
        if not active and self._active:
            self.selected_flags = None
            self._current_flags = dict.fromkeys(PROFILE_FLAGS, False)
            self._profiles.clear()
            self.profile.blockSignals(True)
            self.profile.clear()
            self.profile.addItem("Perfiles bloqueados en modo privado", None)
            self.profile.blockSignals(False)
            self.name.clear()
            self.set_flags(dict.fromkeys(PROFILE_FLAGS, False))
            self.status.setText("Modo privado: perfiles bloqueados. No se aplicó ningún perfil.")
        if active and not self._active:
            self.policy.grant("profiles.read")
            self.policy.grant("profiles.write")
            self.reload_profiles()
            self.status.setText("Procesamiento reactivado. Elegí o revisá un perfil antes de aplicar.")
        self._active = active

    def flags(self):
        return {key: control.isChecked() for key, control in self.checkboxes.items()}

    def set_flags(self, flags):
        for key, value in validate_flags(flags).items():
            self.checkboxes[key].setChecked(value)

    def reload_profiles(self, select=None):
        try:
            self._profiles = self.store.list_profiles()
        except (Denied, ProfileError):
            self._profiles = []
            self.status.setText("No se pudieron leer los perfiles; no se cambió la configuración actual.")
        self.profile.blockSignals(True)
        self.profile.clear()
        self.profile.addItem("Configuración actual / personalizada", None)
        for record in self._profiles:
            self.profile.addItem(record["name"], record["name"])
            if select is not None and record["name"].casefold() == select.casefold():
                self.profile.setCurrentIndex(self.profile.count() - 1)
        self.profile.blockSignals(False)

    def select_profile(self, index):
        name = self.profile.itemData(index)
        if name is None:
            if self.policy.state is State.ACTIVE:
                self.set_flags(self._current_flags)
                self.name.clear()
                self.status.setText("Configuración actual restaurada para revisar; todavía no se aplicó ningún cambio.")
            return
        try:
            flags = self.store.get(name)
            self.set_flags(flags)
            self.name.setText(name)
            self.status.setText("Perfil cargado para revisar. Falta pulsar Aplicar controles.")
        except (Denied, ProfileError):
            self.status.setText("No se pudo cargar ese perfil; no se aplicó ninguna configuración.")
            self.refresh_state()

    def save_profile(self):
        try:
            self.policy.begin("profiles.write")
            name = validate_name(self.name.text())
            existing = any(record["name"].casefold() == name.casefold() for record in self._profiles)
            if existing and QMessageBox.question(self, "Sobrescribir perfil", "¿Reemplazar los controles guardados de este perfil?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.store.save(name, self.flags())
            self.reload_profiles(name)
            self.status.setText("Perfil guardado. Sus controles todavía no se aplicaron al programa.")
        except (Denied, ProfileError):
            self.status.setText("No se guardó el perfil. Revisá nombre, límite de perfiles y estado de privacidad.")
            self.refresh_state()

    def delete_profile(self):
        try:
            self.policy.begin("profiles.write")
            name = self.profile.currentData()
            if name is None:
                self.status.setText("Elegí un perfil guardado para eliminar.")
                return
            if QMessageBox.question(self, "Eliminar perfil", "¿Eliminar este perfil guardado? No borra conversaciones ni recuerdos.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.store.delete(name)
            self.reload_profiles()
            self.status.setText("Perfil eliminado. Los controles actuales no cambiaron.")
        except (Denied, ProfileError):
            self.status.setText("No se pudo eliminar el perfil.")
            self.refresh_state()

    def apply_profile(self):
        try:
            lease = self.policy.begin("profiles.read")
            flags = validate_flags(self.flags())
            self.policy.publish(lease, lambda: setattr(self, "selected_flags", dict(flags)))
        except (Denied, ProfileError):
            self.status.setText("No se aplicó el perfil: el procesamiento está bloqueado.")
            self.refresh_state()
            return
        self.accept()
        self.applied.emit(dict(flags))
