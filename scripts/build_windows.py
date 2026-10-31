"""Reproducible directory-style Windows test bundle (not an installer)."""
from pathlib import Path
import argparse
import importlib.metadata
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "win32":
        raise SystemExit("Build the Windows executable on Windows.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="memory-voice-preview", choices=["modern-chat-preview", "providers-preview", "voice-ready-preview", "interaction-preview", "tools-preview", "memory-voice-preview", "desktop-preview"])
    args = parser.parse_args()
    dist_root = ROOT / "dist" / args.variant
    build_env = os.environ.copy()
    windows_dir = Path(os.environ.get("WINDIR", "C:/Windows"))
    # Avoid DLLs from unrelated tools (e.g. Poppler ICU) on the developer PATH.
    build_env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent),
                                         str(windows_dir / "System32"), str(windows_dir)])
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir",
        "--windowed", "--noupx", "--clean", "--name", "CompanionIA",
        "--paths", str(ROOT / "src"), "--specpath", str(ROOT / "build" / "spec"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--distpath", str(dist_root),
        str(ROOT / "apps" / "desktop" / "launcher.py"),
    ], cwd=ROOT, check=True, env=build_env)
    target = dist_root / "CompanionIA"
    shutil.copy2(ROOT / "LICENSE", target / "LICENSE")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", target / "THIRD_PARTY_NOTICES.md")
    shutil.copytree(ROOT / "third_party" / "licenses", target / "licenses", dirs_exist_ok=True)
    guide = "modern-chat-preview.md" if args.variant == "modern-chat-preview" else "providers-and-history.md" if args.variant == "providers-preview" else "voice-ready-preview.md" if args.variant == "voice-ready-preview" else ("integrated-tools-preview.md" if args.variant in ("tools-preview", "interaction-preview") else "voice-notes-preview.md")
    shutil.copy2(ROOT / "docs" / guide, target / "COMO_PROBAR.md")
    for package in ("PySide6-Essentials", "shiboken6", "pyinstaller", "numpy", "sounddevice", "cffi", "pycparser", "Pillow"):
        distribution = importlib.metadata.distribution(package)
        for file in distribution.files or ():
            if "/licenses/" in str(file).replace("\\", "/"):
                destination = target / "licenses" / package / str(file).replace("/", "__").replace("\\", "__").replace("..", "_")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(distribution.locate_file(file), destination)
    for name in ("LICENSE.txt", "LICENSE"):
        candidate = Path(sys.base_prefix) / name
        if candidate.is_file():
            shutil.copy2(candidate, target / "licenses" / "Python-LICENSE.txt")
            break
    (target / "LEEME.txt").write_text(
        "Companion IA - prueba local\n"
        "Abri CompanionIA.exe. Mantene la carpeta _internal junto al ejecutable.\n"
        "Ollama debe estar abierto y tener un modelo local descargado.\n"
        "Detecta el motor y elegi el modelo; despues escribi y pulsa Enviar.\n"
        "Funciones activadas al primer inicio; desmarca las que no quieras. Chat temporal.\n"
        "Agenda y Cinemateca inicial disponibles; sin escucha permanente. Motor de voz separado.\n"
        "No tiene firma digital comercial. Consultar THIRD_PARTY_NOTICES.md.\n",
        encoding="utf-8",
    )
    archive = dist_root / "CompanionIA-windows-preview.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for file in target.rglob("*"):
            if file.is_file():
                output.write(file, file.relative_to(dist_root))
    print(str(target / "CompanionIA.exe"))
    print(str(archive))


if __name__ == "__main__":
    main()
