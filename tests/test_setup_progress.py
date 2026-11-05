"""Measured step percentages, indeterminate activity and stale-job isolation."""
import io
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from PySide6.QtWidgets import QApplication
from companion.policy import Policy
from companion.setup_manager import SetupManager
from companion.setup_panel import SetupPanel


class SetupProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.policy=Policy(self.root/"privacy.json")
        self.panel=SetupPanel(self.policy,self.root)
        self.manager=SetupManager(self.policy,home=self.root/"runtime")
        with patch("companion.setup_manager.supported_platform",return_value=True):
            self.manager._begin()
        self.job=SimpleNamespace(manager=self.manager)
        self.panel._job=self.job
        self.panel._started_at=10.0
        self.panel._last_update_at=10.0

    def tearDown(self):
        self.panel._job=None
        self.panel.stop_and_wait()
        self.panel.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_measurable_step_then_unknown_install_does_not_guess_percentage(self):
        self.panel._step_progress(self.job,{"percent":42})
        self.assertEqual(self.panel.progress_bar.maximum(),100)
        self.assertEqual(self.panel.progress_bar.value(),42)
        self.panel._step_progress(self.job,{"percent":None})
        self.assertEqual(self.panel.progress_bar.maximum(),0)
        self.assertIsNone(self.panel._last_percent)
        self.assertEqual(self.panel.progress_bar.format(),"En curso…")

    def test_elapsed_and_quiet_clock_advance_without_advancing_percent(self):
        self.panel._step_progress(self.job,{"percent":37})
        self.panel._last_update_at=10.0
        with patch("companion.setup_panel.time.monotonic",return_value=75.0):
            self.panel._tick_activity()
        self.assertEqual(self.panel.progress_bar.value(),37)
        self.assertIn("01:05",self.panel.activity.text())
        self.assertIn("65 s",self.panel.activity.text())

    def test_success_marks_complete_and_failure_stops_animation(self):
        self.panel.activity_timer.start()
        self.panel._success(self.job,"Synthetic success")
        self.assertEqual(self.panel.progress_bar.value(),100)
        self.assertFalse(self.panel.activity_timer.isActive())
        self.panel._step_progress(self.job,{"percent":None})
        self.panel.activity_timer.start()
        self.panel._failure(self.job,"Synthetic failure")
        self.assertEqual(self.panel.progress_bar.maximum(),100)
        self.assertFalse(self.panel.activity_timer.isActive())
        self.assertIn("interrumpida",self.panel.progress_bar.format())

    def test_private_resume_cannot_publish_old_progress(self):
        self.panel._step_progress(self.job,{"percent":23})
        self.policy.enter_private()
        self.policy.resume(local_action=True)
        self.panel._step_progress(self.job,{"percent":99})
        self.assertEqual(self.panel.progress_bar.value(),23)

    def test_download_reports_actual_byte_ratio(self):
        states=[]
        self.manager.progress_state=states.append
        response=io.BytesIO(b"x"*10)
        response.status=200
        response.headers={"Content-Length":"10"}
        response.geturl=lambda:"https://www.python.org/synthetic.exe"
        opener=Mock();opener.open.return_value=response
        with patch("companion.setup_manager.build_opener",return_value=opener):
            self.manager._download("https://www.python.org/synthetic.exe",self.root/"synthetic.exe")
        self.assertEqual(states[-1]["percent"],100)

    def test_unknown_download_size_remains_indeterminate(self):
        states=[]
        self.manager.progress_state=states.append
        response=io.BytesIO(b"synthetic")
        response.status=200
        response.headers={}
        response.geturl=lambda:"https://www.python.org/synthetic.exe"
        opener=Mock();opener.open.return_value=response
        with patch("companion.setup_manager.build_opener",return_value=opener):
            self.manager._download("https://www.python.org/synthetic.exe",self.root/"synthetic.exe")
        self.assertIsNone(states[-1]["percent"])
