"""Discover explicitly registered speech runtimes across Windows app contexts."""
import json
import os
from pathlib import Path


def engine_ready(root):
    return all(path.is_file() for path in (
        root / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python"),
        root / "speech_engine.py", root / "models/piper/es_AR-daniela-high.onnx",
        root / "models/piper/es_AR-daniela-high.onnx.json",
        root / "models/whisper-base/model.bin"))


def engine_root():
    home = Path.home()
    config = home / ".companion-ia" / "voice-location.json"
    try:
        saved = json.loads(config.read_text(encoding="utf-8"))
        root = Path(saved["root"])
        if saved.get("version") == 1 and root.is_absolute() and engine_ready(root):
            return root.resolve()
    except (OSError, ValueError, KeyError, TypeError):
        pass
    stable = home / ".companion-ia" / "voice-engine"
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") or str(home / ".local/share")
    candidates = [stable, Path(base) / "CompanionIA/voice-engine", home / "AppData/Local/CompanionIA/voice-engine"]
    for root in candidates:
        if engine_ready(root):
            return root.resolve()
    return stable


def register_engine(root):
    root = Path(root).resolve()
    if not engine_ready(root):
        raise ValueError("La carpeta no contiene un motor de voz completo.")
    directory = Path.home() / ".companion-ia"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "voice-location.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, "root": str(root)}), encoding="utf-8")
    temporary.replace(path)


def qwen_root():
    return Path.home() / ".companion-ia/qwen-voice-engine"


def qwen_ready():
    root = qwen_root()
    return (root / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")).is_file() and (root / "qwen_voice_engine.py").is_file() and (root / "models/qwen/config.json").is_file() and any((root / "models/qwen").glob("*.safetensors"))
