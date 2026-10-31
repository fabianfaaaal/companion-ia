"""Manual-note storage tests use only synthetic data and temporary directories."""
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.memory_store import MemoryStore, MemoryStoreError
from companion.policy import Policy, Denied


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "uncreated" / "memory.sqlite"
        self.policy = Policy()
        self.policy.grant("memory.read")
        self.policy.grant("memory.write")
        self.store = MemoryStore(self.path, self.policy)

    def enable(self):
        self.policy.set_memory(True)

    def test_constructor_and_off_operations_do_no_disk_access(self):
        with patch("companion.memory_store.sqlite3.connect") as connect, patch.object(Path, "exists") as exists:
            store = MemoryStore(self.path, self.policy)
            for action in (lambda: store.save_note("title", "body"), store.list_notes,
                           lambda: store.search("body"), lambda: store.delete_note("synthetic")):
                with self.assertRaises(Denied):
                    action()
            connect.assert_not_called()
            exists.assert_not_called()
        self.assertFalse(self.path.parent.exists())

    def test_missing_archive_reads_and_delete_never_create_it(self):
        self.enable()
        self.assertEqual(self.store.search("synthetic"), [])
        self.assertEqual(self.store.list_notes(), [])
        self.assertEqual(self.store.search(""), [])
        self.assertFalse(self.store.delete_note("missing"))
        self.assertFalse(self.path.parent.exists())

    def test_persist_reopen_and_consent_reference(self):
        self.enable()
        note = self.store.save_note("Título sintético", "El colibrí azul vive en esta prueba.")
        reopened = MemoryStore(self.path, self.policy)
        self.assertEqual(reopened.list_notes(), [note])
        self.assertEqual(reopened.search("colibrí"), [note])
        self.assertEqual(note["schema_version"], 1)
        self.assertEqual(note["source"], "user_note")
        self.assertEqual(note["privacy_class"], "general")
        self.assertIsNotNone(datetime.fromisoformat(note["created_at"]).utcoffset())
        with closing(sqlite3.connect(self.path)) as connection, connection:
            consent = connection.execute("SELECT scope, action FROM consent WHERE id=?", (note["consent_id"],)).fetchone()
        self.assertEqual(consent, ("manual_general_notes", "user_saved_note"))

    def test_private_denies_before_disk_even_with_existing_data(self):
        self.enable()
        note = self.store.save_note("Title", "Synthetic body")
        self.policy.enter_private()
        with patch("companion.memory_store.sqlite3.connect") as connect:
            for action in (self.store.list_notes, lambda: self.store.search("Synthetic"),
                           lambda: self.store.save_note("Title", "body"), lambda: self.store.delete_note(note["id"])):
                with self.assertRaises(Denied):
                    action()
            connect.assert_not_called()

    def test_revocation_between_begin_and_publish_does_not_create_archive(self):
        self.enable()
        publish = self.policy.publish
        def revoked(lease, callback):
            self.policy.revoke("memory.write")
            return publish(lease, callback)
        with patch.object(self.policy, "publish", side_effect=revoked):
            with self.assertRaises(Denied):
                self.store.save_note("Synthetic", "never written")
        self.assertFalse(self.path.parent.exists())

    def test_disable_memory_blocks_existing_archive(self):
        self.enable()
        self.store.save_note("Synthetic", "body")
        self.policy.set_memory(False)
        with self.assertRaises(Denied):
            self.store.list_notes()
        self.policy.set_memory(True)
        self.assertEqual(len(self.store.list_notes()), 1)

    def test_delete_removes_fts_and_consent(self):
        self.enable()
        note = self.store.save_note("Synthetic", "uniqueSyntheticToken")
        self.assertTrue(self.store.delete_note(note["id"]))
        self.assertFalse(self.store.delete_note(note["id"]))
        self.assertEqual(self.store.search("uniqueSyntheticToken"), [])
        self.assertEqual(self.store.list_notes(), [])
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM notes_fts").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM consent").fetchone()[0], 0)

    def test_sql_and_fts_syntax_are_literal_not_executable(self):
        self.enable()
        note = self.store.save_note("Literal", "DROP TABLE notes synthetic")
        self.assertEqual(self.store.search('\"; DROP TABLE notes; --'), [note])
        self.assertEqual(self.store.search('NEAR(\"DROP\", \"TABLE\")'), [note])
        self.assertFalse(self.store.delete_note("' OR 1=1 --"))
        self.assertEqual(self.store.list_notes(), [note])

    def test_limits_and_no_unexpected_privacy_parameter(self):
        self.enable()
        invalid = (lambda: self.store.save_note("t" * 121, "b"),
                   lambda: self.store.save_note("t", "b" * 8001),
                   lambda: self.store.save_note("", "b"),
                   lambda: self.store.save_note("t", "\x00"),
                   lambda: self.store.search("x" * 4001),
                   lambda: self.store.search("x", limit=True),
                   lambda: self.store.list_notes(limit=101),
                   lambda: self.store.search("x", limit=0))
        for action in invalid:
            with self.assertRaises(ValueError):
                action()
        with self.assertRaises(TypeError):
            self.store.save_note("t", "b", privacy="intimate")
        self.assertFalse(self.path.parent.exists())

    def test_future_version_rejected_without_overwrite(self):
        self.enable()
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("PRAGMA user_version=2")
            connection.execute("CREATE TABLE future_data(value TEXT)")
            connection.execute("INSERT INTO future_data VALUES ('synthetic')")
        before = self.path.read_bytes()
        for action in (self.store.list_notes, lambda: self.store.save_note("t", "b"),
                       lambda: self.store.delete_note("id")):
            with self.assertRaises(MemoryStoreError):
                action()
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_file_is_not_replaced_and_error_hides_path(self):
        self.enable()
        self.path.parent.mkdir()
        self.path.write_bytes(b"synthetic not sqlite")
        with self.assertRaises(MemoryStoreError) as caught:
            self.store.save_note("t", "b")
        self.assertNotIn(str(self.path), str(caught.exception))
        self.assertEqual(self.path.read_bytes(), b"synthetic not sqlite")

    def test_large_query_and_limit_are_bounded(self):
        self.enable()
        for index in range(7):
            self.store.save_note(f"Synthetic {index}", "common")
        self.assertEqual(len(self.store.search("common", limit=2)), 2)
        self.assertEqual(len(self.store.list_notes(limit=3)), 3)
        self.assertEqual(self.store.search(" ".join("q" + str(i) for i in range(500))), [])


if __name__ == "__main__":
    unittest.main()
