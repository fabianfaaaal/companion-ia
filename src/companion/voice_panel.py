"""Opt-in voice UI. Audio/text IPC only in RAM; independent engine process."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget
from companion.audio_io import AudioIO, AudioError
from companion.policy import Denied, State


from companion.voice_paths import engine_root, engine_ready, register_engine, qwen_root, qwen_ready


class SpeechJob(QThread):
    result = Signal(int, object)
    failure = Signal(int, str)

    def __init__(self, job, root, operation, value, backend="piper"):
        super().__init__()
        self.job, self.root, self.operation, self.value = job, root, operation, value
        self.backend = backend
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._process = None
        self._shutdown_failed = False

    def cancel(self):
        self._cancelled.set()
        with self._lock:
            process = self._process
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass

    def _request(self, request):
        if self._cancelled.is_set():
            return None
        with self._lock:
            if self._cancelled.is_set():
                return None
            process = self._process
            if process is None:
                runtime = qwen_root() if self.operation == "tts" and self.backend == "qwen" else self.root
                worker_name = "qwen_voice_engine.py" if self.operation == "tts" and self.backend == "qwen" else "speech_engine.py"
                python = runtime / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
                env = os.environ.copy()
                env.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                           DO_NOT_TRACK="1", PYTHONDONTWRITEBYTECODE="1")
                if os.name == "nt":
                    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
                    env["PATH"] = os.pathsep.join([str(python.parent), str(windows / "System32"), str(windows)])
                process = subprocess.Popen([str(python), str(runtime / worker_name)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    cwd=runtime, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._process = process
        process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        process.stdin.flush()
        output = process.stdout.readline(8 * 1024 * 1024 + 1)
        if self._cancelled.is_set():
            return None
        if not output or len(output) > 8 * 1024 * 1024:
            raise ValueError
        result = json.loads(output)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ValueError
        return result

    @property
    def shutdown_failed(self):
        return self._shutdown_failed

    def confirm_stopped(self):
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            raise RuntimeError("El proceso de voz sigue activo.")
        if process is not None:
            self._close_engine()

    def _close_engine(self):
        with self._lock:
            process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.poll() is None:
                raise RuntimeError("El proceso de voz no confirmó cierre.")
        except (OSError, subprocess.TimeoutExpired, RuntimeError):
            self._shutdown_failed = True
            # Keep the process reference so PRIVATE cannot be confirmed.
            raise RuntimeError("No se pudo confirmar el cierre del proceso de voz.") from None
        self._shutdown_failed = False
        for pipe in (process.stdin, process.stdout):
            if pipe is not None:
                pipe.close()
        with self._lock:
            self._process = None

    def run(self):
        try:
            if self.operation == "stt":
                import numpy as np
                raw = np.asarray(self.value, dtype="<f4").tobytes()
                request = {"op": "stt", "pcm": base64.b64encode(raw).decode("ascii"), "rate": 16000,
                           "model_path": str((self.root / "models/whisper-base").resolve())}
                result = self._request(request)
                if result is not None and isinstance(result.get("text"), str):
                    self.result.emit(self.job, result)
                return
            text = re.sub(r"[#*_\x60]", "", str(self.value)).strip()
            # Generate short chunks while previous audio plays. Never send thinking.
            segments = []
            while text:
                end = min(180, len(text))
                if end < len(text):
                    split = text.rfind(" ", 0, end)
                    if split > 60:
                        end = split
                segments.append(text[:end])
                text = text[end:].lstrip()
            for segment in segments[:64]:
                result = self._request({"op": "tts", "text": segment,
                    "voice_path": str((self.root / "models/piper/es_AR-daniela-high.onnx").resolve())})
                if result is None:
                    return
                self.result.emit(self.job, result)
        except Exception:
            if not self._cancelled.is_set():
                self.failure.emit(self.job, "No se pudo completar el audio local. Revisá el motor de voz o probá otra vez.")


        finally:
            try:
                self._close_engine()
            except RuntimeError:
                self.failure.emit(self.job, "El proceso de voz no confirmó su cierre.")


class VoicePanel(QWidget):
    transcript = Signal(str)
    notice = Signal(str)

    def __init__(self, policy, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.audio = AudioIO(policy)
        self.root = engine_root()
        self.ready = engine_ready(self.root)
        self.prefer_natural = True
        self._job = 0
        self._worker = None
        self._lease = None
        self._workers = set()
        self._queue = []
        self._capturing = False
        self._capture_started = 0.0
        self._tts_started = 0.0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        layout = QHBoxLayout()
        outer.addLayout(layout)
        self.read = QCheckBox("Leer respuestas")
        self.read.toggled.connect(self._toggle_voice)
        self.mic = QCheckBox("Habilitar micrófono")
        self.mic.toggled.connect(self._toggle_mic)
        self.devices = QComboBox()
        self.devices.addItem("Micrófono del sistema", None)
        self.devices.setAccessibleName("Micrófono")
        self.devices.setMinimumWidth(130)
        self.devices.setMaximumWidth(200)
        self.devices.currentIndexChanged.connect(self._device_changed)
        self.record = QPushButton("Dictar")
        self.record.clicked.connect(self.toggle_record)
        self.review_button = QPushButton("Revisar voz")
        self.review_button.clicked.connect(self.review_engine)
        self.stop_button = QPushButton("Detener audio")
        self.stop_button.clicked.connect(self.stop)
        self.state = QLabel("Voz local lista" if self.ready else "Motor de voz no preparado")
        self.state.setObjectName("muted")
        self.state.setMinimumWidth(220)
        self.state.setMaximumWidth(300)
        self.state.setWordWrap(True)
        for widget in (self.read, self.mic, self.devices, self.record, self.stop_button):
            layout.addWidget(widget)
        settings = QHBoxLayout()
        self.quality = QComboBox()
        self.quality.addItems(["Natural · Qwen experimental", "Ligera · Piper"])
        self.quality.setToolTip("Qwen usa GPU y tiene una carga lenta; no es voz en tiempo real en este preview. Piper usa CPU y responde más rápido. Si Qwen falta, se usa Piper.")
        self.quality.currentIndexChanged.connect(self._quality_changed)
        settings.addWidget(QLabel("Voz:"))
        settings.addWidget(self.quality)
        settings.addWidget(self.review_button)
        settings.addWidget(self.state)
        settings.addStretch()
        outer.addLayout(settings)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(150)
        self.refresh()

    def refresh(self):
        active = self.policy.state is State.ACTIVE
        self.read.setEnabled(active)
        self.mic.setEnabled(active)
        self.devices.setEnabled(active and self.mic.isChecked() and not self._capturing)
        self.record.setEnabled(active and self.ready and self.mic.isChecked() and self._worker is None)
        self.stop_button.setEnabled(active)
        self.review_button.setEnabled(active)
        self.quality.setEnabled(active)
        self.record.setText("Terminar dictado" if self._capturing else "Dictar")

    def _quality_changed(self, index):
        self.prefer_natural = index == 0
        try:
            self.stop_and_wait()
        except (AudioError, RuntimeError):
            self.notice.emit("No se pudo confirmar el cierre del motor anterior.")
        self.refresh()

    def _toggle_voice(self, value):
        if value and self.policy.state is State.ACTIVE:
            self.policy.grant("audio.playback")
            self.policy.grant("speech.synthesize")
        elif self.policy.state is State.ACTIVE:
            self.policy.revoke("audio.playback")
            self.policy.revoke("speech.synthesize")
            self.stop()
        self.refresh()

    def set_microphone_enabled(self, value, *, query_devices=False):
        if self.policy.state is not State.ACTIVE:
            return
        self.mic.blockSignals(True)
        self.mic.setChecked(value)
        self.mic.blockSignals(False)
        if value:
            for operation in ("microphone.devices", "microphone.capture", "speech.transcribe"):
                self.policy.grant(operation)
            if query_devices:
                try:
                    devices = self.audio.devices()
                    self.devices.blockSignals(True)
                    self.devices.clear()
                    self.devices.addItem("Predeterminado del sistema", None)
                    for device in devices:
                        self.devices.addItem(device["name"], device["index"])
                    self.devices.blockSignals(False)
                except (AudioError, Denied):
                    self.notice.emit("No se pudieron consultar los micrófonos. Revisá los permisos de Windows.")
        else:
            for operation in ("microphone.capture", "microphone.devices", "speech.transcribe"):
                self.policy.revoke(operation)
            self.stop()
        self.refresh()

    def _toggle_mic(self, value):
        self.set_microphone_enabled(value, query_devices=value)

    def review_engine(self):
        if self.policy.state is not State.ACTIVE:
            return
        self.stop_and_wait()
        root = engine_root()
        if not engine_ready(root):
            folder = QFileDialog.getExistingDirectory(self, "Elegí la carpeta del motor preparado (venv y models)")
            if not folder:
                self.notice.emit("Falta preparar el motor de voz. Consultá la guía de instalación incluida.")
                return
            root = Path(folder)
        try:
            register_engine(root)
        except (OSError, ValueError):
            self.notice.emit("La carpeta no tiene el motor completo. No se cambió su configuración.")
            return
        self.root, self.ready = root, True
        if self.mic.isChecked():
            self.set_microphone_enabled(True, query_devices=True)
        self.state.setText("Voz natural Qwen lista" if self.prefer_natural and qwen_ready() else "Voz Piper lista")
        self.notice.emit("Motor reconocido. Leer respuestas reproduce los próximos mensajes; Dictar capta tu voz.")
        self.refresh()

    def _device_changed(self):
        if self._capturing:
            return
        try:
            self.stop_and_wait()
            self.audio = AudioIO(self.policy, input_device=self.devices.currentData())
        except (AudioError, RuntimeError):
            self.notice.emit("No se pudo cerrar el dispositivo anterior.")

    def _launch(self, operation, value):
        self._job += 1
        lease = self.policy.begin("speech.transcribe" if operation == "stt" else "speech.synthesize")
        self._tts_started = time.monotonic()
        job = SpeechJob(self._job, self.root, operation, value, backend="qwen" if operation == "tts" and self.prefer_natural and qwen_ready() else "piper")
        self._worker, self._lease = job, lease
        self._workers.add(job)
        job.result.connect(self._result)
        job.failure.connect(self._failure)
        job.finished.connect(lambda: self._finished(job))
        self.policy.publish(lease, job.start)
        self.refresh()

    def speak(self, text):
        if not self.read.isChecked() or self.policy.state is not State.ACTIVE or self._capturing:
            return
        if not self.ready:
            self.notice.emit("Falta preparar el motor: pulsá Revisar voz.")
            return
        self.stop()
        try:
            self._launch("tts", text)
            self.state.setText("Preparando voz…")
        except Denied:
            return

    def toggle_record(self):
        if self.policy.state is not State.ACTIVE or not self.mic.isChecked() or not self.ready:
            return
        try:
            if self._capturing:
                self._finish_capture()
            else:
                self.stop()
                self.audio.begin_recording()
                self._capturing = True
                self._capture_started = time.monotonic()
                self.state.setText("Micrófono activo · Máximo 30 s")
        except (AudioError, Denied):
            self.notice.emit("No se pudo abrir el micrófono. Elegí otro dispositivo o revisá los permisos de Windows.")
        self.refresh()

    def _finish_capture(self):
        self._capturing = False
        samples = self.audio.finish_recording()
        self.state.setText("Transcribiendo en esta PC…")
        self._launch("stt", samples)

    def _result(self, job, result):
        if job != self._job or self._lease is None:
            return
        try:
            self.policy.publish(self._lease, lambda: self._accept(result))
        except (Denied, ValueError, AudioError):
            self.notice.emit("Se descartó un resultado de audio no válido.")
            return

    def _accept(self, result):
        if result.get("op") == "stt":
            text = result.get("text", "").strip()
            if text:
                self.transcript.emit(text)
                self.notice.emit("Dictado listo. Revisalo y pulsá Enviar cuando quieras.")
            else:
                self.notice.emit("No se detectaron palabras. Probá acercarte al micrófono.")
            self.state.setText("Dictado listo")
        elif result.get("op") == "tts" and self.read.isChecked():
            import numpy as np
            rate = result.get("rate")
            if result.get("format") != "s16le" or type(rate) is not int or not 8000 <= rate <= 96000:
                raise ValueError("Audio no válido")
            raw = base64.b64decode(result.get("pcm", ""), validate=True)
            if len(raw) % 2 or len(raw) > rate * 2 * 30:
                raise ValueError("Audio no válido")
            samples = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
            if sum(len(a) for a, _ in self._queue) + len(samples) > 16000 * 300:
                raise ValueError("Cola de voz llena")
            self._queue.append((samples, rate))

    def _failure(self, job, message):
        if job == self._job and self.policy.state is State.ACTIVE:
            self.notice.emit(message)
            self.state.setText("Audio no disponible")

    def _finished(self, job):
        if self._worker is job:
            self._worker = None
        if not job.shutdown_failed:
            self._workers.discard(job)
            job.deleteLater()
        self.refresh()

    def _tick(self):
        if self.policy.state is not State.ACTIVE:
            return
        try:
            if self._capturing:
                if not self.audio.recording or time.monotonic() - self._capture_started >= 30:
                    self._finish_capture()
                    self.refresh()
            elif self.read.isChecked() and not self.audio.playing and self._queue:
                samples, rate = self._queue.pop(0)
                self.audio.play_samples(samples, rate)
                self.state.setText("Leyendo respuesta…")
            elif self._worker is not None and getattr(self._worker, "operation", None) == "tts" and not self.audio.playing:
                backend = "Qwen" if self._worker.backend == "qwen" else "Piper"
                self.state.setText(f"Preparando {backend}… {int(time.monotonic() - self._tts_started)} s")
            elif not self._queue and self._worker is None and not self.audio.playing:
                self.state.setText(("Voz natural Qwen lista" if self.prefer_natural and qwen_ready() else "Voz Piper lista") if self.ready else "Falta motor · Revisar voz")
        except (AudioError, Denied):
            self.stop()
            self.notice.emit("Se detuvo el audio. Revisá el dispositivo seleccionado.")

    def stop(self):
        self._job += 1
        self._lease = None
        self._queue.clear()
        self._capturing = False
        for worker in tuple(self._workers):
            worker.cancel()
        self.audio.cancel()
        self.refresh()

    def stop_and_wait(self):
        self.stop()
        for worker in tuple(self._workers):
            if not worker.wait(5000):
                raise RuntimeError("El motor de voz no confirmó su cierre.")
            worker.confirm_stopped()

    def deactivate(self):
        self.stop_and_wait()
        for checkbox in (self.read, self.mic):
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        self.devices.clear()
        self.refresh()
