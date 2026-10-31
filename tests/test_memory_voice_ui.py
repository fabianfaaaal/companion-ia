"""Synthetic GUI tests: no real microphone, speaker, voice inference or user data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    from PySide6.QtWidgets import QApplication
    from companion.desktop import Window
    from companion.policy import State, Denied
    QT = True
except ImportError:
    QT = False


class SilentAudio:
    playing = False
    recording = False
    def cancel(self):
        pass


@unittest.skipUnless(QT, "Qt not installed")
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.w = Window(self.root / "privacy.json", auto_connect=False)
        self.w.voice_panel.audio = SilentAudio()
    def tearDown(self):
        self.w.voice_panel._workers.clear()
        self.w.voice_panel._worker = None
        self.w.close()
        self.w.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_notes_are_explicit_and_survive_reopening(self):
        panel = self.w.memory_panel
        self.assertFalse((self.root / "memory.sqlite").exists())
        panel.set_enabled(True)
        note = panel.store.save_note("Semilla", "Proyecto sintético sobre semillas.")
        self.assertEqual(panel.retrieve("semillas")[0]["id"], note["id"])
        self.w.history = [{"role": "user", "content": "synthetic session"}]
        panel.set_enabled(False)
        self.assertEqual(self.w.history, [])
        self.assertEqual(panel.retrieve("semillas"), [])
        self.assertTrue((self.root / "memory.sqlite").exists())
        panel.set_enabled(True)
        self.assertEqual(len(panel.store.list_notes()), 1)

    def test_function_and_reminder_answers_do_not_call_model(self):
        self.w.input.setPlainText("Podés crear un recordatorio para mañana?")
        with patch("companion.desktop.Worker.start") as start:
            self.w.send_message()
            start.assert_not_called()
        self.assertIn("No quedó ninguno creado", self.w.transcript.toPlainText())
        self.assertFalse((self.root / "memory.sqlite").exists())

    def test_dictation_only_fills_editor_and_does_not_save_or_send(self):
        panel = self.w.voice_panel
        self.w.policy.grant("speech.transcribe")
        panel._lease = self.w.policy.begin("speech.transcribe")
        with patch("companion.desktop.Worker.start") as start:
            panel._result(panel._job, {"op": "stt", "text": "mensaje sintético"})
            start.assert_not_called()
        self.assertEqual(self.w.input.toPlainText(), "mensaje sintético")
        self.assertEqual(self.w.history, [])
        self.assertFalse((self.root / "memory.sqlite").exists())

    def test_disabling_voice_discards_queued_and_late_audio(self):
        panel = self.w.voice_panel
        panel.read.setChecked(True)
        old = panel._job
        panel._queue.append(("synthetic samples", 16000))
        panel.read.setChecked(False)
        panel._result(old, {"op": "tts", "pcm": "invalid"})
        self.assertEqual(panel._queue, [])
        with self.assertRaises(Denied):
            self.w.policy.begin("audio.playback")

    def test_private_waits_for_all_voice_workers_and_clears_notes_context(self):
        panel = self.w.voice_panel
        jobs = [MagicMock(), MagicMock()]
        for job in jobs:
            job.wait.return_value = True
        panel._workers.update(jobs)
        self.w.memory_panel.set_enabled(True)
        self.w.history = [{"role": "assistant", "content": "derived synthetic note"}]
        self.w.toggle_private()
        self.assertEqual(self.w.policy.state, State.PRIVATE)
        for job in jobs:
            job.wait.assert_called_with(5000)
        self.assertEqual(self.w.history, [])
        self.assertFalse(self.w.memory_panel.enabled)

    def test_voice_timeout_prevents_private_confirmation(self):
        job = MagicMock()
        job.wait.return_value = False
        self.w.voice_panel._workers.add(job)
        self.w.toggle_private()
        self.assertEqual(self.w.policy.state, State.ERROR)
        self.assertFalse(self.w.send.isEnabled())

    def test_private_rejects_late_dictation(self):
        panel = self.w.voice_panel
        self.w.policy.grant("speech.transcribe")
        panel._lease = self.w.policy.begin("speech.transcribe")
        old = panel._job
        self.w.toggle_private()
        panel._result(old, {"op": "stt", "text": "late synthetic"})
        self.assertEqual(self.w.input.toPlainText(), "")

    def test_process_timeout_cannot_be_hidden_by_thread_completion(self):
        import subprocess
        from companion.voice_panel import SpeechJob
        job = SpeechJob(1, self.root, "tts", "synthetic")
        process = MagicMock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("synthetic engine", 5)
        job._process = process
        with self.assertRaises(RuntimeError):
            job._close_engine()
        self.assertIs(job._process, process)
        self.assertTrue(job.shutdown_failed)
        with self.assertRaises(RuntimeError):
            job.confirm_stopped()
        process.poll.return_value = 0
        process.wait.side_effect = None
        job.confirm_stopped()
        self.assertIsNone(job._process)

    def test_voice_ipc_keeps_content_out_of_process_arguments(self):
        import io
        import json
        from companion.voice_panel import SpeechJob
        process = MagicMock()
        process.stdin = io.BytesIO()
        process.stdout = io.BytesIO(b'{"ok":true,"op":"stt","text":"synthetic"}\n')
        process.poll.return_value = 0
        job = SpeechJob(1, self.root, "stt", None)
        request = {"op": "tts", "text": "synthetic private sample"}
        with patch("companion.voice_panel.subprocess.Popen", return_value=process) as create:
            result = job._request(request)
        args = create.call_args.args[0]
        self.assertNotIn(request["text"], " ".join(args))
        self.assertEqual(json.loads(process.stdin.getvalue()), request)
        self.assertTrue(result["ok"])
        job._close_engine()
