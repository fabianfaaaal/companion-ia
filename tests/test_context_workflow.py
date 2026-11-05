"""Real widgets with synthetic cards and simulated inference only."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PySide6.QtWidgets import QApplication
from companion.desktop import Window
from companion.session import build_messages
from companion.policy import State


class ContextWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "privacy.json"
        self.w = Window(self.path, auto_connect=False)
        self.w.models.addItem("synthetic-model")

    def tearDown(self):
        self.w._pending_chat_saves.clear()
        self.w.close()
        self.w.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def card(self, title="Demo", body="SYNTHETIC-CARD-DATA"):
        return self.w.context_cards.store.save_card("Proyectos", title, body, ["mi proyecto"])

    def test_off_cards_are_session_only_and_in_local_prompt(self):
        w = self.w
        self.card()
        w.input.setPlainText("Contame sobre mi proyecto")
        with patch.object(w, "_start") as start:
            w.send_message()
        messages = start.call_args.args[1]
        self.assertIn("SYNTHETIC-CARD-DATA", str(messages))
        self.assertIn("keywords", str(messages))
        self.assertFalse((self.path.parent / "memory.sqlite").exists())
        self.assertNotIn("SYNTHETIC-CARD-DATA", str(w._pending_remote_messages))

    def test_persistent_cards_require_explicit_remote_share(self):
        w = self.w
        w.memory_panel.set_enabled(True)
        self.card()
        w.input.setPlainText("Contame sobre mi proyecto")
        w.provider_panel.share_memory.setChecked(False)
        with patch.object(w, "_start"):
            w.send_message()
        self.assertIn("SYNTHETIC-CARD-DATA", str(w.last_messages))
        self.assertNotIn("SYNTHETIC-CARD-DATA", str(w._pending_remote_messages))
        w.provider_panel.share_memory.setChecked(True)
        w.input.setPlainText("Contame sobre mi proyecto")
        with patch.object(w, "_start"):
            w.send_message()
        self.assertIn("SYNTHETIC-CARD-DATA", str(w._pending_remote_messages))

    def test_ambiguous_cancel_preserves_draft_and_sends_nothing(self):
        w = self.w
        self.card("First", "FIRST-SYNTHETIC")
        self.card("Second", "SECOND-SYNTHETIC")
        w.input.setPlainText("Contame sobre mi proyecto")
        with patch("companion.desktop.QInputDialog.getItem", return_value=("", False)), patch.object(w, "_start") as start:
            w.send_message()
        start.assert_not_called()
        self.assertEqual(w.input.toPlainText(), "Contame sobre mi proyecto")

    def test_ambiguous_choice_includes_only_chosen_card(self):
        w = self.w
        self.card("First", "FIRST-SYNTHETIC")
        second = self.card("Second", "SECOND-SYNTHETIC")
        w.input.setPlainText("Contame sobre mi proyecto")
        with patch("companion.desktop.QInputDialog.getItem", return_value=("Second · " + second["id"][:8], True)), patch.object(w, "_start") as start:
            w.send_message()
        messages = start.call_args.args[1]
        self.assertIn("SECOND-SYNTHETIC", str(messages))
        self.assertNotIn("FIRST-SYNTHETIC", str(messages))

    def test_private_blocks_cards_and_setup_and_drops_ephemeral_cards(self):
        w = self.w
        self.card()
        w.toggle_private()
        self.assertEqual(w.policy.state, State.PRIVATE)
        with patch.object(w.engine_setup, "start") as setup:
            w.open_engine_setup()
            setup.assert_not_called()
        w.toggle_private()
        self.assertEqual(w.context_cards.store.list_cards(), [])

    def test_selected_assistant_text_remains_a_proposal(self):
        w = self.w
        with patch.object(w.context_cards, "propose_text") as propose:
            w.transcript.learning_requested.emit("Unverified synthetic assertion", "assistant")
        self.assertIn("no verificada", propose.call_args.kwargs["source_ref"])
        self.assertEqual(w.context_cards.store.list_cards(), [])

    def test_context_cards_cannot_bypass_budget_or_inject_archived_notes_when_off(self):
        note = dict(id="archive", title="Archived", body="NOT-ALLOWED")
        card = dict(id="session", title="Session card", body="Allowed temporary", matched_keywords=["demo"], version=2)
        messages = build_messages([], "hello", "Companion", "Charla", memories=[note], context_cards=[card])
        self.assertNotIn("NOT-ALLOWED", str(messages))
        self.assertIn("Allowed temporary", str(messages))
        self.assertIn('"version": 2', str(messages))

    def test_persistent_card_is_sent_once_with_declared_source(self):
        import json
        w = self.w
        w.memory_panel.set_enabled(True)
        card = self.card()
        w.input.setPlainText("Contame sobre mi proyecto")
        with patch.object(w, "_start") as start:
            w.send_message()
        messages = start.call_args.args[1]
        records = json.loads(messages[1]["content"].split("\n", 1)[1])
        self.assertEqual([record["id"] for record in records].count(card["id"]), 1)
        self.assertIn("source_ref", records[0])

    def test_cancelled_archive_switch_preserves_previous_locator_and_archive(self):
        from companion.storage_location import StorageLocation
        from PySide6.QtWidgets import QDialog
        w = self.w
        location = StorageLocation(self.path.parent / "technical")
        old = location.select(self.path.parent / "personal-old")
        w._storage_location = location
        target = self.path.parent / "personal-new"
        def choose(dialog):
            dialog.chosen_path = target
            return QDialog.DialogCode.Accepted
        with patch("companion.desktop.StorageLocationDialog.exec", choose), patch.object(w, "close", return_value=False):
            w.change_archive_location()
        self.assertEqual(location.resolve(), old)
        self.assertFalse(w._restart_requested)
        self.assertTrue(old.exists())

    def test_interrupted_restore_cancel_never_reactivates_private(self):
        from companion.desktop import prepare_archive_startup
        from companion.archive_transfer import ArchiveError
        self.w.toggle_private()
        with patch("companion.desktop.apply_pending_restore", side_effect=ArchiveError), self.assertRaises(ArchiveError):
            prepare_archive_startup(self.path.parent, lambda: False)
        from companion.policy import Policy
        self.assertEqual(Policy(self.path).state, State.PRIVATE)

    def test_interrupted_restore_requires_explicit_local_reactivation(self):
        from companion.desktop import prepare_archive_startup
        from companion.archive_transfer import ArchiveError
        from companion.policy import Policy
        self.w.toggle_private()
        with patch("companion.desktop.apply_pending_restore", side_effect=[ArchiveError(), "recovered"]) as restore:
            result = prepare_archive_startup(self.path.parent, lambda: True)
        self.assertEqual(result, "recovered")
        self.assertEqual(restore.call_count, 2)
        self.assertEqual(Policy(self.path).state, State.ACTIVE)
