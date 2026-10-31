"""Optional Qwen3-TTS worker. Local model only; bounded JSON/PCM in RAM.

Run in its own prepared environment. No microphone or speaker access.
Qwen 0.6B presets do not support instruction-based style control.
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
MODEL_ROOT = Path(__file__).resolve().parent / "models" / "qwen"


class SpeechError(ValueError):
    """Safe protocol error without text, paths or dependency exceptions."""


def require_model() -> Path:
    root = MODEL_ROOT.resolve()
    required = ("config.json", "model.safetensors", "tokenizer_config.json",
                "speech_tokenizer/config.json", "speech_tokenizer/model.safetensors")
    if not root.is_dir() or any(not (root / name).is_file() for name in required):
        raise SpeechError("model_missing")
    return root


@lru_cache(maxsize=1)
def load_model(path: str):
    import torch
    from qwen_tts import Qwen3TTSModel

    if not torch.cuda.is_available():
        raise SpeechError("cuda_unavailable")
    torch.set_num_threads(4)
    # Native PyTorch attention avoids compiling flash-attn on Windows.
    return Qwen3TTSModel.from_pretrained(
        path, device_map="cuda:0", dtype=torch.bfloat16,
        attn_implementation="sdpa", local_files_only=True)


def synthesize(request: dict) -> dict:
    text = request.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise SpeechError("invalid_text")
    root = require_model()
    model = load_model(str(root))
    import numpy as np

    wavs, rate = model.generate_custom_voice(
        text=text, language="Spanish", speaker="Serena",
        max_new_tokens=384)
    if type(rate) is not int or not 8000 <= rate <= 96000 or len(wavs) != 1:
        raise SpeechError("unsupported_audio")
    samples = np.asarray(wavs[0], dtype=np.float32)
    if (samples.ndim != 1 or not samples.size or samples.size > rate * MAX_AUDIO_SECONDS
            or not np.isfinite(samples).all()):
        raise SpeechError("invalid_audio")
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    return {"ok": True, "op": "tts", "pcm": base64.b64encode(pcm).decode("ascii"),
            "format": "s16le", "rate": rate, "channels": 1}


def handle(request: object) -> dict:
    if not isinstance(request, dict):
        raise SpeechError("invalid_request")
    if request.get("op") != "tts":
        raise SpeechError("invalid_operation")
    return synthesize(request)


def serve(source, target) -> int:
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
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      HF_HUB_DISABLE_IMPLICIT_TOKEN="1", DO_NOT_TRACK="1",
                      TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    return serve(sys.stdin.buffer, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
