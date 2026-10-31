"""Synthetic offline checks for presentation and navigation, not hardware."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from companion.chat_view import ChatView, ConversationSidebar, EmojiButton
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt not installed")
class ChatViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = ChatView()
        self.view.resize(650, 350)
        self.view.show()
        self.app.processEvents()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()

    def test_plain_text_and_emojis_are_not_html(self):
        text = '<img src="https://example.invalid/private.png"> 😊 <b>literal</b>'
        self.view.append_message("user", text, "Vos")
        self.view.append_message("assistant", "Respuesta sintética 💚", "Amiga")
        self.app.processEvents()
        self.assertIn(text, self.view.toPlainText())
        self.assertEqual(self.view._bubbles[0][0].body.toPlainText(), text)
        self.assertTrue(self.view._bubbles[0][0].body.isReadOnly())
        self.assertLess(self.view._bubbles[1][0].x(), self.view._bubbles[0][0].x())

    def test_long_unbroken_text_wraps_without_horizontal_scroll(self):
        self.view.append_message("assistant", "https://example.invalid/" + "a" * 1200)
        self.app.processEvents()
        bubble = self.view._bubbles[0][0]
        self.assertLess(bubble.width(), self.view.viewport().width())
        self.assertEqual(self.view.horizontalScrollBar().maximum(), 0)
        self.assertGreater(bubble.body.height(), 30)

    def test_clear_discards_visible_and_plain_messages(self):
        self.view.append_message("assistant", "Synthetic archived content")
        self.view.clear()
        self.app.processEvents()
        self.assertEqual(self.view.toPlainText(), "")
        self.assertEqual(len(self.view._bubbles), 0)
        self.assertTrue(self.view.placeholder.isVisible())

    def test_new_message_does_not_pull_reader_from_older_messages(self):
        for index in range(20):
            self.view.append_message("assistant", ("Synthetic message " + str(index) + " ") * 12)
        self.app.processEvents()
        self.app.processEvents()
        bar = self.view.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(0)
        self.view.append_message("assistant", "New synthetic message")
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(bar.value(), 0)

    def test_sidebar_programmatic_refresh_never_selects_conversation(self):
        sidebar = ConversationSidebar()
        selections = []
        sidebar.selected.connect(selections.append)
        sidebar.set_items([dict(id="a", title="Synthetic A", updated_at="2026-10-31T12:00:00+00:00"),
                           dict(id="b", title="Synthetic B", updated_at="2026-10-31T13:00:00+00:00")])
        sidebar.set_current("b")
        self.assertEqual(selections, [])
        sidebar.listing.setCurrentRow(0)
        self.assertEqual(selections, ["a"])
        sidebar.deleteLater()

    def test_emoji_picker_emits_unicode_only_on_choice(self):
        button = EmojiButton()
        selected = []
        button.emoji_selected.connect(selected.append)
        self.assertEqual(selected, [])
        button._choose("😊")
        self.assertEqual(selected, ["😊"])
        button.deleteLater()
