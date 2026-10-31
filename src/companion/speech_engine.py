"""Optional local voice worker. JSON lines in/out; user content stays in RAM.

Run with the separately installed voice environment, never import it into Qt.
Models must already exist locally. Kill this process to cancel active work.
"""

from __future__ import annotations

import base64
import contextlib
from functools import lru_cache
import io
import json
import os
from pathlib import Path
import sys

MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 2000
MAX_AUDIO_SECONDS = 30
INPUT_RATE = 16000


class SpeechError(ValueError):
    """Safe error without user content or local paths."""


def local_path(value: object, *, directory: bool = False) -> Path:
    if not isinstance(value, str) or not value:
        raise SpeechError("model_missing")
    path = Path(value)
    if not path.is_absolute() or not (path.is_dir() if directory else path.is_file()):
        raise SpeechError("model_missing")
    return path


@lru_cache(maxsize=1)
def load_voice(path: str):
    """Keep only the most recently used model/session, never synthesized audio.

    The worker is single-threaded. Killing it on PRIVATE also releases this cache.
    Paths are checked for existence by synthesize before every cache lookup.
    """
    from piper import PiperVoice
    try:
        import onnxruntime as ort
        from piper.config import PiperConfig
        from piper.phonemize_espeak import ESPEAK_DATA_DIR
    except (ImportError, AttributeError):
        # Older compatible providers may expose only the documented load API.
        return PiperVoice.load(path, use_cuda=False)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    with open(path + ".json", encoding="utf-8") as source:
        config = PiperConfig.from_dict(json.load(source))
    return PiperVoice(config=config,
                      session=ort.InferenceSession(path, sess_options=options,
                                                   providers=["CPUExecutionProvider"]),
                      espeak_data_dir=ESPEAK_DATA_DIR, download_dir=Path(path).parent)


def synthesize(request: dict) -> dict:
    text = request.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise SpeechError("invalid_text")
    path = local_path(request.get("voice_path"))
    if not Path(str(path) + ".json").is_file():
        raise SpeechError("model_missing")
    voice = load_voice(str(path.resolve()))
    audio = bytearray()
    rate = None
    for chunk in voice.synthesize(text):
        if chunk.sample_width != 2 or chunk.sample_channels != 1:
            raise SpeechError("unsupported_audio")
        if rate is not None and chunk.sample_rate != rate:
            raise SpeechError("unsupported_audio")
        rate = chunk.sample_rate
        if not isinstance(rate, int) or not 8000 <= rate <= 96000:
            raise SpeechError("unsupported_audio")
        data = chunk.audio_int16_bytes
        if len(data) % 2:
            raise SpeechError("unsupported_audio")
        if len(audio) + len(data) > rate * 2 * MAX_AUDIO_SECONDS:
            raise SpeechError("audio_too_long")
        audio.extend(data)
    if not audio:
        raise SpeechError("empty_audio")
    return {"ok": True, "op": "tts", "pcm": base64.b64encode(audio).decode("ascii"),
            "format": "s16le", "rate": rate, "channels": 1}


def transcribe(request: dict) -> dict:
    if type(request.get("rate")) is not int or request["rate"] != INPUT_RATE:
        raise SpeechError("invalid_rate")
    encoded = request.get("pcm")
    if not isinstance(encoded, str) or len(encoded) > INPUT_RATE * MAX_AUDIO_SECONDS * 4 * 4 // 3 + 4:
        raise SpeechError("invalid_audio")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise SpeechError("invalid_audio") from None
    if not raw or len(raw) % 4 or len(raw) > INPUT_RATE * MAX_AUDIO_SECONDS * 4:
        raise SpeechError("invalid_audio")
    path = local_path(request.get("model_path"), directory=True)
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        if not (path / name).is_file():
            raise SpeechError("model_missing")
    import numpy as np
    from faster_whisper import WhisperModel

    samples = np.frombuffer(raw, dtype="<f4")
    if not np.isfinite(samples).all() or (np.abs(samples) > 1).any():
        raise SpeechError("invalid_audio")
    model = WhisperModel(str(path), device="cpu", compute_type="int8", cpu_threads=4,
                         local_files_only=True)
    segments, _ = model.transcribe(samples, language="es", beam_size=1,
                                  condition_on_previous_text=False, vad_filter=False)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    if len(text) > 8000:
        raise SpeechError("invalid_result")
    return {"ok": True, "op": "stt", "text": text}


def handle(request: object) -> dict:
    if not isinstance(request, dict):
        raise SpeechError("invalid_request")
    if request.get("op") == "tts":
        return synthesize(request)
    if request.get("op") == "stt":
        return transcribe(request)
    raise SpeechError("invalid_operation")


def serve(source, target) -> int:
    """Consume bounded UTF-8 JSON lines, never echo malformed input."""
    while True:
        line = source.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            return 0
        if len(line) > MAX_REQUEST_BYTES:
            target.write('{"ok":false,"error":"request_too_large"}\n')
            target.flush()
            return 2
        try:
            request = json.loads(line)
            # Third-party Python output must not corrupt or leak into protocol.
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = handle(request)
        except SpeechError as error:
            result = {"ok": False, "error": str(error)}
        except (ValueError, UnicodeError, TypeError):
            result = {"ok": False, "error": "invalid_request"}
        except Exception:
            result = {"ok": False, "error": "engine_unavailable"}
        target.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")) + "\n")
        target.flush()


def main() -> int:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    return serve(sys.stdin.buffer, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
