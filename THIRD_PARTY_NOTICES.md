# Third-party notices — Windows preview

Companion IA code is Apache-2.0; see LICENSE. This directory bundle dynamically loads Qt/PySide6 libraries and is intended for early testing. It contains no Ollama binary or language model.

- **Python 3.12**: Python Software Foundation license; included when provided by the build runtime. Sources: https://www.python.org/downloads/source/
- **PySide6 Essentials / Shiboken6 6.12.0**: open-source licensing documented as LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only. This application uses QtCore, QtGui and QtWidgets through the LGPL route; included licenses and upstream notices remain applicable.
- **Qt 6.12.0 libraries**: source and module-specific notices at https://code.qt.io/cgit/qt/qtbase.git/?h=v6.12.0 and https://doc.qt.io/qt-6/licensing.html. PySide/Shiboken source: https://code.qt.io/cgit/pyside/pyside-setup.git/?h=v6.12.0.
- **PyInstaller 6.22.3**: GPL license with bootloader exception; its terms permit distributing generated executables under their own applicable license. See included COPYING and https://pyinstaller.org/en/stable/license.html.

GNU LGPLv3 and GPLv3 texts are in licenses/. Wheels also contain an alternative commercial-license notice; no commercial Qt license is claimed here. The open-source terms described by the PyPI package apply to this preview.

Qt libraries remain separate in _internal, and can be replaced by compatible rebuilt libraries. Rebuild instructions and pinned dependencies are provided in the repository. Do not use the preview to prohibit reverse engineering needed to debug changes to the LGPL components.

This preview is not a security-audited or signed installer. Packaging of additional modules/resources must add their notices before redistribution.

## Audio del preview de notas y voz
NumPy (BSD), sounddevice (MIT), PortAudio (MIT) y CFFI (MIT) aportan captura/reproducción y arrays al ejecutable. Se conservan los avisos disponibles en sus paquetes.
Piper y faster-whisper se instalan opcionalmente en un entorno independiente; sus paquetes/modelos no están incluidos en este ZIP. Piper es GPL-3.0-or-later, faster-whisper MIT; Daniela declara dataset CC-BY-SA-4.0 en su MODEL_CARD. Consultar docs/voice-notes-preview.md y las licencias/atribuciones del directorio del motor.
