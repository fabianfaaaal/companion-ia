"""Synthetic conversation persistence: no user chats, API calls or UI."""
from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.conversation_store import ConversationStore, MAX_MESSAGE_CHARS
from companion.memory_store import MemoryStore, MemoryStoreError
from companion.policy import Policy, Denied


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "memory.sqlite"
        self.policy = Policy()
        for operation in ("conversation.read", "conversation.write", "memory.read", "memory.write"):
            self.policy.grant(operation)
        self.store = ConversationStore(self.path, self.policy)

    def tearDown(self):
        self.temp.cleanup()

    def enable(self):
        self.policy.set_memory(True)

    def test_off_and_private_cannot_create_or_read_archive(self):
        self.assertFalse(self.path.exists())
        with self.assertRaises(Denied):
            self.store.create()
        with self.assertRaises(Denied):
            self.store.list_conversations()
        self.assertFalse(self.path.exists())
        self.enable()
        self.policy.enter_private()
        with self.assertRaises(Denied):
            self.store.create()
        self.assertFalse(self.path.exists())

    def test_round_trip_unicode_literal_content_and_message_order(self):
        self.enable()
        conversation = self.store.create("Ensayo sintético 🧪")
        first = self.store.append(conversation["id"], "user", "<b>hola</b> 😊", "Vos")
        second = self.store.append(conversation["id"], "assistant", "¡Hola!\nSegunda línea.", "Amiga", "Ollama local")
        third = self.store.append(conversation["id"], "notice", "Aviso sintético", "Programa")
        reopened = ConversationStore(self.path, self.policy)
        messages = reopened.messages(conversation["id"])
        self.assertEqual([item["id"] for item in messages], [first["id"], second["id"], third["id"]])
        self.assertEqual(messages[0]["content"], "<b>hola</b> 😊")
        self.assertEqual(messages[1]["provider"], "Ollama local")
        self.assertEqual(reopened.list_conversations()[0]["title"], "Ensayo sintético 🧪")

    def test_rename_and_delete_cascade_preserve_other_conversations_and_notes(self):
        self.enable()
        note = MemoryStore(self.path, self.policy).save_note("Synthetic note", "synthetic magnolia")
        one = self.store.create("First synthetic")
        two = self.store.create("Second synthetic")
        self.store.append(one["id"], "user", "First text")
        self.store.append(two["id"], "assistant", "Second text")
        self.assertTrue(self.store.rename(one["id"], "Renamed synthetic"))
        self.assertEqual(self.store.list_conversations()[0]["id"], one["id"])
        self.assertTrue(self.store.delete(one["id"]))
        self.assertFalse(self.store.delete(one["id"]))
        self.assertEqual(self.store.messages(one["id"]), [])
        self.assertEqual(len(self.store.messages(two["id"])), 1)
        self.assertEqual(MemoryStore(self.path, self.policy).search("magnolia")[0]["id"], note["id"])
        with closing(sqlite3.connect(self.path)) as database, database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM chat_messages WHERE conversation_id=?", (one["id"],)).fetchone()[0], 0)
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_reads_and_deletions_of_missing_archive_do_not_create_it(self):
        self.enable()
        self.assertEqual(self.store.list_conversations(), [])
        self.assertEqual(self.store.messages("missing"), [])
        self.assertFalse(self.store.rename("missing", "Synthetic title"))
        self.assertFalse(self.store.delete("missing"))
        with self.assertRaises(MemoryStoreError):
            self.store.append("missing", "user", "Synthetic text")
        self.assertFalse(self.path.exists())

    def test_validation_and_message_quota_are_atomic(self):
        self.enable()
        conversation = self.store.create()
        for role, text in (("system", "not allowed"), ([], "invalid role"), ("user", ""), ("user", "x" * (MAX_MESSAGE_CHARS + 1)), ("assistant", "x\x00y")):
            with self.assertRaises(ValueError):
                self.store.append(conversation["id"], role, text)
        self.assertEqual(self.store.messages(conversation["id"]), [])
        with patch("companion.conversation_store.MAX_MESSAGES", 1):
            self.store.append(conversation["id"], "user", "Accepted synthetic")
            with self.assertRaises(MemoryStoreError):
                self.store.append(conversation["id"], "assistant", "Rejected synthetic")
        self.assertEqual(len(self.store.messages(conversation["id"])), 1)

    def test_global_text_quota_counts_utf8_and_allows_deletion(self):
        self.enable()
        conversation = self.store.create("A")
        with patch("companion.conversation_store.MAX_TEXT_BYTES", 6):
            self.store.append(conversation["id"], "user", "😊")  # 4 bytes + title 1
            with self.assertRaises(MemoryStoreError):
                self.store.append(conversation["id"], "assistant", "é")
            with self.assertRaises(MemoryStoreError):
                self.store.rename(conversation["id"], "AAA")
            self.assertEqual(self.store.list_conversations()[0]["title"], "A")
            self.assertTrue(self.store.delete(conversation["id"]))

    def test_conversation_limit_and_read_limit(self):
        self.enable()
        with patch("companion.conversation_store.MAX_CONVERSATIONS", 2):
            self.store.create("One")
            self.store.create("Two")
            with self.assertRaises(MemoryStoreError):
                self.store.create("Three")
            self.assertEqual(len(self.store.list_conversations(2)), 2)
        for limit in (0, 501, True):
            with self.assertRaises(ValueError):
                self.store.list_conversations(limit)

    def test_future_component_version_is_rejected_without_writing(self):
        self.enable()
        self.store.create()
        with closing(sqlite3.connect(self.path)) as database, database:
            database.execute("UPDATE component_versions SET version=2 WHERE name='conversations'")
        with self.assertRaises(MemoryStoreError):
            self.store.list_conversations()
        with self.assertRaises(MemoryStoreError):
            self.store.create("Must not appear")

    def test_revocation_before_publication_blocks_late_write(self):
        self.enable()
        publish = self.policy.publish
        revoked = False
        def revoke_then_publish(lease, effect):
            nonlocal revoked
            if not revoked and lease.operation == "conversation.write":
                revoked = True
                self.policy.set_memory(False)
            return publish(lease, effect)
        with patch.object(self.policy, "publish", side_effect=revoke_then_publish):
            with self.assertRaises(Denied):
                self.store.create("Late synthetic")
        self.assertFalse(self.path.exists())

    def test_listing_note_archive_does_not_initialize_chat_schema(self):
        self.enable()
        MemoryStore(self.path, self.policy).save_note("Synthetic note", "only a note")
        before = self.path.read_bytes()
        self.assertEqual(self.store.list_conversations(), [])
        self.assertEqual(self.path.read_bytes(), before)
        with closing(sqlite3.connect(self.path)) as database:
            self.assertIsNone(database.execute("SELECT name FROM sqlite_master WHERE name='chat_conversations'").fetchone())
