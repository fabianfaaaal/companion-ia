# Companion IA

[Leer en español](README_spanish.md) · [Windows downloads](https://github.com/fabianfaaaal/companion-ia/releases) · [Android alpha](https://github.com/fabianfaaaal/companion-ia/releases/tag/android-alpha-0.1.0)

Companion IA is an open-source desktop assistant that runs local language models, speaks their responses, and lets you choose which context carries over between conversations. You can switch models while keeping the personality and context you have configured.

## Try it on Windows

1. Download a Windows preview ZIP from [GitHub Releases](https://github.com/fabianfaaaal/companion-ia/releases), extract the entire folder, and open **CompanionIA.exe**.
2. Set up Ollama and download a local model. Follow the [first steps guide (Spanish)](docs/primeros-pasos.md).
3. Start a chat with memory off. To try dictation or spoken replies, follow the [voice setup guide (Spanish)](docs/voice-ready-preview.md).

The Android alpha pairs with the current Windows source build using a one-time QR code. Both devices must be on the same Wi-Fi. Android sends messages and displays replies while Ollama runs on the PC. [Android setup and build instructions (Spanish)](android/README.md)

## What you can try

| Feature | What the preview offers |
| --- | --- |
| Local chat | Ollama, configurable personality and privacy profiles, optional conversation history |
| Context | Notes, context cards, imported chat histories, and suggested updates you can review |
| Voice | Local Whisper dictation, an experimental push-to-talk mode, and spoken replies with Piper or Qwen TTS |
| Standalone reader | Paste text into a small Windows app and listen with a compatible installed voice |
| Personal archive | Photo gallery, reminders, folder-based backups, and guided restore |
| External models | Optional OpenAI and DeepSeek adapters with separate controls for the data you share. Providers may charge for API use. |

## Run from source

Use **Python 3.12** and an Ollama installation with a local model. From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build-windows.txt
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m companion.desktop
```

To open the standalone reader:

```powershell
powershell.exe -NoProfile -STA -File .\scripts\reader-windows.ps1
```

## Privacy and architecture

The Qt desktop app checks permissions before gathering context, routing a request, or playing audio. Memory, sensor access, and sharing with external providers have separate controls. Private mode leaves a temporary local chat and disables memory access, voice, sensors, external APIs, and the Android session.

The personal archive is currently stored without encryption. Use test data in the alpha. The Android client verifies the PC's TLS identity using the QR code and keeps the mobile conversation in memory. The PC records connection events locally.

## Validation and project status

Windows and Android are in alpha. The Android APK was built and launched on a physical phone, and the encrypted transport, one-time pairing, and local connection logging were tested with synthetic data. A full phone-to-PC Ollama session on Wi-Fi remains to be verified. Remote connectivity over the internet, persistent trusted devices, email confirmation, and mobile voice and sensors are planned. Linux has CI coverage, with packaging still pending.

Run the Windows test suite from the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

## Maintainer and license

Companion IA is maintained by [Fabián](https://github.com/fabianfaaaal). Setup guides are available in [docs](docs/), and reproducible bugs can be reported through [GitHub Issues](https://github.com/fabianfaaaal/companion-ia/issues).

The project code is licensed under [Apache 2.0](LICENSE). Dependencies, models, and voices have their own terms. See [third-party notices](THIRD_PARTY_NOTICES.md).
