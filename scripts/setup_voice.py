"""Explicit optional voice installation; never called by inference.

Run with Python 3.12: scripts/setup_voice.py --install
Default invocation only prints the plan. No user audio is collected.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

PACKAGES = ("piper-tts==1.8.0", "faster-whisper==1.2.1")
WHISPER_REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
VOICE_REPO = "rhasspy/piper-voices"
VOICE_FOLDER = "es/es_AR/daniela/high"
VOICE_NAME = "es_AR-daniela-high"


def voice_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base).resolve() / "CompanionIA" / "voice-engine"


def install(root: Path) -> None:
    # Keep model downloads and environments out of the public source/vault.
    repo = Path(__file__).resolve().parents[1]
    if root == repo or repo in root.parents or root == repo.parent or repo.parent in root.parents:
        raise ValueError("El motor debe instalarse fuera del repositorio y del baúl.")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()  # Resolve Windows app-data redirection after creation.
    env_path = root / "venv"
    venv.EnvBuilder(with_pip=True).create(env_path)
    python = env_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    lock = repo / "requirements-voice-windows.txt"
    packages = ["-r", str(lock)] if os.name == "nt" and lock.is_file() else list(PACKAGES)
    subprocess.run([str(python), "-m", "pip", "install", "--only-binary=:all:", *packages], check=True)
    # Hub client comes from faster-whisper. Resolve and record the voice revision
    # before fetching all three files, so they share one immutable revision.
    downloader = r'''
import json, os, pathlib, sys
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
from huggingface_hub import HfApi, hf_hub_download, snapshot_download
root = pathlib.Path(sys.argv[1])
repo, folder, name, whisper_revision = sys.argv[2:]
revision = HfApi(token=False).model_info(repo).sha
voice_dir = root / 'models' / 'piper'
voice_dir.mkdir(parents=True, exist_ok=True)
import shutil
for filename in [name + '.onnx', name + '.onnx.json', 'MODEL_CARD']:
    src = hf_hub_download(repo, folder + '/' + filename, revision=revision, token=False,
                         cache_dir=str(root / 'download-cache'))
    shutil.copyfile(src, voice_dir / filename)
snapshot_download('Systran/faster-whisper-base', revision=whisper_revision, token=False,
                  local_dir=str(root / 'models' / 'whisper-base'),
                  allow_patterns=['model.bin','config.json','tokenizer.json','vocabulary.txt','README.md'])
manifest = {'piper_repository': repo, 'piper_revision': revision,
            'voice': name, 'voice_dataset_license': 'CC-BY-SA-4.0; see MODEL_CARD',
            'whisper_repository': 'Systran/faster-whisper-base', 'whisper_revision': whisper_revision}
(root / 'models' / 'sources.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
'''
    subprocess.run([str(python), "-c", downloader, str(root), VOICE_REPO, VOICE_FOLDER,
                    VOICE_NAME, WHISPER_REVISION], check=True)
    # Copy our standalone worker only. Do not install/import the Qt application
    # into the environment containing the optional GPL-3.0-or-later engine.
    shutil.copyfile(repo / "src" / "companion" / "speech_engine.py", root / "speech_engine.py")
    (root / "NOTICE.txt").write_text(
        "Optional independently installed voice programs, not bundled in the Qt executable.\n"
        "Piper TTS 1.8.0: GPL-3.0-or-later https://github.com/OHF-Voice/piper1-gpl\n"
        "faster-whisper 1.2.1: MIT https://github.com/SYSTRAN/faster-whisper\n"
        "Daniela voice: dataset CC-BY-SA-4.0; consult models/piper/MODEL_CARD.\n"
        "Source/model licenses remain applicable. Process separation alone is not a license determination.\n",
        encoding="utf-8")
    print(json.dumps({"python": str(python), "worker": str(root / "speech_engine.py"),
                      "voice_path": str(root / "models" / "piper" / (VOICE_NAME + ".onnx")),
                      "model_path": str(root / "models" / "whisper-base")}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install packages and download models")
    args = parser.parse_args()
    root = voice_root()
    print(f"Motor opcional: {root}")
    print("Paquetes: " + ", ".join(PACKAGES))
    print("Modelos: Daniela es_AR high (~114 MB), Whisper base (~145 MB).")
    print("Piper GPL-3.0-or-later; dataset Daniela CC-BY-SA-4.0. Ver MODEL_CARD.")
    if not args.install:
        print("Plan solamente. Usá --install para descargar e instalar.")
        return 0
    if sys.version_info[:2] != (3, 12):
        print("Prepará el motor con Python 3.12.", file=sys.stderr)
        return 2
    try:
        install(root)
    except (OSError, subprocess.SubprocessError, ValueError):
        print("No se completó la preparación del motor. Revisá la conexión y los permisos.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
