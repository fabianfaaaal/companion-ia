"""Floating-device presentation only: no hardware, context or inference."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from companion.device_view import CompanionDevice
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt not installed")
class DeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.device = CompanionDevice()

    def tearDown(self):
        self.device.hide()
        self.device.deleteLater()
        self.app.processEvents()

    def test_device_is_optional_nonmodal_topmost_tool(self):
        self.assertFalse(self.device.isModal())
        flags = self.device.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(self.device.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertFalse(self.device.isVisible())

    def test_buttons_only_emit_actions_without_changing_state(self):
        events = []
        self.device.open_chat.connect(lambda: events.append("chat"))
        self.device.open_memory.connect(lambda: events.append("memory"))
        self.device.record_requested.connect(lambda: events.append("record"))
        self.device.private_requested.connect(lambda: events.append("private"))
        for button in (self.device.chat_button,self.device.memory_button,self.device.record_button,self.device.private_button):
            button.click()
        self.assertEqual(events,["chat","memory","record","private"])
        self.assertFalse(self.device._private)
        self.assertFalse(self.device._recording)

    def test_private_blocks_recording_and_memory_but_keeps_return_and_resume(self):
        self.device.set_status(private=True,recording=True,busy=True)
        self.assertFalse(self.device.record_button.isEnabled())
        self.assertFalse(self.device.memory_button.isEnabled())
        self.assertTrue(self.device.chat_button.isEnabled())
        self.assertTrue(self.device.private_button.isEnabled())
        self.assertEqual(self.device.private_button.text(),"Reactivar")
        self.assertIn("privado",self.device.status.text().lower())

    def test_recording_can_be_stopped_while_busy(self):
        self.device.set_status(recording=True,busy=True)
        self.assertTrue(self.device.record_button.isEnabled())
        self.assertEqual(self.device.record_button.text(),"Terminar")
        self.device.set_status(busy=True)
        self.assertFalse(self.device.record_button.isEnabled())

    def test_skin_does_not_change_global_application_style(self):
        original = self.app.styleSheet()
        self.device.skin.setCurrentText("Violeta")
        self.device.set_skin("unknown")
        self.assertEqual(self.app.styleSheet(),original)
        self.assertEqual(self.device.skin.currentText(),"Violeta")
        self.assertFalse(self.device._private)

    def test_close_returns_to_chat_without_application_exit(self):
        returned = []
        self.device.open_chat.connect(lambda: returned.append(True))
        self.device.show()
        self.app.processEvents()
        self.device.close()
        self.assertEqual(returned,[True])
        self.assertFalse(self.device.isVisible())

    def test_error_or_stopping_is_blocked_without_confirming_private(self):
        self.device.set_status(private=True,recording=True,busy=True,blocked=True)
        self.assertEqual(self.device.status.text(),"Bloqueado")
        self.assertFalse(self.device.record_button.isEnabled())
        self.assertFalse(self.device.memory_button.isEnabled())
        self.assertFalse(self.device.private_button.isEnabled())
        self.assertTrue(self.device.chat_button.isEnabled())

    def test_unprepared_or_unpermitted_microphone_stays_disabled_except_finish(self):
        self.device.set_status(can_record=False)
        self.assertFalse(self.device.record_button.isEnabled())
        self.device.set_status(recording=True,can_record=False)
        self.assertTrue(self.device.record_button.isEnabled())
        self.assertEqual(self.device.record_button.text(),"Terminar")
