"""Reproducible directory-style Windows test bundle (not an installer)."""
from pathlib import Path
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
        "--distpath", str(ROOT / "dist"),
        str(ROOT / "apps" / "desktop" / "launcher.py"),
    ], cwd=ROOT, check=True, env=build_env)
    target = ROOT / "dist" / "CompanionIA"
    shutil.copy2(ROOT / "LICENSE", target / "LICENSE")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", target / "THIRD_PARTY_NOTICES.md")
    shutil.copytree(ROOT / "third_party" / "licenses", target / "licenses", dirs_exist_ok=True)
    for package in ("PySide6-Essentials", "shiboken6", "pyinstaller"):
        distribution = importlib.metadata.distribution(package)
        for file in distribution.files or ():
            if "/licenses/" in str(file).replace("\\", "/"):
                destination = target / "licenses" / package / Path(str(file)).name
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
        "Memoria apagada: no conserva conversaciones. Modo privado borra la sesion.\n"
        "No incluye sensores, recuerdos ni instalador completo.\n"
        "No tiene firma digital comercial. Consultar THIRD_PARTY_NOTICES.md.\n",
        encoding="utf-8",
    )
    archive = ROOT / "dist" / "CompanionIA-windows-preview.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for file in target.rglob("*"):
            if file.is_file():
                output.write(file, file.relative_to(ROOT / "dist"))
    print(str(target / "CompanionIA.exe"))
    print(str(archive))


if __name__ == "__main__":
    main()
