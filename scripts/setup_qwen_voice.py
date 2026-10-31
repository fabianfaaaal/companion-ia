"""Prepare optional Qwen voice locally; --install explicitly downloads packages/models.

Nothing runs during chat inference. Python 3.12 and a compatible NVIDIA GPU
are required for this preview. Source/model licenses remain applicable.
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

QWEN_VERSION = "0.1.1"
TORCH_VERSION = "2.9.1"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
MODEL_REVISION = "85e237c12c027371202489a0ec509ded67b5e4b5"


def voice_root() -> Path:
    return Path.home() / ".companion-ia" / "qwen-voice-engine"


def install(root: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    root = root.resolve()
    if root == repo or repo in root.parents or root == repo.parent or repo.parent in root.parents:
        raise ValueError("El motor debe instalarse fuera del repositorio y del baúl.")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    environment = root / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install",
                    "torch==" + TORCH_VERSION, "torchaudio==" + TORCH_VERSION,
                    "--index-url", TORCH_INDEX], check=True)
    subprocess.run([str(python), "-m", "pip", "install",
                    "qwen-tts==" + QWEN_VERSION, "hf-xet==1.8.0"], check=True)
    downloader = r"""
import json, os, pathlib, sys
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "60"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "30"
from huggingface_hub import snapshot_download
root = pathlib.Path(sys.argv[1])
model_id, revision = sys.argv[2:]
snapshot_download(model_id, revision=revision, token=False,
                  local_dir=str(root / "models" / "qwen"),
                  allow_patterns=["*.json", "*.txt", "*.md", "*.safetensors", "LICENSE*",
                                  "speech_tokenizer/*.json", "speech_tokenizer/*.safetensors"])
manifest = {"model_id": model_id, "revision": revision, "model_license": "Apache-2.0",
            "qwen_tts_version": "0.1.1", "torch_version": "2.9.1", "cuda_wheel": "cu128",
            "speaker": "Serena", "language": "Spanish", "style_instruction_supported": False}
(root / "models" / "sources.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
"""
    subprocess.run([str(python), "-c", downloader, str(root), MODEL_ID, MODEL_REVISION],
                    check=True)
    shutil.copyfile(repo / "src" / "companion" / "qwen_voice_engine.py",
                    root / "qwen_voice_engine.py")
    (root / "NOTICE.txt").write_text(
        "Optional Qwen3-TTS 0.6B CustomVoice and qwen-tts 0.1.1: Apache-2.0.\n"
        "https://github.com/QwenLM/Qwen3-TTS\n"
        "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice\n"
        "PyTorch/Torchaudio: BSD-style licenses and dependencies retain their licenses.\n"
        "https://pytorch.org/get-started/previous-versions/\n"
        "Local preset Serena, Spanish. Not a clone of the user. No style instructions in 0.6B.\n"
        "Model weights and installed-package license metadata remain alongside the runtime.\n",
        encoding="utf-8")
    license_export = r"""
import importlib.metadata, pathlib, shutil, sys
target = pathlib.Path(sys.argv[1]) / "licenses"
for name in ("qwen-tts", "torch", "torchaudio", "hf-xet"):
    package = importlib.metadata.distribution(name)
    for file in package.files or ():
        label = str(file).replace(chr(92), "/")
        if "/licenses/" in label.lower() or label.upper().endswith(("/LICENSE", "/LICENSE.TXT", "/COPYING")):
            destination = target / name / label.replace("/", "__").replace("..", "_")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package.locate_file(file), destination)
"""
    subprocess.run([str(python), "-c", license_export, str(root)], check=True)
    shutil.copyfile(repo / "LICENSE", root / "licenses" / "Qwen-model-Apache-2.0.txt")
    freeze = subprocess.run([str(python), "-m", "pip", "freeze"],
                            check=True, capture_output=True, text=True)
    (root / "requirements-installed.txt").write_text(freeze.stdout, encoding="utf-8")
    print("Motor preparado: " + str(root))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    root = voice_root()
    print("Motor Qwen opcional: " + str(root))
    print("Python 3.12; PyTorch CUDA 12.8 + Qwen3-TTS 0.6B, Serena en español.")
    print("Descargas de varios GB; sin servicios pagos ni envío de texto al sintetizar.")
    if not args.install:
        print("Solo plan. Usá --install para preparar el motor.")
        return 0
    if sys.version_info[:2] != (3, 12):
        print("El preparador requiere Python 3.12.", file=sys.stderr)
        return 2
    try:
        install(root)
    except (OSError, subprocess.SubprocessError, ValueError):
        print("No se completó la preparación de Qwen. Revisá descarga y dependencias.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
