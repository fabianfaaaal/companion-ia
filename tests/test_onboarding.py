"""Synthetic startup checks. No physical audio or network."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PySide6.QtWidgets import QApplication, QDialog
from companion.desktop import Window
from companion.onboarding import SetupState
from companion.policy import State


class OnboardingTests(unittest.TestCase):
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

    def test_pending_setup_disables_memory_and_observation(self):
        w = self.window
        w.enable_preferences(require_setup=True)
        self.assertFalse(w.memory_panel.enabled)
        self.assertFalse(w.environment.timer.isActive())
        self.assertFalse(w.voice_panel.mic.isChecked())
        with patch.object(w.voice_panel, "speak") as speak, patch("companion.desktop.SetupDialog.exec", return_value=QDialog.DialogCode.Rejected):
            w.start_welcome()
            w.start_welcome()
            speak.assert_called_once()
        self.assertFalse(w._setup.complete())
        self.assertFalse((self.path.parent / "memory.sqlite").exists())

    def test_confirm_save_and_restart_without_repeating_setup(self):
        w = self.window
        w.enable_preferences(require_setup=True)
        with patch("companion.desktop.SetupDialog.exec", return_value=QDialog.DialogCode.Accepted):
            w.configure_startup()
        self.assertTrue(w._setup.complete())
        self.assertTrue(w.memory_panel.enabled)
        w.environment.timer.stop()
        with patch.object(w.voice_panel, "speak"), patch.object(w, "configure_startup") as setup:
            w.start_welcome()
            setup.assert_not_called()
        self.assertTrue(SetupState(w._setup.path, w.policy).complete())

    def test_no_memory_choice_survives_restart(self):
        w = self.window
        w.enable_preferences(require_setup=True)
        options = dict.fromkeys(w._preference_values, False)
        with patch("companion.desktop.SetupDialog.exec", return_value=QDialog.DialogCode.Accepted), patch("companion.desktop.SetupDialog.options", return_value=options):
            w.configure_startup()
        w.enable_preferences(require_setup=True)
        self.assertTrue(w._setup.complete())
        self.assertFalse(w.memory_panel.enabled)
        self.assertFalse(w.voice_panel.read.isChecked())

    def test_private_restart_never_greets_or_opens_setup(self):
        w = self.window
        w.toggle_private()
        w.enable_preferences(require_setup=True)
        with patch.object(w.voice_panel, "speak") as speak, patch.object(w, "configure_startup") as setup:
            w.start_welcome()
            speak.assert_not_called()
            setup.assert_not_called()
        self.assertEqual(w.policy.state, State.PRIVATE)
        self.assertFalse(w._setup.path.exists())

    def test_corrupt_marker_and_failed_save_do_not_enable_memory(self):
        w = self.window
        w._setup.path.write_text("broken", encoding="utf-8")
        w.enable_preferences(require_setup=True)
        self.assertFalse(w._setup.complete())
        with patch("companion.desktop.SetupDialog.exec", return_value=QDialog.DialogCode.Accepted), patch.object(w._preferences, "save", side_effect=OSError):
            w.configure_startup()
        self.assertFalse(w.memory_panel.enabled)
        self.assertFalse(w._setup.complete())

    def test_profile_write_failure_never_completes_initial_setup(self):
        w = self.window
        w.enable_preferences(require_setup=True)
        w._profile_flags_path.mkdir()
        with patch("companion.desktop.SetupDialog.exec", return_value=QDialog.DialogCode.Accepted):
            w.configure_startup()
        self.assertFalse(w._setup.complete())
        self.assertFalse(w.memory_panel.enabled)

    def test_completed_choices_restore_in_another_window(self):
        w = self.window
        w.enable_preferences(require_setup=True)
        options = dict.fromkeys(w._preference_values, False)
        with patch("companion.desktop.SetupDialog.exec", return_value=QDialog.DialogCode.Accepted), patch("companion.desktop.SetupDialog.options", return_value=options):
            w.configure_startup()
        other = Window(self.path, auto_connect=False)
        try:
            other.enable_preferences(require_setup=True)
            self.assertTrue(other._setup.complete())
            self.assertFalse(other.memory_panel.enabled)
            self.assertFalse(other.voice_panel.read.isChecked())
            self.assertFalse(other.environment.timer.isActive())
        finally:
            other.close()
            other.deleteLater()

    def test_private_entered_while_dialog_open_discards_confirmation(self):
        w = self.window
        w.enable_preferences(require_setup=True)
        def enter_private(*args):
            w.toggle_private()
            return QDialog.DialogCode.Accepted
        with patch("companion.desktop.SetupDialog.exec", side_effect=enter_private):
            w.configure_startup()
        self.assertEqual(w.policy.state, State.PRIVATE)
        self.assertFalse(w._setup.path.exists())
