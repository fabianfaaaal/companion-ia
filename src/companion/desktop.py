"""First Windows desktop prototype. Conversation exists in RAM only."""
from __future__ import annotations
import argparse
import json
import os
import sys
import threading
import time
from uuid import uuid4
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths, QThread, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QTabWidget,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget, QSplitter, QInputDialog,
)
from companion.ollama_client import Cancelled, ClientError, OllamaClient
from companion.policy import Denied, Policy, State
from companion.session import build_messages, final_answer, capabilities, direct_reply
from companion.memory_panel import MemoryPanel
from companion.memory_store import MemoryStoreError
from companion.voice_panel import VoicePanel
from companion.audio_io import AudioError, AudioIO
from companion.agenda_panel import AgendaPanel
from companion.cinematheque_panel import CinemathequePanel
from companion.environment_panel import EnvironmentPanel
from companion.web_panel import WebPanel
from companion.session import requested_tool
from companion.preferences import Preferences
from companion.provider_panel import ProviderPanel
from companion.personality_panel import PersonalityPanel
from companion.history_panel import HistoryPanel
from companion.interaction_worker import InteractionWorker
from companion.orchestration import resolve_depth
from companion.chat_view import ChatView, ConversationSidebar, EmojiButton
from companion.conversation_store import ConversationStore
from companion.privacy_profiles import PrivacyProfilesDialog, PROFILE_FLAGS, validate_flags, ProfileError
from companion.device_view import CompanionDevice

STYLE = """
QWidget { background:#101825; color:#e6edf5; font-family:'Segoe UI'; font-size:14px; }
QMainWindow { background:#101825; }
QLabel#brand { color:#75e4c2; font-size:13px; font-weight:700; letter-spacing:2px; }
QLabel#title { font-size:22px; font-weight:700; }
QLabel#muted { color:#9baec4; font-size:12px; }
QTextEdit { background:#152132; border:1px solid #2b3b50; border-radius:12px; padding:14px; selection-background-color:#286956; }
QLineEdit, QComboBox { background:#1b293c; border:1px solid #34455b; border-radius:7px; padding:8px; }
QPushButton { background:#24374c; border:1px solid #3c526a; border-radius:8px; padding:9px 14px; }
QPushButton:hover { background:#304963; }
QPushButton:disabled { color:#68788b; background:#1a2636; border-color:#26374b; }
QPushButton#send { background:#75e4c2; color:#10251f; font-weight:700; }
QPushButton#send:disabled { background:#1a2636; color:#68788b; }
QCheckBox::indicator { width:16px; height:16px; border:1px solid #567184; background:#1b293c; border-radius:3px; }
QCheckBox::indicator:checked { background:#75e4c2; border-color:#75e4c2; }
QPushButton#private { border-color:#dfb26a; color:#ffd99e; }
"""


def format_response_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    if milliseconds < 1000:
        return f"{milliseconds} ms"
    if milliseconds < 60000:
        return f"{milliseconds / 1000:.2f} s"
    total_seconds = milliseconds // 1000
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min {seconds:02d} s"


class MessageInput(QTextEdit):
    submit = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if not event.isAutoRepeat():
                self.submit.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class Worker(QThread):
    result = Signal(int, object)
    failure = Signal(int, str)

    def __init__(self, job: int, kind: str, model: str = "", messages=None):
        super().__init__()
        self.job, self.kind, self.model = job, kind, model
        self.messages = messages or []
        self.cancel_event = threading.Event()
        self.client = OllamaClient()

    def cancel(self):
        self.cancel_event.set()
        self.client.cancel()

    def run(self):
        try:
            if self.kind == "models":
                models = self.client.list_models()
                self.result.emit(self.job, {"models": models, "thinking": {
                    model: self.client.thinking_required(model) for model in models}})
                return
            start, first, parts, metrics = time.perf_counter(), None, [], {}
            for chunk in self.client.stream_chat(self.model, self.messages, self.cancel_event):
                if chunk.text:
                    if first is None:
                        first = time.perf_counter() - start
                    parts.append(chunk.text)
                if chunk.metrics:
                    metrics = chunk.metrics
            answer = final_answer("".join(parts))
            self.result.emit(self.job, {"answer": answer, "first": first,
                                       "elapsed": time.perf_counter() - start, "metrics": metrics})
        except Cancelled:
            pass
        except (ClientError, ValueError) as exc:
            self.failure.emit(self.job, str(exc))
        except Exception:
            self.failure.emit(self.job, "No se pudo completar la operación. Revisá Ollama y probá otra vez.")


