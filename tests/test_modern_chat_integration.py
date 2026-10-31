"""Chat persistence and private isolation with synthetic content only."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from PySide6.QtWidgets import QApplication
from companion.desktop import Window
from companion.policy import State
from companion.ollama_client import ChatChunk
from companion.privacy_profiles import PROFILE_PRESETS


class ModernChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"privacy.json"
        self.w=Window(self.path,auto_connect=False)
    def tearDown(self):
        self.w._pending_chat_saves.clear()
        self.w.close();self.w.deleteLater();self.app.processEvents();self.temp.cleanup()

    def test_saved_bubbles_reopen_without_duplicate_messages(self):
        w=self.w;w.memory_panel.set_enabled(True)
        w._append("Synthetic hello 😊",role="user",label="Vos")
        w._append("Synthetic reply ✨",role="assistant",label="Amiga")
        identifier=w._current_conversation
        self.assertEqual(len(w.conversations.messages(identifier)),2)
        w.new_conversation();w.open_conversation(identifier)
        self.assertIn("Synthetic hello",w.transcript.toPlainText())
        self.assertEqual(len(w.conversations.messages(identifier)),2)
        self.assertEqual([m["role"] for m in w.history],["user","assistant"])

    def test_off_is_session_only_with_reopenable_session_sidebar(self):
        w=self.w
        w._append("Volatile synthetic",role="user",label="Vos")
        identifier=w._current_conversation
        w.new_conversation();w.open_conversation(identifier)
        self.assertIn("Volatile synthetic",w.transcript.toPlainText())
        self.assertFalse((self.path.parent/"memory.sqlite").exists())

    def test_private_local_reply_uses_no_archive_context_and_writes_nothing(self):
        w=self.w;w.memory_panel.set_enabled(True)
        w._append("PUBLIC-SYNTHETIC-SECRET",role="user",label="Vos")
        database=self.path.parent/"memory.sqlite"
        before=database.read_bytes()
        w.models.addItem("synthetic-local")
        w.toggle_private()
        self.assertTrue(w.input.isEnabled())
        w.input.setPlainText("Private synthetic message")
        with patch("companion.desktop.InteractionWorker.start"):
            w.send_message()
        worker=w._worker
        seen=[]
        def stream(model,messages,cancel,**kwargs):
            seen.extend(messages)
            yield ChatChunk("Private synthetic reply",True,{})
        with patch.object(worker.client,"stream_chat",stream):
            worker.run()
        w._finished(worker)
        self.assertIn("Private synthetic reply",w.transcript.toPlainText())
        self.assertNotIn("PUBLIC-SYNTHETIC-SECRET",str(seen))
        self.assertIsNone(worker.route)
        self.assertEqual(database.read_bytes(),before)
        self.assertEqual(w.sidebar.listing.count(),0)
        w.toggle_private()
        self.assertEqual(w.history,[])
        self.assertNotIn("Private synthetic",w.transcript.toPlainText())

    def test_profile_cannot_override_private(self):
        self.w.toggle_private()
        self.w.apply_privacy_profile(PROFILE_PRESETS["Completo"])
        self.assertEqual(self.w.policy.state,State.PRIVATE)
        self.assertFalse(self.w.memory_panel.enabled)
        self.assertFalse(self.w.environment.timer.isActive())
        self.assertFalse(self.w.provider_panel.allow.isChecked())

    def test_applied_profile_survives_reload_without_granting_api_costs(self):
        flags=dict(PROFILE_PRESETS["Concentrado"])
        self.w.apply_privacy_profile(flags)
        self.assertTrue(self.w.memory_panel.enabled)
        self.assertFalse(self.w.voice_panel.mic.isChecked())
        self.assertFalse(self.w.provider_panel.allow.isChecked())
        self.w.memory_panel.set_enabled(False)
        self.w._restore_active_profile()
        self.assertTrue(self.w.memory_panel.enabled)
        self.assertFalse(self.w.environment.timer.isActive())

    def test_context_change_starts_clean_chat_but_keeps_saved_conversation(self):
        w=self.w;w.memory_panel.set_enabled(True)
        w._append("Synthetic archived",role="user",label="Vos")
        identifier=w._current_conversation
        w._discard_derived_context()
        self.assertIsNone(w._current_conversation)
        self.assertEqual(w.transcript.toPlainText(),"")
        self.assertEqual(len(w.conversations.messages(identifier)),1)

    def test_renamed_temporary_chat_keeps_title_after_more_messages(self):
        w=self.w
        w._append("First synthetic",role="user",label="Vos")
        identifier=w._current_conversation
        with patch("companion.desktop.QInputDialog.getText",return_value=("Chosen title",True)):
            w.rename_conversation(identifier)
        w._append("Second synthetic",role="assistant",label="Amiga")
        self.assertEqual(w._volatile_conversations[identifier]["title"],"Chosen title")

    def test_failed_save_stays_visible_and_retries_in_order_without_duplicates(self):
        from companion.memory_store import MemoryStoreError
        w=self.w;w.memory_panel.set_enabled(True)
        with patch.object(w.conversations,"append",side_effect=MemoryStoreError("synthetic write failure")):
            w._append("Pending user",role="user",label="Vos")
            identifier=w._current_conversation
            w._append("Pending assistant",role="assistant",label="Amiga")
            self.assertEqual(len(w._pending_chat_saves[identifier]),2)
            self.assertIn("sin guardar",w.saving_badge.text())
            w.new_conversation()
            w.open_conversation(identifier)
            self.assertIn("Pending user",w.transcript.toPlainText())
        w.retry_chat_save()
        self.assertEqual([item["content"] for item in w.conversations.messages(identifier)],["Pending user","Pending assistant"])
        w.retry_chat_save()
        self.assertEqual(len(w.conversations.messages(identifier)),2)
        self.assertFalse(w._pending_chat_saves)
        self.assertIn("Guardado local activo",w.saving_badge.text())

    def test_retry_after_successful_commit_does_not_duplicate_a_message(self):
        from companion.memory_store import MemoryStoreError
        w=self.w;w.memory_panel.set_enabled(True)
        original=w.conversations.append
        def commit_then_fail(*args,**kwargs):
            original(*args,**kwargs)
            raise MemoryStoreError("synthetic lost acknowledgement")
        with patch.object(w.conversations,"append",side_effect=commit_then_fail):
            w._append("Committed once",role="user",label="Vos")
        identifier=w._current_conversation
        self.assertTrue(w._pending_chat_saves)
        w.retry_chat_save()
        self.assertEqual(len(w.conversations.messages(identifier)),1)
        self.assertFalse(w._pending_chat_saves)

    def test_new_conversation_after_failed_creation_keeps_recoverable_draft(self):
        from companion.memory_store import MemoryStoreError
        w=self.w;w.memory_panel.set_enabled(True)
        with patch.object(w.conversations,"create",side_effect=MemoryStoreError("synthetic quota")):
            w._append("Draft survives",role="user",label="Vos")
            draft=w._draft_chat_key
            self.assertIn(draft,w._failed_chat_views)
            w.new_conversation()
            w.open_conversation(draft)
            self.assertIn("Draft survives",w.transcript.toPlainText())
            with patch("companion.desktop.QInputDialog.getText",return_value=("Draft name",True)):
                w.rename_conversation(draft)
        w.retry_chat_save()
        self.assertEqual(w.conversations.list_conversations()[0]["title"],"Draft name")
        self.assertFalse(w._pending_chat_saves)

    def test_deleted_open_draft_does_not_reappear_in_the_next_saved_chat(self):
        from companion.memory_store import MemoryStoreError
        from PySide6.QtWidgets import QMessageBox
        w=self.w;w.memory_panel.set_enabled(True)
        with patch.object(w.conversations,"create",side_effect=MemoryStoreError("synthetic quota")):
            w._append("Deleted draft text",role="user",label="Vos")
            draft=w._draft_chat_key
            w.open_conversation(draft)
            with patch("companion.desktop.QMessageBox.question",return_value=QMessageBox.StandardButton.Yes):
                w.delete_conversation(draft)
            self.assertEqual(w.transcript.toPlainText(),"")
            self.assertEqual(w.history,[])
            self.assertNotIn(draft,w._pending_chat_saves)
        w._append("New clean message",role="user",label="Vos")
        messages=w.conversations.messages(w._current_conversation)
        self.assertEqual([item["content"] for item in messages],["New clean message"])
