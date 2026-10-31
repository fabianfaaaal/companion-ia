"""First Windows desktop prototype. Conversation exists in RAM only."""
from __future__ import annotations
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QStandardPaths, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)
from companion.ollama_client import Cancelled, ClientError, OllamaClient
from companion.policy import Denied, Policy, State
from companion.session import build_messages, final_answer

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
QPushButton#private { border-color:#dfb26a; color:#ffd99e; }
"""


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
        self._thinking = {}
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
        note = QLabel("Prueba local · Memoria apagada · Sin sensores · La charla se borra al cerrar")
        note.setObjectName("muted")
        layout.addWidget(note)
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
        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setAccessibleName("Conversación de esta sesión")
        self.transcript.setPlaceholderText("Tu conversación aparecerá acá. No se guarda en el disco.")
        layout.addWidget(self.transcript, 1)
        self.input = QTextEdit()
        self.input.setMaximumHeight(105)
        self.input.setAccessibleName("Mensaje")
        self.input.setPlaceholderText("Escribí tu mensaje y pulsá Enviar…")
        layout.addWidget(self.input)
        buttons = QHBoxLayout()
        self.send = QPushButton("Enviar")
        self.send.setObjectName("send")
        self.send.clicked.connect(self.send_message)
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
        self.metrics = QLabel("Los tiempos de esta sesión aparecerán después de una respuesta.")
        self.metrics.setObjectName("muted")
        self.metrics.setWordWrap(True)
        layout.addWidget(self.metrics)
        footer = QHBoxLayout()
        setup = QPushButton("Instalar / abrir Ollama")
        setup.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://ollama.com/download/windows")))
        footer.addWidget(setup)
        foot = QLabel("Motor separado · Sin APIs pagas · Modelo elegido por vos")
        foot.setObjectName("muted")
        footer.addWidget(foot, 1)
        layout.addLayout(footer)
        self.models.currentTextChanged.connect(self._model_changed)
        self._refresh()
        if auto_connect and self.policy.state is State.ACTIVE:
            QTimer.singleShot(200, self.detect_models)

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
        try:
            messages = build_messages(self.history, text, self.name.text(), self.mode.currentText())
        except ValueError as exc:
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
        m = result["metrics"]
        rate = m.get("eval_count", 0) / (m["eval_duration"] / 1e9) if m.get("eval_duration") else None
        first = result.get("first")
        summary = f"Total: {result['elapsed']:.2f} s"
        if first is not None:
            summary += f" · Primer texto del motor: {first:.2f} s"
        if rate is not None:
            summary += f" · Generación: {rate:.1f} tokens/s"
        self.metrics.setText(summary + " · Medición puntual, no benchmark")
        self.status.setText("Respuesta lista. Memoria apagada; conversación solo en esta sesión.")

    def _failure(self, job, message):
        if job != self._job or self._lease is None:
            return
        try:
            self.policy.publish(self._lease, lambda: self.status.setText(message))
        except Denied:
            pass

    def _finished(self, worker):
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()
        self._refresh()

    def _cancel_worker(self):
        self._job += 1
        self._lease = None
        if self._worker is not None:
            self._worker.cancel()

    def _stop_for_private(self):
        self._cancel_worker()
        if self._worker is not None and not self._worker.wait(5000):
            raise RuntimeError("No se pudo confirmar la detención del trabajo local.")

    def cancel_response(self):
        self._cancel_worker()
        self.status.setText("Solicitud detenida. Se descartan los resultados pendientes; el motor puede demorar en liberar recursos.")
        self._refresh()

    def new_conversation(self):
        if self._worker is not None:
            return
        self.history.clear()
        self.last_messages.clear()
        self._pending_user = ""
        self.transcript.clear()
        self.input.clear()
        self.metrics.setText("Nueva sesión: todavía sin mediciones.")
        self._refresh()

    def toggle_private(self):
        if self.policy.state is State.ACTIVE:
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
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args(argv)
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
        QTimer.singleShot(350, capture)
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