class Window(QMainWindow):
    def __init__(self, state_path: Path, *, auto_connect: bool = True):
        super().__init__()
        self.setWindowTitle("Companion IA · Prueba local")
        self.resize(1240, 900)
        self.policy = Policy(state_path)
        self.history: list[dict] = []
        self.last_messages: list[dict] = []
        self._worker = None
        self._job = 0
        self._lease = None
        self._pending_user = ""
        self._pending_route = None
        self._pending_remote_messages = []
        self._pending_depth = "brief"
        self._response_started = None
        self._preferences = Preferences(state_path.parent / "preferences.json")
        self._preference_values = None
        self._current_conversation = None
        self._session_display = []
        self._draft_chat_key = "draft:" + uuid4().hex
        self._pending_chat_saves = {}
        self._failed_chat_views = {}
        self._volatile_conversations = {}
        self._loading_conversation = False
        self._device_mode = False
        self._profiles_applying = False
        self._profile_flags_path = state_path.parent / "active-privacy-profile.json"
        self._privacy_profiles_path = state_path.parent / "privacy-profiles.json"
        self.conversations = ConversationStore(state_path.parent / "memory.sqlite", self.policy)
        self._thinking = {}
        self._last_sources = []
        if self.policy.state is State.ACTIVE:
            self.policy.grant("local.request")
        # register_stopper is intentionally not called while restored PRIVATE.
        if self.policy.state is State.ACTIVE:
            self.policy.register_stopper(self._stop_for_private)
        self._stopper_registered = self.policy.state is State.ACTIVE
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(8)
        brand = QLabel("COMPANION IA")
        brand.setObjectName("brand")
        brand.hide()
        title = QLabel("Un espacio para conversar")
        title.setObjectName("title")
        layout.addWidget(title)
        note = QLabel("Prueba local · Chat temporal · Notas y audio solo con sus controles")
        note.setObjectName("muted")
        self.session_note = note
        layout.addWidget(note)
        self.defaults_note = QLabel("Funciones disponibles: podés habilitarlas o desmarcarlas desde sus controles.")
        self.defaults_note.setWordWrap(True)
        self.defaults_note.setObjectName("muted")
        layout.addWidget(self.defaults_note)
        self.memory_panel = MemoryPanel(self.policy, state_path.parent, self)
        self.memory_panel.changed.connect(self._memory_changed)
        layout.addWidget(self.memory_panel)
        row = QHBoxLayout()
        self.name = QLineEdit("Amiga")
        self.name.setMaxLength(40)
        self.name.setPlaceholderText("Nombre de tu compañera")
        self.name.setAccessibleName("Nombre de tu compañera")
        self.mode = QComboBox()
        self.mode.addItems(["Charla", "Trabajo"])
        self.mode.setAccessibleName("Estilo de conversación")
        self.models = QComboBox()
        self.models.setMinimumWidth(180)
        self.models.setAccessibleName("Modelo local")
        self.connect_button = QPushButton("Detectar Ollama")
        self.connect_button.clicked.connect(self.detect_models)
        row.addWidget(self.name, 1)
        row.addWidget(self.mode)
        row.addWidget(self.models, 1)
        row.addWidget(self.connect_button)
        layout.addLayout(row)
        self.status = QLabel("Preparada para detectar el motor local.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.voice_panel = VoicePanel(self.policy, self)
        self.voice_panel.transcript.connect(self._dictated)
        self.voice_panel.notice.connect(self.status.setText)
        layout.addWidget(self.voice_panel)
        self.transcript = ChatView()
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Conversación de esta sesión")
        self.transcript.setPlaceholderText("Tu conversación aparecerá acá. El guardado sigue el perfil y el modo privado.")
        layout.addWidget(self.transcript, 1)
        self.input = MessageInput()
        self.input.setAcceptRichText(False)
        self.input.setMaximumHeight(105)
        self.input.setAccessibleName("Mensaje")
        self.input.setPlaceholderText("Enter para enviar · Shift+Enter para una nueva línea…")
        layout.addWidget(self.input)
        buttons = QHBoxLayout()
        self.send = QPushButton("Enviar")
        self.send.setObjectName("send")
        self.send.clicked.connect(self.send_message)
        self.input.submit.connect(lambda: self.send_message() if self.send.isEnabled() else None)
        self.emoji = EmojiButton()
        self.emoji.emoji_selected.connect(self._insert_emoji)
        buttons.addWidget(self.emoji)
        self.stop = QPushButton("Detener")
        self.stop.clicked.connect(self.cancel_response)
        self.fresh = QPushButton("Nueva conversación")
        self.fresh.clicked.connect(self.new_conversation)
        self.inspector = QPushButton("Ver contexto")
        self.inspector.clicked.connect(self.show_context)
        self.privacy = QPushButton("Modo privado")
        self.privacy.setObjectName("private")
        self.privacy.clicked.connect(self.toggle_private)
        for button in (self.send, self.stop, self.fresh, self.inspector, self.privacy):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.response_time = QLabel("Respuesta: —")
        self.response_time.setObjectName("muted")
        self.response_time.setToolTip("Tiempo desde Enviar hasta insertar la respuesta completa en el chat. No incluye síntesis ni reproducción de voz.")
        layout.addWidget(self.response_time)
        self.metrics = QLabel("Los tiempos de esta sesión aparecerán después de una respuesta.")
        self.metrics.setObjectName("muted")
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        footer = QHBoxLayout()
        setup = QPushButton("Instalar / abrir Ollama")
        setup.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://ollama.com/download/windows")))
        footer.addWidget(setup)
        functions = QPushButton("Funciones reales")
        functions.clicked.connect(lambda: QMessageBox.information(self, "Funciones de esta versión", capabilities(self.memory_panel.enabled) + "\n\nVoz y dictado requieren habilitar sus controles. El micrófono no escucha por sí solo."))
        footer.addWidget(functions)
        foot = QLabel("Local por defecto · Proveedor visible · Contexto bajo tu control")
        foot.setObjectName("muted")
        footer.addWidget(foot, 1)
        layout.addLayout(footer)
        self.takeCentralWidget()
        self.tabs = QTabWidget()
        self.chat_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.sidebar = ConversationSidebar()
        self.sidebar.setMinimumWidth(210)
        self.sidebar.setMaximumWidth(330)
        self.sidebar.selected.connect(self.open_conversation)
        self.sidebar.new_requested.connect(self.new_conversation)
        self.sidebar.rename_requested.connect(self.rename_conversation)
        self.sidebar.delete_requested.connect(self.delete_conversation)
        self.chat_splitter.addWidget(self.sidebar)
        self.chat_splitter.addWidget(root)
        self.chat_splitter.setStretchFactor(1, 1)
        self.chat_splitter.setSizes([245, 995])
        self.tabs.addTab(self.chat_splitter, "Conversación")
        db_path = state_path.parent / "memory.sqlite"
        self.agenda_panel = AgendaPanel(self.policy, db_path, self)
        self.cinematheque = CinemathequePanel(self.policy, db_path, self)
        self.web_panel = WebPanel(self.policy, self)
        self.environment = EnvironmentPanel(self.policy, self)
        self.provider_panel = ProviderPanel(self.policy, state_path.parent, self)
        self.personality = PersonalityPanel(self.policy, state_path.parent, self)
        self.histories = HistoryPanel(self.policy, db_path, self)
        self.name.setText(self.personality.context().get("name", "Amiga"))
        self.memory_window = QDialog(self, Qt.WindowType.Window)
        self.memory_window.setWindowTitle("Memoteca · Companion IA")
        self.memory_window.resize(1000, 780)
        memory_layout = QVBoxLayout(self.memory_window)
        memory_layout.addWidget(self.cinematheque)
        self.memory_launcher = QWidget()
        launcher_layout = QVBoxLayout(self.memory_launcher)
        description = QLabel("Tu Memoteca se abre en una ventana independiente. Conserva fotos, notas y permisos del mismo programa.")
        description.setWordWrap(True)
        launcher_layout.addWidget(description)
        open_memory = QPushButton("Abrir Memoteca")
        open_memory.clicked.connect(self.open_memoteca)
        launcher_layout.addWidget(open_memory)
        launcher_layout.addStretch()
        self.device = CompanionDevice(self)
        self.device.open_chat.connect(self.open_full_chat)
        self.device.open_memory.connect(self.open_memoteca)
        self.device.record_requested.connect(self.voice_panel.toggle_record)
        self.device.private_requested.connect(self.toggle_private)
        for panel, label in ((self.agenda_panel, "Agenda"), (self.memory_launcher, "Memoteca"),
                             (self.web_panel, "Web"), (self.environment, "Entorno"),
                             (self.provider_panel, "Proveedores"), (self.personality, "Personalidad"),
                             (self.histories, "Historiales")):
            self.tabs.addTab(panel, label)
            if hasattr(panel, "notice"):
                panel.notice.connect(self.status.setText)
        self.cinematheque.notice.connect(self.status.setText)
        self.agenda_panel.alert.connect(self._agenda_alert, Qt.ConnectionType.QueuedConnection)
        self.cinematheque.changed.connect(self._discard_derived_context)
        self.environment.changed.connect(self._discard_derived_context)
        self.provider_panel.changed.connect(self._discard_derived_context)
        self.histories.changed.connect(self._discard_derived_context)
        self.personality.applied.connect(self._personality_applied)
        self.alarm_audio = AudioIO(self.policy, playback_operation="alarm.playback")
        self.setCentralWidget(self.tabs)
        toolbar = self.addToolBar("Controles globales")
        toolbar.setMovable(False)
        self.global_privacy = QPushButton("Modo privado")
        self.global_privacy.clicked.connect(self.toggle_private)
        toolbar.addWidget(self.global_privacy)
        self.profile_button = QPushButton("Perfiles de privacidad")
        self.profile_button.clicked.connect(self.show_privacy_profiles)
        toolbar.addWidget(self.profile_button)
        self.device_button = QPushButton("Modo dispositivo")
        self.device_button.clicked.connect(self.open_device)
        toolbar.addWidget(self.device_button)
        self.global_audio = QPushButton("Detener audio")
        self.global_audio.clicked.connect(self._stop_audio)
        toolbar.addWidget(self.global_audio)
        self.audio_indicator = QLabel()
        toolbar.addWidget(self.audio_indicator)
        self.audio_timer = QTimer(self)
        self.audio_timer.setInterval(250)
        self.audio_timer.timeout.connect(self._audio_status)
        self.audio_timer.start()
        self.saving_badge = QLabel("  Guardado: temporal  ")
        toolbar.addWidget(self.saving_badge)
        self.retry_save_button = QPushButton("Reintentar guardado")
        self.retry_save_button.clicked.connect(self.retry_chat_save)
        self.retry_save_button.hide()
        toolbar.addWidget(self.retry_save_button)
        self.global_status = QLabel("  Agenda y fotos requieren memoria; web y entorno tienen autorización propia.")
        toolbar.addWidget(self.global_status)
        self.models.currentTextChanged.connect(self._model_changed)
        self._refresh_conversations()
        self._refresh()
        if auto_connect and self.policy.state is State.ACTIVE:
            QTimer.singleShot(200, self.detect_models)

    def enable_preferences(self):
        self._preference_values = self._preferences.load()
        controls = {
            "memory": self.memory_panel.toggle, "voice": self.voice_panel.read,
            "microphone": self.voice_panel.mic, "notices": self.agenda_panel.toggle,
            "foreground": self.environment.allow, "environment_context": self.environment.share,
            "web": self.web_panel.allow, "web_context": self.web_panel.use,
        }
        for key, checkbox in controls.items():
            # Only an actual user click changes saved preferences. Private-mode
            # cleanup and one-use web context consumption are transient overrides.
            checkbox.clicked.connect(lambda checked, key=key: self._remember_option(key, checked))
        self.voice_panel.quality.activated.connect(lambda index: self._remember_option("natural_voice", index == 0))
        self._apply_preferences()
        self._restore_active_profile()

    def _remember_option(self, key, checked):
        if self._preference_values is None or self.policy.state is not State.ACTIVE:
            return
        self._preference_values[key] = bool(checked)
        try:
            self._preferences.save(self._preference_values)
        except OSError:
            self.status.setText("No se pudo guardar esta preferencia. Se aplica solo a esta sesión.")

    def _apply_preferences(self):
        if self._preference_values is None or self.policy.state is not State.ACTIVE:
            return
        options = self._preference_values
        self.memory_panel.set_enabled(options["memory"])
        self.voice_panel.quality.setCurrentIndex(0 if options["natural_voice"] else 1)
        self.voice_panel.prefer_natural = options["natural_voice"]
        self.voice_panel.read.setChecked(options["voice"])
        self.voice_panel.set_microphone_enabled(options["microphone"])
        self.agenda_panel.toggle.setChecked(options["notices"] and options["memory"])
        self.environment.set_observation_enabled(options["foreground"])
        self.environment.share.setChecked(options["environment_context"])
        self.web_panel.allow.setChecked(options["web"])
        self.web_panel.use.setChecked(options["web_context"])
        self.defaults_note.setText(
            "Inicio con funciones activadas; podés desmarcarlas. Memoria local sin cifrar; "
            "micrófono solo con Dictar. Entorno observa títulos en RAM; Web envía consultas al pulsar Buscar.")
        self._refresh()

    def _insert_emoji(self, text):
        self.input.insertPlainText(text)
        self.input.setFocus()

    def open_device(self):
        self._device_mode = True
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self.hide()
        self.device.show()
        self.device.raise_()

    def open_full_chat(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.device.hide()
        self._device_mode = False
        QApplication.instance().setQuitOnLastWindowClosed(True)

    def open_memoteca(self):
        if self.policy.state is not State.ACTIVE:
            self.status.setText("Memoteca desconectada en modo privado.")
            return
        self.memory_window.show()
        self.memory_window.raise_()
        self.memory_window.activateWindow()

    def _privacy_flags(self):
        return dict(memory=self.memory_panel.enabled, voice=self.voice_panel.read.isChecked(),
            microphone=self.voice_panel.mic.isChecked(), foreground=self.environment.allow.isChecked(),
            environment_context=self.environment.share.isChecked(), notices=self.agenda_panel.toggle.isChecked(),
            history_context=self.histories.use_context.isChecked(),
            share_history=self.provider_panel.share_history.isChecked(), share_memory=self.provider_panel.share_memory.isChecked(),
            share_imports=self.provider_panel.share_imports.isChecked(), share_environment=self.provider_panel.share_environment.isChecked())

    def show_privacy_profiles(self):
        if self.policy.state is not State.ACTIVE:
            return
        dialog = PrivacyProfilesDialog(self.policy, self._privacy_profiles_path, self._privacy_flags(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_flags is not None:
            self.apply_privacy_profile(dialog.selected_flags)

    def _restore_active_profile(self):
        if self.policy.state is not State.ACTIVE:
            return
        self.policy.grant("profiles.read")
        try:
            lease = self.policy.begin("profiles.read")
            def read():
                if not self._profile_flags_path.exists():
                    return None
                if self._profile_flags_path.stat().st_size > 4096:
                    raise ValueError()
                return validate_flags(json.loads(self._profile_flags_path.read_text(encoding="utf-8")))
            flags = self.policy.publish(lease, read)
            if flags is not None:
                self.apply_privacy_profile(flags, persist=False)
        except (OSError,ValueError,Denied):
            self.apply_privacy_profile(dict.fromkeys(PROFILE_FLAGS, False), persist=False)

    def apply_privacy_profile(self, flags, *, persist=True):
        if self.policy.state is not State.ACTIVE:
            return
        try:
            flags = validate_flags(flags)
        except ProfileError:
            return
        self._cancel_worker()
        self.voice_panel.stop_and_wait()
        self._profiles_applying = True
        try:
            if flags["memory"]:
                self.memory_panel.set_enabled(True)
                self.histories.refresh(True)
            self.voice_panel.read.setChecked(flags["voice"])
            self.voice_panel.set_microphone_enabled(flags["microphone"])
            self.environment.set_observation_enabled(flags["foreground"])
            self.environment.share.setChecked(flags["environment_context"])
            self.agenda_panel.toggle.setChecked(flags["notices"] and flags["memory"])
            self.histories.use_context.setChecked(flags["history_context"])
            if self.memory_panel.enabled:
                self.histories._save_scope()
            for flag, control in (("share_history",self.provider_panel.share_history), ("share_memory",self.provider_panel.share_memory),
                                  ("share_imports",self.provider_panel.share_imports),("share_environment",self.provider_panel.share_environment)):
                control.setChecked(flags[flag])
            if not flags["memory"]:
                self.memory_panel.set_enabled(False)
        finally:
            self._profiles_applying = False
        if persist:
            self.policy.grant("profiles.write")
            try:
                lease = self.policy.begin("profiles.write")
                def save():
                    self._profile_flags_path.parent.mkdir(parents=True,exist_ok=True)
                    temporary=self._profile_flags_path.with_suffix(".tmp")
                    temporary.write_text(json.dumps(flags),encoding="utf-8")
                    temporary.replace(self._profile_flags_path)
                self.policy.publish(lease,save)
                if self._preference_values is not None:
                    for key in ("memory","voice","microphone","foreground","environment_context","notices"):
                        self._preference_values[key]=flags[key]
                    self._preferences.save(self._preference_values)
                self.provider_panel._save_options()
            except (OSError,Denied):
                self.status.setText("Perfil aplicado solo a esta sesión; no se pudo guardar.")
        self.new_conversation()
        self.saving_badge.setText("  Guardado local activo  " if flags["memory"] else "  Chat temporal · sin guardar  ")
        self.status.setText("Perfil aplicado. Se abrió una charla limpia para no arrastrar contexto anterior.")

    def _show_response_time(self):
        if self._response_started is not None:
            self.response_time.setText("Respuesta: " + format_response_time(time.perf_counter() - self._response_started))
            self._response_started = None

    def _audio_status(self):
        recording = self.voice_panel.audio.recording
        playing = self.voice_panel.audio.playing or self.alarm_audio.playing
        self.device.set_status(private=self.policy.state is State.PRIVATE, recording=recording, busy=self._worker is not None, blocked=self.policy.state in (State.ERROR, State.STOPPING), can_record=self.voice_panel.record.isEnabled())
        self.audio_indicator.setText("  Micrófono grabando  " if recording else ("  Audio reproduciéndose  " if playing else "  Micrófono inactivo  "))

    def _stop_audio(self):
        try:
            self.voice_panel.stop_and_wait()
            self.alarm_audio.cancel()
        except (RuntimeError, AudioError):
            self.status.setText("No se pudo confirmar el cierre del audio. Activá privado para bloquear la sesión.")

    def _personality_applied(self, values):
        self.name.setText(values["name"])
        self._discard_derived_context()

    def _worker_stage(self, job, message):
        if job != self._job or self._lease is None:
            return
        try:
            self.policy.publish(self._lease, lambda: self.status.setText(message))
        except Denied:
            pass

    def _worker_snapshot(self, job, messages):
        if job != self._job or self._lease is None:
            return
        try:
            self.policy.publish(self._lease, lambda: setattr(self, "last_messages", [dict(item) for item in messages]))
            self.inspector.setEnabled(True)
        except Denied:
            pass

    def _discard_derived_context(self):
        if self._profiles_applying:
            return
        self._cancel_worker()
        self.voice_panel.stop()
        self.history.clear()
        self.last_messages.clear()
        self._last_sources.clear()
        self.transcript.clear()
        self._current_conversation = None
        self._session_display = []
        self._refresh_conversations()
        self._refresh()

    def _agenda_alert(self, item):
        if self.policy.state is not State.ACTIVE or not self.memory_panel.enabled or not self.agenda_panel.toggle.isChecked():
            return
        self.tabs.setCurrentWidget(self.agenda_panel)
        self.global_status.setText("  Tenés un aviso pendiente en Agenda")
        if item.get("sound"):
            try:
                import numpy as np
                self.policy.grant("alarm.playback")
                rate = 16000
                samples = (0.12 * np.sin(2 * np.pi * 660 * np.arange(rate // 2) / rate)).astype("float32")
                self.alarm_audio.play_samples(samples, rate)
            except (Denied, AudioError):
                self.status.setText("Aviso visible; no se pudo reproducir el sonido.")

    def _dictated(self, text):
        if self.policy.state is State.ACTIVE:
            previous = self.input.toPlainText().strip()
            self.input.setPlainText(previous + "\n" + text if previous else text)

    def _memory_changed(self, enabled):
        if self._profiles_applying:
            return
        self._current_conversation = None
        self._session_display = []
        self._pending_chat_saves.clear()
        self._failed_chat_views.clear()
        self.retry_save_button.hide()
        self._volatile_conversations.clear()
        self._cancel_worker()
        self.voice_panel.stop()
        self.alarm_audio.cancel()
        self.history.clear()
        self.last_messages.clear()
        self._last_sources.clear()
        self._pending_user = ""
        self.transcript.clear()
        self.input.clear()
        self.session_note.setText("Guardado automático de conversaciones activo · Contexto según perfil" if enabled
                                  else "Memoria apagada · Chat temporal · Micrófono solo al pulsar Dictar")
        self.saving_badge.setText("  Guardado local activo  " if enabled else "  Chat temporal · sin guardar  ")
        self.status.setText("Guardado automático de mensajes habilitado." if enabled else "Memoria desactivada. Las notas existentes se conservan, pero no se recuperan.")
        self._refresh_conversations()
        self._refresh()

    def _chat_allowed(self):
        return self.policy.state in (State.ACTIVE, State.PRIVATE)

    def _append(self, text, role=None, label="", provider=""):
        if role is None:
            first, separator, body = text.partition("\n")
            if separator and first == "Vos":
                role, label, text = "user", "Vos", body.rstrip("\n")
            elif separator and first in (self.name.text().strip() or "Amiga", "Amiga"):
                role, label, text = "assistant", first, body.rstrip("\n")
            elif separator and first.startswith("Programa"):
                role, label, text = "assistant", first, body.rstrip("\n")
            else:
                role = "notice"
        self.transcript.append_message(role, text, label=label, meta=provider)
        if self._loading_conversation:
            return
        record = dict(id=str(uuid4()), role=role, content=text, label=label, provider=provider)
        self._session_display.append(record)
        if self.policy.state is State.PRIVATE:
            return
        if self.policy.state is not State.ACTIVE:
            return
        if self.memory_panel.enabled:
            key = self._current_conversation or self._draft_chat_key
            self._pending_chat_saves.setdefault(key, []).append(record)
            self.retry_chat_save()
        else:
            if self._current_conversation is None:
                self._current_conversation = "session:" + uuid4().hex
            title = next((" ".join(item["content"].split())[:70] for item in self._session_display if item["role"] == "user"), "Chat temporal")
            title = self._volatile_conversations.get(self._current_conversation, {}).get("title", title)
            self._volatile_conversations[self._current_conversation] = dict(id=self._current_conversation, title=title, updated_at="", messages=list(self._session_display))
        self._refresh_conversations()

    def retry_chat_save(self):
        if self.policy.state is not State.ACTIVE or not self.memory_panel.enabled:
            return
        key = self._current_conversation or self._draft_chat_key
        queue = self._pending_chat_saves.get(key, [])
        if not queue:
            return
        try:
            self.policy.grant("conversation.read")
            self.policy.grant("conversation.write")
            if self._current_conversation is None:
                title = next((" ".join(item["content"].split())[:70] for item in self._session_display if item["role"] == "user"), "Nueva conversación")
                title = self._failed_chat_views.get(key, {}).get("title", title)
                identifier = self.conversations.create(title)["id"]
                self._pending_chat_saves[identifier] = self._pending_chat_saves.pop(key)
                self._failed_chat_views.pop(key, None)
                self._current_conversation = identifier
                key = identifier
                queue = self._pending_chat_saves[key]
            while queue:
                item = queue[0]
                self.conversations.append(key, item["role"], item["content"], item.get("label",""), item.get("provider",""), message_id=item["id"])
                queue.pop(0)
            self._pending_chat_saves.pop(key, None)
            self._failed_chat_views.pop(key, None)
        except (MemoryStoreError, ValueError, Denied, OSError):
            title = next((" ".join(item["content"].split())[:70] for item in self._session_display if item["role"] == "user"), "Nueva conversación")
            title = self._failed_chat_views.get(key, {}).get("title", title)
            self._failed_chat_views[key] = dict(id=key, title=title, updated_at="", messages=list(self._session_display))
        remaining = sum(len(items) for items in self._pending_chat_saves.values())
        self.saving_badge.setText(f"  {remaining} mensaje(s) sin guardar  " if remaining else "  Guardado local activo  ")
        self.retry_save_button.setVisible(bool(remaining))
        self._refresh_conversations()

    def _refresh_conversations(self):
        if self.policy.state is State.PRIVATE:
            self.sidebar.set_items([])
            self.retry_save_button.hide()
            self.saving_badge.setText("  Privado · sin guardar  ")
            return
        if self.policy.state is not State.ACTIVE:
            self.sidebar.set_items([])
            return
        if self.memory_panel.enabled:
            try:
                self.policy.grant("conversation.read")
                self.policy.grant("conversation.write")
                rows = self.conversations.list_conversations(500)
                for item in rows:
                    if self._pending_chat_saves.get(item["id"]):
                        item["title"] += " · sin guardar"
                rows.extend(dict(id=key,title=value["title"]+" · sin guardar",updated_at="") for key,value in self._failed_chat_views.items() if key.startswith("draft:"))
                self.sidebar.set_items(rows)
            except (MemoryStoreError, Denied):
                self.sidebar.set_items([])
                self.saving_badge.setText("  No se pudo leer el archivo de chats  ")
        else:
            self.sidebar.set_items(list(self._volatile_conversations.values()))
        self.sidebar.set_current(self._current_conversation or "")

    def open_conversation(self, identifier):
        if self.policy.state is not State.ACTIVE:
            return
        self._cancel_worker()
        self.voice_panel.stop()
        try:
            if self.memory_panel.enabled and identifier in self._failed_chat_views:
                messages = self._failed_chat_views[identifier]["messages"]
            else:
                messages = self.conversations.messages(identifier) if self.memory_panel.enabled else self._volatile_conversations[identifier]["messages"]
        except (MemoryStoreError, Denied, KeyError):
            self.status.setText("No se pudo abrir esa conversación.")
            return
        self._current_conversation = None if identifier.startswith("draft:") else identifier
        if identifier.startswith("draft:"):
            self._draft_chat_key = identifier
        self._session_display = [dict(message) for message in messages]
        self._loading_conversation = True
        self.transcript.clear()
        try:
            for message in messages:
                self.transcript.append_message(message["role"], message["content"], label=message.get("label",""), meta=message.get("provider",""))
        finally:
            self._loading_conversation = False
        self.history = [{"role":message["role"],"content":message["content"]} for message in messages if message["role"] in ("user","assistant")][-12:]
        self.last_messages.clear()
        self.input.clear()
        self._refresh()

    def rename_conversation(self, identifier):
        if self.policy.state is not State.ACTIVE:
            return
        title, accepted = QInputDialog.getText(self, "Renombrar conversación", "Título:")
        if not accepted or not title.strip():
            return
        try:
            if self.memory_panel.enabled:
                if not identifier.startswith("draft:"):
                    self.conversations.rename(identifier, title.strip()[:120])
                if identifier in self._failed_chat_views:
                    self._failed_chat_views[identifier]["title"] = title.strip()[:120]
            else:
                self._volatile_conversations[identifier]["title"] = title.strip()[:120]
            self._refresh_conversations()
        except (MemoryStoreError, Denied, KeyError, ValueError):
            self.status.setText("No se pudo renombrar la conversación.")

    def delete_conversation(self, identifier):
        if self.policy.state is not State.ACTIVE:
            return
        if QMessageBox.question(self, "Borrar conversación", "¿Borrar esta conversación y sus mensajes guardados?", QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._cancel_worker()
        self.voice_panel.stop()
        try:
            if self.memory_panel.enabled:
                if not identifier.startswith("draft:"):
                    self.conversations.delete(identifier)
                self._pending_chat_saves.pop(identifier, None)
                self._failed_chat_views.pop(identifier, None)
            else:
                self._volatile_conversations.pop(identifier, None)
            if identifier == self._current_conversation or (self._current_conversation is None and identifier == self._draft_chat_key):
                self.new_conversation()
            self._refresh_conversations()
        except (MemoryStoreError, Denied):
            self.status.setText("No se pudo borrar la conversación.")

    def _refresh(self):
        active = self.policy.state is State.ACTIVE
        busy = self._worker is not None
        chatting = self._chat_allowed()
        for widget in (self.name, self.mode, self.models, self.connect_button, self.fresh):
            widget.setEnabled(active and not busy)
        self.memory_panel.refresh(busy)
        self.agenda_panel.refresh(self.memory_panel.enabled, busy)
        self.cinematheque.refresh(self.memory_panel.enabled, busy)
        self.web_panel.refresh(active, busy)
        self.environment.refresh(active)
        self.provider_panel.refresh(active, busy)
        self.personality.refresh(active, busy)
        self.histories.refresh(self.memory_panel.enabled, busy)
        self.global_privacy.setText("Reactivar" if self.policy.state is State.PRIVATE else "Modo privado")
        self.global_privacy.setEnabled(self.policy.state in (State.ACTIVE, State.PRIVATE))
        self.global_status.setText("  Modo privado activo" if not active else "  Herramientas disponibles en las pestañas")
        self.voice_panel.refresh()
        self.models.setEnabled(chatting and not busy)
        self.connect_button.setEnabled(chatting and not busy)
        self.fresh.setEnabled(chatting and not busy)
        self.emoji.setEnabled(chatting and not busy)
        self.profile_button.setEnabled(active and not busy)
        self.sidebar.setEnabled(active)
        self.input.setEnabled(chatting and not busy)
        self.send.setEnabled(chatting and not busy and (self.models.count() > 0 or self.provider_panel.account.currentData() is not None))
        self.stop.setEnabled(chatting and busy)
        self.inspector.setEnabled(chatting and bool(self.last_messages))
        self.privacy.setText("Reactivar" if self.policy.state is State.PRIVATE else "Modo privado")
        self.privacy.setEnabled(self.policy.state in (State.ACTIVE, State.PRIVATE))
        if self.policy.state is State.PRIVATE:
            self.status.setText("Modo privado: chat local temporal. Memoria, voz, sensores, contexto adicional y APIs externas desconectados.")
        elif self.policy.state is State.ERROR:
            self.status.setText("No se pudo confirmar el estado de privacidad. El programa permanece bloqueado.")

    def _start(self, kind, messages=None):
        if self._worker is not None:
            return
        try:
            lease = self.policy.begin("private.chat" if self.policy.state is State.PRIVATE else "local.request")
        except Denied:
            return
        self._job += 1
        if kind == "chat":
            route = None if self.policy.state is State.PRIVATE else self._pending_route
            remote_lease = None
            if route and route.get("allowed") and not route.get("error"):
                self.policy.grant("remote.request")
                remote_lease = self.policy.begin("remote.request")
            worker = InteractionWorker(self._job, self.models.currentText(), messages,
                self._pending_remote_messages, route, self._pending_depth, self.mode.currentText(),
                self.policy, lease, remote_lease)
            worker.stage.connect(self._worker_stage)
            worker.snapshot.connect(self._worker_snapshot)
        else:
            worker = Worker(self._job, kind, self.models.currentText(), messages)
        self._lease = lease
        self._worker = worker
        worker.result.connect(self._result)
        worker.failure.connect(self._failure)
        worker.finished.connect(lambda: self._finished(worker))
        self.policy.publish(lease, worker.start)
        self._refresh()

    def detect_models(self):
        if not self._chat_allowed() or self._worker is not None:
            return
        self.status.setText("Buscando modelos locales en Ollama…")
        self._start("models")

    def _model_changed(self):
        model = self.models.currentText()
        if model:
            suffix = "Este modelo requiere razonamiento; la respuesta puede tardar más." if self._thinking.get(model) else "Perfil de respuesta sin razonamiento solicitado."
            self.status.setText("Motor local conectado. " + suffix)

    def send_message(self):
        if self._worker is not None or not self._chat_allowed():
            return
        text = self.input.toPlainText().strip()
        if not text or len(text) > 4000:
            self.status.setText("Escribí entre 1 y 4000 caracteres.")
            return
        self._response_started = time.perf_counter()
        self.response_time.setText("Respuesta: esperando…")
        if self.policy.state is State.PRIVATE:
            from companion.session import private_messages
            messages = private_messages(self.history, text)
            self._pending_route = None
            self._pending_remote_messages = []
            self._pending_user = text
            self.last_messages = messages
            self.input.clear()
            self._append(text, role="user", label="Vos")
            self._start("chat", messages)
            return
        tool = requested_tool(text)
        if tool == "agenda":
            self.tabs.setCurrentWidget(self.agenda_panel)
            self.agenda_panel.draft_text = text
        elif tool == "web":
            self.tabs.setCurrentWidget(self.web_panel)
            self.web_panel.request_search(text)
        elif tool == "cinema":
            self.open_memoteca()
        elif tool == "environment":
            self.tabs.setCurrentWidget(self.environment)
        reply = direct_reply(text, memory_enabled=self.memory_panel.enabled)
        if reply is not None:
            self.input.clear()
            self._append("Vos\n" + text + "\n")
            self._append("Programa · Funciones verificadas\n" + reply + "\n")
            self.history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
            self.history = self.history[-12:]
            self.last_messages.clear()
            self._show_response_time()
            self.voice_panel.speak(reply)
            self.status.setText("Información del programa. No se creó ningún aviso ni se realizó una búsqueda web.")
            self._refresh()
            return
        try:
            notes = self.memory_panel.retrieve(text)
            photos = self.cinematheque.retrieve(text)
            imports = self.histories.retrieve(text)
            def distribute(groups):
                return [group[index] for index in range(3) for group in groups if index < len(group)]
            self._last_sources = distribute([notes, photos, imports])
            temporary_context = self.web_panel.context() + self.environment.context()
            self._pending_depth = resolve_depth(text, self.provider_panel.depth.currentText())
            personality = self.personality.context()
            messages = build_messages(self.history, text, self.name.text(), self.mode.currentText(),
                memories=self._last_sources, memory_enabled=self.memory_panel.enabled,
                extra_context=temporary_context, personality=personality, depth=self._pending_depth)
            self._pending_route = self.provider_panel.request()
            outbound_memories = distribute([
                notes + photos if self.provider_panel.share_memory.isChecked() else [],
                imports if self.provider_panel.share_imports.isChecked() else []])
            self._pending_remote_messages = build_messages(
                self.history if self.provider_panel.share_history.isChecked() else [],
                text, self.name.text(), self.mode.currentText(),
                memories=outbound_memories, memory_enabled=self.memory_panel.enabled,
                extra_context=temporary_context if self.provider_panel.share_environment.isChecked() else [],
                personality=personality, depth=self._pending_depth)
        except (ValueError, MemoryStoreError, Denied) as exc:
            self._response_started = None
            self.response_time.setText("Respuesta: no enviada")
            self.status.setText(str(exc))
            return
        self.last_messages = self._pending_remote_messages if self._pending_route and self._pending_route.get("allowed") else messages
        self._pending_user = text
        self.input.clear()
        self._append("Vos\n" + text + "\n")
        self.status.setText("Preparando respuesta final… Podés detenerla o activar modo privado.")
        self._start("chat", messages)

    def _result(self, job, result):
        if job != self._job or self._lease is None:
            return
        try:
            self.policy.publish(self._lease, lambda: self._apply_result(result))
            if "answer" in result:
                self.voice_panel.speak(result["answer"])
        except Denied:
            return

    def _apply_result(self, result):
        if "models" in result:
            previous = self.models.currentText()
            self.models.blockSignals(True)
            self.models.clear()
            self.models.addItems(result["models"])
            if previous in result["models"]:
                self.models.setCurrentText(previous)
            elif "qwen2.5:7b" in result["models"]:
                self.models.setCurrentText("qwen2.5:7b")
            elif "qwen3:4b" in result["models"]:
                self.models.setCurrentText("qwen3:4b")
            self.models.blockSignals(False)
            self._thinking = result["thinking"]
            if not result["models"]:
                self.status.setText("Ollama respondió, pero no hay modelos locales verificables. Instalá uno y volvé a detectar.")
            else:
                self._model_changed()
            return
        answer = result["answer"]
        if result.get("request_messages"):
            self.last_messages = result["request_messages"]
        if result.get("provider"):
            self._append("Motor · " + result["provider"] + (" · " + result["account"] if result.get("account") else ""))
        if result.get("fallback_reason"):
            self._append("Aviso del programa · " + result["fallback_reason"] + " Se usó Ollama local; puede tener menos capacidad.")
        if result.get("truncated"):
            self._append("Aviso del programa · El proveedor recortó la respuesta por el límite de salida.")
        self.provider_panel.show_usage(result)
        self.history.extend([{"role": "user", "content": self._pending_user},
                             {"role": "assistant", "content": answer}])
        self.history = self.history[-12:]
        self._append((self.name.text().strip() or "Amiga") + "\n" + answer + "\n")
        self._show_response_time()
        m = result["metrics"]
        rate = m.get("eval_count", 0) / (m["eval_duration"] / 1e9) if m.get("eval_duration") else None
        first = result.get("first")
        summary = f"Total: {result['elapsed']:.2f} s"
        if first is not None:
            summary += f" · Primer texto del motor: {first:.2f} s"
        if rate is not None:
            summary += f" · Generación: {rate:.1f} tokens/s"
        self.metrics.setText(summary + " · Medición puntual, no benchmark")
        suffix = f" · Notas recuperadas: {len(self._last_sources)}" if self._last_sources else ""
        route_note = " Respaldo local usado por un problema con la API." if result.get("fallback_reason") else ""
        self.status.setText("Respuesta lista · " + result.get("provider", "Ollama local") + (". Guardado local activo." if self.memory_panel.enabled and self.policy.state is State.ACTIVE else ". Chat temporal.") + suffix + route_note)

    def _failure(self, job, message):
        if job != self._job or self._lease is None:
            return
        try:
            def show_failure():
                self.status.setText(message)
                if self._response_started is not None:
                    self._response_started = None
                    self.response_time.setText("Respuesta: falló")
            self.policy.publish(self._lease, show_failure)
        except Denied:
            pass

    def _finished(self, worker):
        if self._worker is worker:
            self._worker = None
            if self._response_started is not None:
                self._response_started = None
                self.response_time.setText("Respuesta: descartada")
                self.status.setText("La solicitud terminó sin respuesta publicable, posiblemente por un cambio de permisos. Podés reenviarla.")
        worker.deleteLater()
        self._refresh()

    def _cancel_worker(self):
        if self._response_started is not None:
            self._response_started = None
            self.response_time.setText("Respuesta: cancelada")
        self._job += 1
        self._lease = None
        if self._worker is not None:
            self._worker.cancel()
        if self._pending_route is not None:
            self._pending_route["key"] = ""
        self._pending_route = None
        self._pending_remote_messages.clear()
        self.policy.revoke("remote.request")

    def _stop_for_private(self):
        self._cancel_worker()
        self.voice_panel.deactivate()
        self.web_panel.stop()
        self.provider_panel.stop()
        self.personality.stop()
        self.histories.stop()
        self.environment.stop()
        self.agenda_panel.stop()
        self.cinematheque.stop()
        for dialog in self.memory_window.findChildren(QDialog):
            dialog.reject()
        self.memory_window.hide()
        self.alarm_audio.cancel()
        self.policy.revoke("conversation.read")
        self.policy.revoke("conversation.write")
        self.policy.revoke("alarm.playback")
        for operation in ("microphone.capture", "microphone.devices", "speech.transcribe", "speech.synthesize", "audio.playback"):
            self.policy.revoke(operation)
        if self._worker is not None and not self._worker.wait(5000):
            raise RuntimeError("No se pudo confirmar la detención del trabajo local.")

    def cancel_response(self):
        self._cancel_worker()
        self.voice_panel.stop()
        self.status.setText("Solicitud detenida. Se descartan los resultados pendientes; el motor puede demorar en liberar recursos.")
        self._refresh()

    def new_conversation(self):
        if not self._chat_allowed():
            return
        self._cancel_worker()
        self._current_conversation = None
        self._session_display = []
        self._draft_chat_key = "draft:" + uuid4().hex
        self.voice_panel.stop()
        self.alarm_audio.cancel()
        self.history.clear()
        self.last_messages.clear()
        self._last_sources.clear()
        self._pending_user = ""
        self._pending_remote_messages.clear()
        self._pending_route = None
        self.transcript.clear()
        self.input.clear()
        self.metrics.setText("Nueva sesión: todavía sin mediciones.")
        self.response_time.setText("Respuesta: —")
        self._refresh_conversations()
        self._refresh()

    def toggle_private(self):
        if self.policy.state is State.ACTIVE:
            self.memory_panel.deactivate()
            try:
                self.policy.enter_private()
            except (OSError, RuntimeError):
                pass
            self.history.clear()
            self.last_messages.clear()
            self._pending_user = ""
            self.transcript.clear()
            self.input.clear()
            self._current_conversation = None
            self._session_display = []
            self._volatile_conversations.clear()
            self._pending_chat_saves.clear()
            self._failed_chat_views.clear()
            self._refresh_conversations()
            self._thinking.clear()
            self.name.setText("Amiga")
            self.metrics.setText("Contenido de sesión borrado al activar privado.")
            self.response_time.setText("Respuesta: —")
        elif self.policy.state is State.PRIVATE:
            self._cancel_worker()
            self.history.clear()
            self.last_messages.clear()
            self._session_display = []
            self._current_conversation = None
            self.transcript.clear()
            self.input.clear()
            try:
                self.policy.resume(local_action=True)
                self.policy.grant("local.request")
                if not self._stopper_registered:
                    self.policy.register_stopper(self._stop_for_private)
                    self._stopper_registered = True
                self.provider_panel.reload()
                self.personality.reload()
                self.name.setText(self.personality.context().get("name", "Amiga"))
                self._apply_preferences()
                self._restore_active_profile()
                self._refresh_conversations()
                self.status.setText("Reactivada. Pulsá Detectar Ollama para continuar.")
            except (OSError, Denied):
                self.status.setText("No se pudo reactivar. Se mantiene el bloqueo.")
        self._refresh()

    def show_context(self):
        if not self._chat_allowed():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Contexto preparado · Solo en esta sesión")
        dialog.resize(740, 560)
        layout = QVBoxLayout(dialog)
        label = QLabel("Mensajes preparados para el último envío. No es una copia exacta de la solicitud HTTP.")
        label.setWordWrap(True)
        layout.addWidget(label)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(json.dumps(self.last_messages, ensure_ascii=False, indent=2))
        layout.addWidget(view)
        dialog.exec()

    def closeEvent(self, event):
        if self.policy.state is State.ACTIVE and any(self._pending_chat_saves.values()):
            answer = QMessageBox.question(self, "Mensajes sin guardar", "Quedan mensajes solo en RAM. Podés cancelar y reintentar el guardado. ¿Cerrar y descartarlos?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._cancel_worker()
        try:
            self.voice_panel.stop_and_wait()
            self.web_panel.stop()
            self.provider_panel.stop()
            self.personality.stop()
            self.histories.stop()
            self.environment.stop()
            self.agenda_panel.stop()
            self.cinematheque.stop()
            self.alarm_audio.cancel()
        except (RuntimeError, AudioError):
            self.status.setText("Esperando cierre del audio. Probá cerrar de nuevo.")
            event.ignore()
            return
        if self._worker is not None and not self._worker.wait(5000):
            self.status.setText("Esperando cierre del motor local. Volvé a cerrar en unos segundos.")
            event.ignore()
            return
        self.history.clear()
        self.last_messages.clear()
        self.device.hide()
        self.memory_window.hide()
        if self._device_mode:
            QApplication.instance().setQuitOnLastWindowClosed(True)
            QTimer.singleShot(0, QApplication.instance().quit)
        event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--memory-self-test", action="store_true")
    parser.add_argument("--tools-self-test", action="store_true")
    parser.add_argument("--providers-self-test", action="store_true")
    parser.add_argument("--modern-chat-self-test", action="store_true")
    parser.add_argument("--engine-self-test", action="store_true")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args(argv)
    if (args.memory_self_test or args.engine_self_test or args.tools_self_test or args.providers_self_test or args.modern_chat_self_test) and not (args.smoke_test and args.state_dir):
        parser.error("Self-tests require --smoke-test and an explicit temporary --state-dir")
    app = QApplication([sys.argv[0]])
    app.setApplicationName("CompanionIA")
    app.setOrganizationName("CompanionIA")
    if sys.platform == "win32":
        system_font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
        if system_font.is_file():
            QFontDatabase.addApplicationFont(str(system_font))
        app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    data_dir = args.state_dir or Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / "app.lock"))
    if not lock.tryLock(0):
        QMessageBox.information(None, "Companion IA", "Companion ya está abierto en esta cuenta.")
        return 1
    window = Window(data_dir / "privacy.json", auto_connect=not args.smoke_test)
    if not args.smoke_test:
        window.enable_preferences()
    window.show()
    if args.smoke_test:
        def capture():
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.screenshot))
            window.close()
            app.quit()
        if args.memory_self_test:
            panel = window.memory_panel
            panel.set_enabled(True)
            saved = panel.store.save_note("Synthetic packaging check", "synthetic apricot")
            assert panel.store.search("apricot")[0]["id"] == saved["id"]
            assert panel.store.delete_note(saved["id"])
            assert panel.store.search("apricot") == []
            panel.set_enabled(False)
        if args.tools_self_test:
            from datetime import datetime, timedelta, timezone
            from PIL import Image
            panel = window.memory_panel
            panel.set_enabled(True)
            item = window.agenda_panel.store.add("Synthetic alarm", "packaging", datetime.now(timezone.utc) + timedelta(seconds=60))
            assert window.agenda_panel.store.cancel(item["id"])
            fixture = data_dir / "synthetic.png"
            Image.new("RGB", (32, 32), "teal").save(fixture)
            moment = window.cinematheque.store.import_photo(fixture, "Synthetic apricot", "packaging")
            assert window.cinematheque.store.search("apricot")
            window.cinematheque.reload()
            assert window.cinematheque.store.delete_moment(moment["id"])
            fixture.unlink()
            panel.set_enabled(False)
        if args.providers_self_test:
            window.memory_panel.set_enabled(True)
            result = window.histories.store.import_conversations("Synthetic", "Packaging", [
                dict(id="packaging", title="Synthetic apricot history",
                     messages=[dict(role="user", content="synthetic apricot"),
                               dict(role="assistant", content="Unverified synthetic answer")])])
            assert result["imported"] == 1
            assert window.histories.retrieve("apricot")
            window.histories.store.delete_account("Synthetic", "Packaging")
            record = window.provider_panel.store.add("DeepSeek", "Synthetic packaging", "deepseek-flash")
            assert window.provider_panel.store.key(record["id"]) == ""
            window.provider_panel.store.delete(record["id"])
            assert window.provider_panel.account.currentData() is None
            window.memory_panel.set_enabled(False)
        if args.modern_chat_self_test:
            window.memory_panel.set_enabled(True)
            window._append("Synthetic saved message 😊", role="user", label="Vos")
            window._append("Synthetic saved reply ✨", role="assistant", label="Amiga")
            identifier=window._current_conversation
            assert len(window.conversations.messages(identifier)) == 2
            window.new_conversation()
            window.open_conversation(identifier)
            assert "Synthetic saved reply" in window.transcript.toPlainText()
            window.toggle_private()
            assert window.input.isEnabled()
            window._append("Synthetic private message", role="user", label="Vos")
            assert window.sidebar.listing.count() == 0
            window.toggle_private()
            window.memory_panel.set_enabled(True)
            assert len(window.conversations.messages(identifier)) == 2
            window.conversations.delete(identifier)
        if args.engine_self_test:
            if not window.voice_panel.ready:
                return 2
            outcome = {"ok": False}
            window.policy.grant("speech.synthesize")
            window.voice_panel._launch("tts", "Hola. Prueba local de voz.")
            job = window.voice_panel._worker
            job.result.connect(lambda _, data: outcome.update(ok=bool(data.get("pcm"))))
            def finished_engine_check():
                capture()
                app.exit(0 if outcome["ok"] else 3)
            job.finished.connect(finished_engine_check)
            QTimer.singleShot(60000, lambda: (window.voice_panel.stop(), app.exit(4)))
        else:
            QTimer.singleShot(350, capture)
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
