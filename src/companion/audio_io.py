"""Optional local audio hardware adapter; all samples remain in RAM.

The application grants permissions and registers cancel as a private-mode stopper.
No recognition, synthesis, transmission, files, or global sounddevice stop calls.
"""
from __future__ import annotations

import threading
from .policy import Policy, Denied

RATE = 16000
MAX_CAPTURE_FRAMES = RATE * 30


class AudioError(RuntimeError):
    pass


class AudioIO:
    def __init__(self, policy: Policy, *, module=None, numpy_module=None, input_device=None):
        if input_device is not None and (type(input_device) is not int or input_device < 0):
            raise ValueError("Dispositivo de entrada no válido.")
        self._policy = policy
        self._module = module
        self._numpy = numpy_module
        self._input_device = input_device
        self._lock = threading.Lock()
        self._capture = None
        self._playback = None

    def _dependencies(self):
        try:
            if self._numpy is None:
                import numpy
                self._numpy = numpy
            if self._module is None:
                import sounddevice
                self._module = sounddevice
        except (ImportError, OSError):
            raise AudioError("El soporte de audio local no está disponible.") from None
        return self._module, self._numpy

    @staticmethod
    def _close(stream):
        if stream is None:
            return
        failed = False
        try:
            stream.abort()
        except Exception:
            failed = True
        try:
            stream.close()
        except Exception:
            failed = True
        if failed:
            raise AudioError("No se pudo cerrar el dispositivo de audio.")

    def devices(self) -> list[dict]:
        lease = self._policy.begin("microphone.devices")
        module, _ = self._dependencies()
        def query():
            try:
                return [{"index": index, "name": str(device["name"])}
                        for index, device in enumerate(module.query_devices())
                        if device.get("max_input_channels", 0) > 0]
            except Exception:
                raise AudioError("No se pudieron consultar los dispositivos de entrada.") from None
        return self._policy.publish(lease, query)

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._capture is not None and self._capture["active"]

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._playback is not None and self._playback["active"]

    def cancel(self) -> None:
        with self._lock:
            states = (self._capture, self._playback)
            self._capture = self._playback = None
            streams = []
            for state in states:
                if state is not None:
                    state["active"] = False
                    state["data"] = []
                    streams.append(state.get("stream"))
        failed = False
        for stream in streams:
            try:
                self._close(stream)
            except AudioError:
                failed = True
        if failed:
            raise AudioError("No se pudo detener todo el audio local.")

    def begin_recording(self) -> None:
        lease = self._policy.begin("microphone.capture")
        module, _ = self._dependencies()
        self.cancel()
        state = {"active": True, "data": [], "frames": 0, "lease": lease, "stream": None}
        with self._lock:
            self._capture = state
        def callback(indata, frames, time_info, status):
            stop = False
            def accept():
                nonlocal stop
                with self._lock:
                    if self._capture is not state or not state["active"]:
                        stop = True
                        return
                    count = min(frames, MAX_CAPTURE_FRAMES - state["frames"])
                    if count > 0:
                        state["data"].append(indata[:count, 0].copy())
                        state["frames"] += count
                    if state["frames"] >= MAX_CAPTURE_FRAMES:
                        state["active"] = False
                        stop = True
            try:
                self._policy.publish(lease, accept)
            except Denied:
                with self._lock:
                    state["active"] = False
                    state["data"] = []
                stop = True
            if stop:
                raise module.CallbackStop
        try:
            def start():
                # Construction opens hardware, so it belongs to the policy gate too.
                with self._lock:
                    if self._capture is not state:
                        raise AudioError("La grabación fue cancelada.")
                stream = module.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                            device=self._input_device, callback=callback)
                with self._lock:
                    current = self._capture is state
                    if current:
                        state["stream"] = stream
                if not current:
                    self._close(stream)
                    raise AudioError("La grabación fue cancelada.")
                stream.start()
            self._policy.publish(lease, start)
        except Exception as exc:
            self.cancel()
            if isinstance(exc, (AudioError, Denied)):
                raise
            raise AudioError("No se pudo iniciar el micrófono.") from None

    def finish_recording(self):
        with self._lock:
            state = self._capture
            if state is not None:
                state["active"] = False
        if state is None:
            raise AudioError("No hay una grabación disponible.")
        try:
            self._close(state["stream"])
            state["stream"] = None
            def collect():
                with self._lock:
                    if self._capture is not state:
                        raise AudioError("La grabación fue cancelada.")
                    np = self._numpy
                    return np.concatenate(state["data"]).astype("float32", copy=False) if state["data"] else np.empty(0, dtype="float32")
            return self._policy.publish(state["lease"], collect)
        finally:
            with self._lock:
                if self._capture is state:
                    self._capture = None
                state["data"] = []

    def play_samples(self, samples, sample_rate: int) -> None:
        lease = self._policy.begin("audio.playback")
        module, np = self._dependencies()
        if type(sample_rate) is not int or not 8000 <= sample_rate <= 96000:
            raise AudioError("Frecuencia de audio no válida.")
        try:
            data = np.asarray(samples, dtype="float32")
            if data.ndim != 1 or data.size == 0 or data.size > sample_rate * 120 or not np.isfinite(data).all():
                raise ValueError
            data = np.clip(data, -1.0, 1.0).copy()
        except (ValueError, TypeError, OverflowError):
            raise AudioError("Las muestras de audio no son válidas.") from None
        self.cancel()
        state = {"active": True, "data": data, "position": 0, "stream": None}
        with self._lock:
            self._playback = state
        def callback(outdata, frames, time_info, status):
            outdata.fill(0)
            stop = False
            def deliver():
                nonlocal stop
                with self._lock:
                    if self._playback is not state or not state["active"]:
                        stop = True
                        return
                    count = min(frames, len(state["data"]) - state["position"])
                    outdata[:count, 0] = state["data"][state["position"]:state["position"] + count]
                    state["position"] += count
                    if state["position"] >= len(state["data"]):
                        state["active"] = False
                        stop = True
            try:
                self._policy.publish(lease, deliver)
            except Denied:
                with self._lock:
                    state["active"] = False
                    state["data"] = []
                stop = True
            if stop:
                raise module.CallbackStop
        try:
            def start():
                with self._lock:
                    if self._playback is not state:
                        raise AudioError("La reproducción fue cancelada.")
                stream = module.OutputStream(samplerate=sample_rate, channels=1, dtype="float32", callback=callback)
                with self._lock:
                    current = self._playback is state
                    if current:
                        state["stream"] = stream
                if not current:
                    self._close(stream)
                    raise AudioError("La reproducción fue cancelada.")
                stream.start()
            self._policy.publish(lease, start)
        except Exception as exc:
            self.cancel()
            if isinstance(exc, (AudioError, Denied)):
                raise
            raise AudioError("No se pudo iniciar la salida de audio.") from None
