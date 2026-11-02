import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from companion.browser_chat import BrowserChatDialog, prepare_prompt
from companion.policy import Policy

class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_prompt_contains_current_message_without_invented_capabilities(self):
        prompt = prepare_prompt("Consulta sintética", "Vero")
        self.assertIn("Consulta sintética", prompt)
        self.assertIn("Vero", prompt)
        self.assertIn("No inventes recuerdos", prompt)
        for invalid in ("", "x" * 4001):
            with self.assertRaises(ValueError):
                prepare_prompt(invalid, "Amiga")

    def test_open_sends_no_prompt_in_url_and_copy_is_explicit(self):
        policy = Policy()
        with patch("companion.browser_chat.QDesktopServices.openUrl", return_value=True) as opened:
            dialog = BrowserChatDialog(policy, "SYNTHETIC", "Amiga")
            opened.assert_not_called()
            dialog.open_site()
            self.assertEqual(opened.call_args.args[0].toString(), "https://chat.deepseek.com/")
            dialog.site.setCurrentText("ChatGPT")
            dialog.open_site()
            self.assertEqual(opened.call_args.args[0].toString(), "https://chatgpt.com/")
            dialog.close()

    def test_private_denies_copy_launch_import_and_clears_draft(self):
        policy = Policy()
        dialog = BrowserChatDialog(policy, "Synthetic private", "Amiga")
        dialog.response.setPlainText("Synthetic answer")
        policy.enter_private()
        with patch("companion.browser_chat.QDesktopServices.openUrl") as opened, patch.object(dialog, "perform", wraps=dialog.perform):
            dialog.open_site()
            dialog.import_answer()
            self.assertEqual(dialog.answer, "")
            opened.assert_not_called()
        dialog.clear_private()
        self.assertEqual(dialog.prompt.toPlainText(), "")
        self.assertEqual(dialog.response.toPlainText(), "")

    def test_import_requires_explicit_nonempty_bounded_answer(self):
        dialog = BrowserChatDialog(Policy(), "Synthetic question", "Amiga")
        dialog.import_answer()
        self.assertEqual(dialog.answer, "")
        dialog.response.setPlainText("x" * 16001)
        dialog.import_answer()
        self.assertEqual(dialog.answer, "")
        dialog.response.setPlainText("Synthetic approved answer")
        dialog.import_answer()
        self.assertEqual(dialog.answer, "Synthetic approved answer")
        self.assertEqual(dialog.provider, "DeepSeek")
        dialog.close()
