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

    def test_enter_sends_and_keypad_enter_sends(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.window.models.addItem("synthetic-model")
        self.window._refresh()
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.window.input.setPlainText("Synthetic greeting")
            with patch.object(self.window, "send_message") as send:
                QTest.keyClick(self.window.input, key)
                send.assert_called_once()
            self.assertEqual(self.window.input.toPlainText(), "Synthetic greeting")

    def test_shift_enter_inserts_newline_without_sending(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.window.input.setPlainText("First line")
        self.window.input.moveCursor(self.window.input.textCursor().MoveOperation.End)
        with patch.object(self.window, "send_message") as send:
            QTest.keyClick(self.window.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
            QTest.keyClicks(self.window.input, "Second line")
            send.assert_not_called()
        self.assertEqual(self.window.input.toPlainText(), "First line\nSecond line")

    def test_enter_respects_disabled_send(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.assertFalse(self.window.send.isEnabled())
        with patch.object(self.window, "send_message") as send:
            QTest.keyClick(self.window.input, Qt.Key.Key_Return)
            send.assert_not_called()

    def test_response_time_units(self):
        from companion.desktop import format_response_time
        for duration, label in [(0.125, "125 ms"), (1.25, "1.25 s"),
                                (61.25, "1 min 01 s"), (3661, "1 h 01 min 01 s")]:
            self.assertEqual(format_response_time(duration), label)

    def test_response_label_measures_send_to_display_not_worker_duration(self):
        w = self.window
        w.models.addItem("synthetic-model")
        w.input.setPlainText("Escribí un saludo breve.")
        with patch("companion.desktop.time.perf_counter", return_value=10), patch.object(w, "_start"):
            w.send_message()
        self.assertEqual(w.response_time.text(), "Respuesta: esperando…")
        with patch("companion.desktop.time.perf_counter", return_value=10.125):
            w._apply_result({"answer": "Synthetic response", "elapsed": 0.001, "first": 0.001, "metrics": {}})
        self.assertIn("Synthetic response", w.transcript.toPlainText())
        self.assertEqual(w.response_time.text(), "Respuesta: 125 ms")
        self.assertIsNone(w._response_started)

    def test_cancel_failure_and_private_do_not_report_success_latency(self):
        w = self.window
        w._response_started = 10
        w.cancel_response()
        self.assertEqual(w.response_time.text(), "Respuesta: cancelada")
        w._response_started = 20
        w._lease = w.policy.begin("local.request")
        w._failure(w._job, "Synthetic failure")
        self.assertEqual(w.response_time.text(), "Respuesta: falló")
        w.toggle_private()
        self.assertEqual(w.response_time.text(), "Respuesta: —")
