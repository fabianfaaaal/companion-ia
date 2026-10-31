"""Desktop policy integration, using synthetic content and no live inference."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    from PySide6.QtWidgets import QApplication
    from companion.desktop import Window
    from companion.policy import State
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "Optional Qt desktop dependency not installed")
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "privacy.json"
        self.window = Window(self.path, auto_connect=False)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def test_private_erases_session_and_rejects_late_result(self):
        w = self.window
        w.history = [{"role": "user", "content": "synthetic secret"}]
        w.last_messages = list(w.history)
        w._append("synthetic secret")
        w._lease = w.policy.begin("local.request")
        old_job = w._job
        w.toggle_private()
        w._result(old_job, {"answer": "late synthetic", "elapsed": 1, "first": 1, "metrics": {}})
        self.assertEqual(w.policy.state, State.PRIVATE)
        self.assertEqual(w.history, [])
        self.assertEqual(w.last_messages, [])
        self.assertEqual(w.transcript.toPlainText(), "")
        self.assertNotIn("secret", self.path.read_text())
        self.assertFalse(w.input.isEnabled())

    def test_restart_private_does_not_start_model_discovery(self):
        self.window.toggle_private()
        with patch("companion.desktop.Worker.start") as start:
            other = Window(self.path, auto_connect=True)
            self.app.processEvents()
            other.detect_models()
            self.assertEqual(other.policy.state, State.PRIVATE)
            start.assert_not_called()
            other.close()
            other.deleteLater()

    def test_html_is_displayed_as_literal_text(self):
        text = '<img src="file:///nonexistent-synthetic">hello'
        self.window._append(text)
        self.assertIn(text, self.window.transcript.toPlainText())

    def test_reactivation_is_local_and_does_not_restore_session(self):
        self.window.toggle_private()
        self.window.toggle_private()
        self.assertEqual(self.window.policy.state, State.ACTIVE)
        self.assertEqual(self.window.history, [])
        self.assertEqual(self.window.models.count(), 0)

    def test_private_waits_for_worker_termination(self):
        from unittest.mock import MagicMock
        worker = MagicMock()
        worker.wait.return_value = True
        self.window._worker = worker
        self.window.toggle_private()
        worker.cancel.assert_called_once()
        worker.wait.assert_called_once_with(5000)
        self.assertEqual(self.window.policy.state, State.PRIVATE)
        self.window._worker = None

    def test_private_timeout_stays_blocked_in_error(self):
        from unittest.mock import MagicMock
        worker = MagicMock()
        worker.wait.return_value = False
        self.window._worker = worker
        self.window.toggle_private()
        self.assertEqual(self.window.policy.state, State.ERROR)
        self.assertFalse(self.window.send.isEnabled())
        self.assertTrue(self.path.exists())
        self.window._worker = None
