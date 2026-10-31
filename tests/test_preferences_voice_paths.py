"""Synthetic preference and runtime-discovery checks, without real capture."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.preferences import DEFAULTS, Preferences
from companion.voice_paths import engine_root, engine_ready, register_engine


class PreferenceTests(unittest.TestCase):
    def test_first_start_features_enabled_and_explicit_off_survives(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Preferences(Path(folder) / "preferences.json")
            options = store.load()
            self.assertTrue(all(options.values()))
            options["microphone"] = options["memory"] = False
            store.save(options)
            self.assertFalse(store.load()["memory"])
            self.assertFalse(store.load()["microphone"])
            self.assertNotIn("messages", store.path.read_text())

    def test_invalid_file_does_not_reactivate_disabled_features(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "preferences.json"
            for value in ("broken", '{"version":1,"options":{"memory":true}}'):
                path.write_text(value)
                self.assertFalse(any(Preferences(path).load().values()))


class RuntimeTests(unittest.TestCase):
    def prepare(self, root):
        for name in ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python",
                     "speech_engine.py", "models/piper/es_AR-daniela-high.onnx",
                     "models/piper/es_AR-daniela-high.onnx.json", "models/whisper-base/model.bin"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

    def test_registered_root_survives_appdata_change(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            root = home / "installed-runtime"
            self.prepare(root)
            with patch("companion.voice_paths.Path.home", return_value=home):
                register_engine(root)
                with patch.dict(os.environ, {"LOCALAPPDATA": str(home / "different-context")}):
                    self.assertEqual(engine_root(), root.resolve())

    def test_invalid_location_is_ignored_and_incomplete_root_not_registered(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            config = home / ".companion-ia/voice-location.json"
            config.parent.mkdir()
            config.write_text(json.dumps({"version": 1, "root": "relative"}))
            with patch("companion.voice_paths.Path.home", return_value=home), patch.dict(os.environ, {"LOCALAPPDATA": str(home / "empty")}):
                self.assertEqual(engine_root(), home / ".companion-ia/voice-engine")
                with self.assertRaises(ValueError):
                    register_engine(home / "missing")


try:
    from PySide6.QtWidgets import QApplication
    from companion.desktop import Window
    from companion.policy import State
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt not installed")
class DefaultUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "privacy.json"
        self.window = Window(self.path, auto_connect=False)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_enabled_defaults_never_open_microphone_or_infer_automatically(self):
        w = self.window
        with patch.object(w.voice_panel.audio, "devices") as devices, patch.object(w.voice_panel.audio, "begin_recording") as record, patch.object(w.environment, "read_title") as title:
            w.enable_preferences()
            self.assertTrue(w.memory_panel.enabled)
            self.assertTrue(w.voice_panel.read.isChecked())
            self.assertTrue(w.voice_panel.mic.isChecked())
            self.assertTrue(w.web_panel.allow.isChecked())
            devices.assert_not_called()
            record.assert_not_called()
            title.assert_not_called()
        w.environment.timer.stop()
        self.assertFalse((self.path.parent / "memory.sqlite").exists())

    def test_user_unchecks_and_reopen_restores_off(self):
        w = self.window
        w.enable_preferences()
        w.voice_panel.mic.click()
        w.memory_panel.toggle.click()
        w.enable_preferences()
        self.assertFalse(w.voice_panel.mic.isChecked())
        self.assertFalse(w.memory_panel.enabled)
        w.environment.timer.stop()

    def test_private_restart_suppresses_enabled_defaults(self):
        w = self.window
        w.enable_preferences()
        w.toggle_private()
        self.assertEqual(w.policy.state, State.PRIVATE)
        with patch.object(w.environment, "read_title") as title:
            w.enable_preferences()
            self.assertFalse(w.memory_panel.enabled)
            self.assertFalse(w.voice_panel.mic.isChecked())
            self.assertFalse(w.environment.timer.isActive())
            title.assert_not_called()

    def test_missing_engine_does_not_lock_preference_checkboxes(self):
        panel = self.window.voice_panel
        panel.ready = False
        panel.refresh()
        self.assertTrue(panel.read.isEnabled())
        self.assertTrue(panel.mic.isEnabled())
        self.assertFalse(panel.record.isEnabled())

    def test_tts_backend_selection_does_not_change_local_stt(self):
        panel = self.window.voice_panel
        self.window.policy.grant("speech.synthesize")
        self.window.policy.grant("speech.transcribe")
        for operation, available, preference, expected in (
                ("tts", True, True, "qwen"), ("tts", False, True, "piper"),
                ("tts", True, False, "piper"), ("stt", True, True, "piper")):
            panel.prefer_natural = preference
            with patch("companion.voice_panel.qwen_ready", return_value=available), patch("companion.voice_panel.SpeechJob") as factory:
                panel._launch(operation, "synthetic")
                self.assertEqual(factory.call_args.kwargs["backend"], expected)
                panel._workers.clear()
                panel._worker = None
