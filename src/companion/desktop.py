"""First Windows desktop prototype. Conversation exists in RAM only."""
from __future__ import annotations
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths, QThread, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QTabWidget,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget,
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

STYLE = """
QWidget { background:#101825; color:#e6edf5; font-family:'Segoe UI'; font-size:14px; }
QMainWindow { background:#101825; }
QLabel#brand { color:#75e4c2; font-size:13px; font-weight:700; letter-spacing:2px; }
QLabel#title { font-size:28px; font-weight:700; }
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
        self.resize(1000, 780)
        self.policy = Policy(state_path)
        self.history: list[dict] = []
        self.last_messages: list[dict] = []
        self._worker = None
        self._job = 0
        self._lease = None
        self._pending_user = ""
        self._response_started = None
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
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(13)
        brand = QLabel("COMPANION IA")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        title = QLabel("Un espacio para conversar")
        title.setObjectName("title")
        layout.addWidget(title)
        note = QLabel("Prueba local · Chat temporal · Notas y audio solo con sus controles")
        note.setObjectName("muted")
        self.session_note = note
        layout.addWidget(note)
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
        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Conversación de esta sesión")
        self.transcript.setPlaceholderText("Tu conversación aparecerá acá. No se guarda en el disco.")
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
        foot = QLabel("Motor separado · Sin APIs pagas · Modelo elegido por vos")
        foot.setObjectName("muted")
        footer.addWidget(foot, 1)
        layout.addLayout(footer)
        self.takeCentralWidget()
        self.tabs = QTabWidget()
        self.tabs.addTab(root, "Conversación")
        db_path = state_path.parent / "memory.sqlite"
        self.agenda_panel = AgendaPanel(self.policy, db_path, self)
        self.cinematheque = CinemathequePanel(self.policy, db_path, self)
        self.web_panel = WebPanel(self.policy, self)
        self.environment = EnvironmentPanel(self.policy, self)
        for panel, label in ((self.agenda_panel, "Agenda"), (self.cinematheque, "Cinemateca"),
                             (self.web_panel, "Web"), (self.environment, "Entorno")):
            self.tabs.addTab(panel, label)
            panel.notice.connect(self.status.setText)
        self.agenda_panel.alert.connect(self._agenda_alert, Qt.ConnectionType.QueuedConnection)
        self.cinematheque.changed.connect(self._discard_derived_context)
        self.environment.changed.connect(self._discard_derived_context)
        self.alarm_audio = AudioIO(self.policy, playback_operation="alarm.playback")
        self.setCentralWidget(self.tabs)
        toolbar = self.addToolBar("Controles globales")
        toolbar.setMovable(False)
        self.global_privacy = QPushButton("Modo privado")
        self.global_privacy.clicked.connect(self.toggle_private)
        toolbar.addWidget(self.global_privacy)
        self.global_audio = QPushButton("Detener audio")
        self.global_audio.clicked.connect(self._stop_audio)
        toolbar.addWidget(self.global_audio)
        self.audio_indicator = QLabel()
        toolbar.addWidget(self.audio_indicator)
        self.audio_timer = QTimer(self)
        self.audio_timer.setInterval(250)
        self.audio_timer.timeout.connect(self._audio_status)
        self.audio_timer.start()
        self.global_status = QLabel("  Agenda y fotos requieren memoria; web y entorno tienen autorización propia.")
        toolbar.addWidget(self.global_status)
        self.models.currentTextChanged.connect(self._model_changed)
        self._refresh()
        if auto_connect and self.policy.state is State.ACTIVE:
            QTimer.singleShot(200, self.detect_models)

    def _show_response_time(self):
        if self._response_started is not None:
            self.response_time.setText("Respuesta: " + format_response_time(time.perf_counter() - self._response_started))
            self._response_started = None

    def _audio_status(self):
        recording = self.voice_panel.audio.recording
        playing = self.voice_panel.audio.playing or self.alarm_audio.playing
        self.audio_indicator.setText("  Micrófono grabando  " if recording else ("  Audio reproduciéndose  " if playing else "  Micrófono inactivo  "))

    def _stop_audio(self):
        try:
            self.voice_panel.stop_and_wait()
            self.alarm_audio.cancel()
        except (RuntimeError, AudioError):
            self.status.setText("No se pudo confirmar el cierre del audio. Activá privado para bloquear la sesión.")

    def _discard_derived_context(self):
        self._cancel_worker()
        self.voice_panel.stop()
        self.history.clear()
        self.last_messages.clear()
        self._last_sources.clear()
        self.transcript.clear()
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
        self._cancel_worker()
        self.voice_panel.stop()
        self.alarm_audio.cancel()
        self.history.clear()
        self.last_messages.clear()
        self._last_sources.clear()
        self._pending_user = ""
        self.transcript.clear()
        self.input.clear()
        self.session_note.setText("Memoria local habilitada · Chat temporal · Entorno con permiso separado" if enabled
                                  else "Memoria apagada · Chat temporal · Micrófono solo al pulsar Dictar")
        self.status.setText("Memoria de notas habilitada. Usá Guardar nota." if enabled else "Memoria desactivada. Las notas existentes se conservan, pero no se recuperan.")
        self._refresh()

    def _append(self, text):
        cursor = self.transcript.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text + "\n")
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()

    def _refresh(self):
        active = self.policy.state is State.ACTIVE
        busy = self._worker is not None
        for widget in (self.name, self.mode, self.models, self.connect_button, self.fresh):
            widget.setEnabled(active and not busy)
        self.memory_panel.refresh(busy)
        self.agenda_panel.refresh(self.memory_panel.enabled, busy)
        self.cinematheque.refresh(self.memory_panel.enabled, busy)
        self.web_panel.refresh(active, busy)
        self.environment.refresh(active)
        self.global_privacy.setText("Reactivar" if self.policy.state is State.PRIVATE else "Modo privado")
        self.global_privacy.setEnabled(self.policy.state in (State.ACTIVE, State.PRIVATE))
        self.global_status.setText("  Modo privado activo" if not active else "  Herramientas disponibles en las pestañas")
        self.voice_panel.refresh()
        self.input.setEnabled(active and not busy)
        self.send.setEnabled(active and not busy and self.models.count() > 0)
        self.stop.setEnabled(active and busy)
        self.inspector.setEnabled(active and not busy and bool(self.last_messages))
        self.privacy.setText("Reactivar" if self.policy.state is State.PRIVATE else "Modo privado")
        self.privacy.setEnabled(self.policy.state in (State.ACTIVE, State.PRIVATE))
        if self.policy.state is State.PRIVATE:
            self.status.setText("Modo privado: procesamiento detenido. Reactivación solo desde este botón.")
        elif self.policy.state is State.ERROR:
            self.status.setText("No se pudo confirmar el estado de privacidad. El programa permanece bloqueado.")

    def _start(self, kind, messages=None):
        if self._worker is not None:
            return
        try:
            lease = self.policy.begin("local.request")
        except Denied:
            return
        self._job += 1
        worker = Worker(self._job, kind, self.models.currentText(), messages)
        self._lease = lease
        self._worker = worker
        worker.result.connect(self._result)
        worker.failure.connect(self._failure)
        worker.finished.connect(lambda: self._finished(worker))
        self.policy.publish(lease, worker.start)
        self._refresh()

    def detect_models(self):
        if self.policy.state is not State.ACTIVE or self._worker is not None:
            return
        self.status.setText("Buscando modelos locales en Ollama…")
        self._start("models")

    def _model_changed(self):
        model = self.models.currentText()
        if model:
            suffix = "Este modelo requiere razonamiento; la respuesta puede tardar más." if self._thinking.get(model) else "Perfil de respuesta sin razonamiento solicitado."
            self.status.setText("Motor local conectado. " + suffix)

    def send_message(self):
        if self._worker is not None or self.policy.state is not State.ACTIVE:
            return
        text = self.input.toPlainText().strip()
        if not text or len(text) > 4000:
            self.status.setText("Escribí entre 1 y 4000 caracteres.")
            return
        self._response_started = time.perf_counter()
        self.response_time.setText("Respuesta: esperando…")
        tool = requested_tool(text)
        if tool == "agenda":
            self.tabs.setCurrentWidget(self.agenda_panel)
            self.agenda_panel.draft_text = text
        elif tool == "web":
            self.tabs.setCurrentWidget(self.web_panel)
            self.web_panel.request_search(text)
        elif tool == "cinema":
            self.tabs.setCurrentWidget(self.cinematheque)
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
            self._last_sources = self.memory_panel.retrieve(text) + self.cinematheque.retrieve(text)
            temporary_context = self.web_panel.context() + self.environment.context()
            messages = build_messages(self.history, text, self.name.text(), self.mode.currentText(),
                memories=self._last_sources, memory_enabled=self.memory_panel.enabled, extra_context=temporary_context)
        except (ValueError, MemoryStoreError, Denied) as exc:
            self._response_started = None
            self.response_time.setText("Respuesta: no enviada")
            self.status.setText(str(exc))
            return
        self.last_messages = messages
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
        self.status.setText("Respuesta lista. El chat sigue siendo temporal." + suffix)

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

    def _stop_for_private(self):
        self._cancel_worker()
        self.voice_panel.deactivate()
        self.web_panel.stop()
        self.environment.stop()
        self.agenda_panel.stop()
        self.cinematheque.stop()
        self.alarm_audio.cancel()
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
        if self._worker is not None:
            return
        self.voice_panel.stop()
        self.alarm_audio.cancel()
        self.history.clear()
        self.last_messages.clear()
        self._last_sources.clear()
        self._pending_user = ""
        self.transcript.clear()
        self.input.clear()
        self.metrics.setText("Nueva sesión: todavía sin mediciones.")
        self.response_time.setText("Respuesta: —")
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
            self.models.clear()
            self._thinking.clear()
            self.metrics.setText("Contenido de sesión borrado al activar privado.")
            self.response_time.setText("Respuesta: —")
        elif self.policy.state is State.PRIVATE:
            try:
                self.policy.resume(local_action=True)
                self.policy.grant("local.request")
                if not self._stopper_registered:
                    self.policy.register_stopper(self._stop_for_private)
                    self._stopper_registered = True
                self.status.setText("Reactivada. Pulsá Detectar Ollama para continuar.")
            except (OSError, Denied):
                self.status.setText("No se pudo reactivar. Se mantiene el bloqueo.")
        self._refresh()

    def show_context(self):
        if self.policy.state is not State.ACTIVE or self._worker is not None:
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
        self._cancel_worker()
        try:
            self.voice_panel.stop_and_wait()
            self.web_panel.stop()
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
        event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--memory-self-test", action="store_true")
    parser.add_argument("--tools-self-test", action="store_true")
    parser.add_argument("--engine-self-test", action="store_true")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args(argv)
    if (args.memory_self_test or args.engine_self_test or args.tools_self_test) and not (args.smoke_test and args.state_dir):
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
