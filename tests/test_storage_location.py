"""Synthetic archive location selection; no real user folders are read."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.storage_location import StorageLocation, StorageLocationError, IDENTITY


class StorageLocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.control = self.root / "control"
        self.location = StorageLocation(self.control)

    def tearDown(self):
        self.temp.cleanup()

    def test_first_start_defaults_separate_and_does_not_create(self):
        self.assertFalse(self.location.is_configured())
        self.assertEqual(self.location.resolve(), self.control / "archivo")
        self.assertFalse(self.control.exists())

    def test_legacy_files_preserve_existing_archive_even_invalid_latch(self):
        for name, value in (("memory.sqlite", b"synthetic old content"), ("preferences.json", b"broken"), ("privacy.json", b"broken")):
            with self.subTest(name=name):
                self.control.mkdir(exist_ok=True)
                target = self.control / name
                target.write_bytes(value)
                self.assertTrue(self.location.is_configured())
                self.assertEqual(self.location.resolve(), self.control)
                self.assertEqual(target.read_bytes(), value)
                target.unlink()

    def test_accept_new_folder_records_only_locator_and_identity(self):
        candidate = self.root / "personal" / "archive"
        selected = self.location.select(candidate)
        self.assertEqual(selected, candidate)
        self.assertTrue(self.location.is_configured())
        self.assertEqual(self.location.resolve(), candidate)
        self.assertEqual(json.loads(self.location.path.read_text()), {"version": 1, "directory": str(candidate)})
        self.assertEqual(json.loads((candidate / "archive-identity.json").read_text()), IDENTITY)
        self.assertEqual({path.name for path in candidate.iterdir()}, {"archive-identity.json"})
        self.assertEqual({path.name for path in self.control.iterdir()}, {"archive-location.json"})

    def test_default_accept_creates_own_subfolder(self):
        self.location.select(self.location.default_directory)
        self.assertEqual(self.location.resolve(), self.control / "archivo")

    def test_switch_preserves_old_archive_and_does_not_migrate(self):
        old = self.root / "old"
        new = self.root / "new"
        self.location.select(old)
        data = old / "synthetic-note.txt"
        data.write_text("SYNTHETIC-CONTENT")
        self.location.select(new)
        self.assertEqual(self.location.resolve(), new)
        self.assertEqual(data.read_text(), "SYNTHETIC-CONTENT")
        self.assertFalse((new / data.name).exists())

    def test_nonempty_foreign_folder_is_never_modified(self):
        candidate = self.root / "foreign"
        candidate.mkdir()
        (candidate / "unrelated.txt").write_text("SYNTHETIC-FOREIGN")
        with self.assertRaises(StorageLocationError):
            self.location.select(candidate)
        self.assertFalse(self.location.path.exists())
        self.assertEqual((candidate / "unrelated.txt").read_text(), "SYNTHETIC-FOREIGN")
        self.assertEqual(len(list(candidate.iterdir())), 1)

    def test_existing_valid_companion_latch_is_recognized(self):
        candidate = self.root / "existing"
        candidate.mkdir()
        (candidate / "privacy.json").write_text('{"private":true}')
        self.assertEqual(self.location.validate(candidate), candidate)
        self.assertFalse(self.location.path.exists())
        self.location.select(candidate)
        self.assertEqual((candidate / "privacy.json").read_text(), '{"private":true}')

    def test_existing_note_schema_is_recognized_but_arbitrary_sqlite_rejected(self):
        candidate = self.root / "existing"
        candidate.mkdir()
        with closing(sqlite3.connect(candidate / "memory.sqlite")) as connection, connection:
            connection.execute("CREATE TABLE notes(id TEXT)")
            connection.execute("CREATE TABLE consent(id TEXT)")
            connection.execute("PRAGMA user_version=1")
        self.assertEqual(self.location.validate(candidate), candidate)
        other = self.root / "foreign-db"
        other.mkdir()
        with closing(sqlite3.connect(other / "memory.sqlite")) as connection, connection:
            connection.execute("CREATE TABLE unrelated(id TEXT)")
        with self.assertRaises(StorageLocationError):
            self.location.validate(other)

    def test_corrupt_marker_does_not_qualify_by_other_files(self):
        candidate = self.root / "corrupt"
        candidate.mkdir()
        (candidate / "archive-identity.json").write_text("broken")
        (candidate / "privacy.json").write_text('{"private":false}')
        with self.assertRaises(StorageLocationError):
            self.location.validate(candidate)

    def test_malformed_locator_never_falls_back_to_empty_or_legacy(self):
        self.control.mkdir()
        (self.control / "privacy.json").write_text('{"private":false}')
        for value in ("broken", '{"version":99,"directory":"x"}', '{"version":true,"directory":"x"}', '{"version":1,"directory":"relative"}', '[]'):
            with self.subTest(value=value):
                self.location.path.write_text(value)
                with self.assertRaises(StorageLocationError):
                    self.location.resolve()
                with self.assertRaises(StorageLocationError):
                    self.location.is_configured()
        self.assertFalse((self.control / "archivo").exists())

    def test_configured_missing_drive_or_directory_is_visible_error(self):
        chosen = self.root / "removable" / "archive"
        self.control.mkdir()
        self.location.path.write_text(json.dumps({"version": 1, "directory": str(chosen)}))
        with self.assertRaises(StorageLocationError):
            self.location.resolve()
        self.assertFalse(chosen.exists())
        self.assertFalse((self.control / "archivo").exists())

    def test_forbidden_program_code_and_engines_overlap_rejected(self):
        forbidden = self.root / "program"
        location = StorageLocation(self.control, forbidden_roots=[forbidden])
        for candidate in (forbidden, forbidden / "my-data", self.root):
            with self.subTest(candidate=candidate):
                with self.assertRaises(StorageLocationError):
                    location.validate(candidate)
        allowed = self.root / "separate-personal"
        self.assertEqual(location.validate(allowed), allowed)
        for candidate in (self.control, self.control.parent):
            with self.assertRaises(StorageLocationError):
                self.location.validate(candidate)

    def test_relative_network_and_file_paths_rejected(self):
        for value in ("relative-folder", "\\\\synthetic-server\\share\\archive", "//synthetic-server/share/archive", "bad\0folder"):
            with self.subTest(value=value):
                with self.assertRaises(StorageLocationError):
                    self.location.validate(value)
        candidate = self.root / "file"
        candidate.write_text("SYNTHETIC")
        with self.assertRaises(StorageLocationError):
            self.location.validate(candidate)

    def test_symlink_and_junction_rejected_without_following(self):
        fake = self.root / "link"
        with patch.object(Path, "is_symlink", autospec=True, side_effect=lambda path: path == fake):
            with self.assertRaises(StorageLocationError):
                self.location.validate(fake / "archive")
        if hasattr(Path, "is_junction"):
            with patch.object(Path, "is_junction", autospec=True, side_effect=lambda path: path == fake):
                with self.assertRaises(StorageLocationError):
                    self.location.validate(fake / "archive")

    def test_broken_locator_link_never_falls_back_to_new_archive(self):
        locator = self.location.path
        with patch.object(Path, "is_symlink", autospec=True, side_effect=lambda path: path == locator):
            with self.assertRaises(StorageLocationError):
                self.location.resolve()
            with self.assertRaises(StorageLocationError):
                self.location.is_configured()
        self.assertFalse(self.control.exists())

    def test_failed_locator_write_preserves_previous_selection(self):
        old = self.root / "old"
        new = self.root / "new"
        self.location.select(old)
        original = self.location.path.read_bytes()
        original_writer = self.location._atomic_json
        def fail_locator(path, value):
            if path == self.location.path:
                raise OSError("synthetic write failure")
            original_writer(path, value)
        with patch.object(self.location, "_atomic_json", side_effect=fail_locator):
            with self.assertRaises(StorageLocationError):
                self.location.select(new)
        self.assertEqual(self.location.path.read_bytes(), original)
        self.assertEqual(self.location.resolve(), old)
        self.assertTrue(old.is_dir())
        self.assertFalse(list(self.control.glob(".companion-location-*.tmp")))


try:
    from PySide6.QtWidgets import QApplication, QDialog
    from companion.storage_dialog import StorageLocationDialog
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt not installed")
class StorageDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.location = StorageLocation(self.root / "control")
        self.dialog = StorageLocationDialog(self.location)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_accept_only_returns_validated_path_without_saving(self):
        self.dialog.accept()
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self.dialog.chosen_path, self.location.default_directory)
        self.assertFalse(self.location.control_dir.exists())

    def test_cancel_returns_none_and_creates_nothing(self):
        self.dialog.reject()
        self.assertIsNone(self.dialog.chosen_path)
        self.assertFalse(self.location.control_dir.exists())

    def test_picker_cancel_keeps_recommended_folder_and_invalid_folder_not_selected(self):
        with patch("companion.storage_dialog.QFileDialog.getExistingDirectory", return_value=""):
            self.dialog.choose_folder()
        self.assertEqual(self.dialog._candidate, self.location.default_directory)
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "file").write_text("SYNTHETIC")
        with patch("companion.storage_dialog.QFileDialog.getExistingDirectory", return_value=str(foreign)):
            self.dialog.choose_folder()
        self.assertEqual(self.dialog._candidate, self.location.default_directory)
        self.assertTrue(self.dialog.status.text())
        self.assertFalse(self.location.control_dir.exists())


if __name__ == "__main__":
    unittest.main()
