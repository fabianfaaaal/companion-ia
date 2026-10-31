"""Privacy-profile metadata tests with synthetic settings only."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.policy import Policy, Denied
try:
    from companion.privacy_profiles import (
        PrivacyProfileStore, PrivacyProfilesDialog, ProfileError, PROFILE_FLAGS,
        PROFILE_PRESETS, validate_flags,
    )
    from PySide6.QtWidgets import QApplication
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt not installed")
class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "profiles.json"
        self.policy = Policy()
        self.policy.grant("profiles.read")
        self.policy.grant("profiles.write")
        self.store = PrivacyProfileStore(self.path, self.policy)

    def tearDown(self):
        self.temp.cleanup()

    def test_construct_and_presets_do_not_write_any_file(self):
        self.assertFalse(self.path.exists())
        records = self.store.list_profiles()
        self.assertEqual([record["name"] for record in records], ["Completo", "Concentrado", "Solo chat"])
        self.assertFalse(self.path.exists())
        self.assertTrue(all(self.store.get("Completo").values()))
        self.assertFalse(any(self.store.get("Solo chat").values()))

    def test_save_roundtrip_is_metadata_only_and_delete_does_not_apply(self):
        flags = PROFILE_PRESETS["Concentrado"].copy()
        self.store.save("Synthetic focus", flags)
        self.assertEqual(self.store.get("Synthetic focus"), flags)
        self.assertEqual(PrivacyProfileStore(self.path,self.policy).get("Synthetic focus"), flags)
        data = json.loads(self.path.read_text())
        self.assertEqual(set(data), {"version","profiles"})
        self.assertTrue(self.store.delete("Synthetic focus"))
        with self.assertRaises(ProfileError):
            self.store.get("Synthetic focus")
        self.policy.grant("memory.read")
        with self.assertRaises(Denied):
            self.policy.begin("memory.read")

    def test_unknown_keys_non_bool_and_invalid_names_are_rejected(self):
        flags = PROFILE_PRESETS["Completo"].copy()
        for invalid in (dict(flags, api_key=True), {"memory":True}, dict(flags, voice=1)):
            with self.assertRaises(ProfileError):
                self.store.save("Synthetic", invalid)
        for name in ("", "a"*41, "bad\nname"):
            with self.assertRaises(ProfileError):
                self.store.save(name, flags)
        self.assertFalse(self.path.exists())

    def test_twenty_profile_limit_allows_existing_override(self):
        for index in range(17):
            self.store.save("Synthetic "+str(index), PROFILE_PRESETS["Completo"])
        self.assertEqual(len(self.store.list_profiles()),20)
        with self.assertRaises(ProfileError):
            self.store.save("Synthetic overflow", PROFILE_PRESETS["Completo"])
        self.store.save("Synthetic 0", PROFILE_PRESETS["Solo chat"])
        self.assertFalse(any(self.store.get("Synthetic 0").values()))

    def test_private_blocks_reads_and_writes_before_disk_access(self):
        self.policy.enter_private()
        with patch.object(self.store, "_load") as read:
            for action in (self.store.list_profiles, lambda:self.store.get("Completo"),
                           lambda:self.store.save("Synthetic", PROFILE_PRESETS["Completo"]),
                           lambda:self.store.delete("Completo")):
                with self.assertRaises(Denied):
                    action()
            read.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_boolean_schema_version_is_not_accepted_as_integer(self):
        self.path.write_text(json.dumps({"version":True,"profiles":[]}))
        with self.assertRaises(ProfileError):
            self.store.list_profiles()

    def test_corrupt_file_never_silently_returns_complete_profile(self):
        self.path.write_text('{"version":1,"profiles":[{"name":"bad","flags":{"memory":true}}]}')
        with self.assertRaises(ProfileError):
            self.store.list_profiles()


@unittest.skipUnless(QT, "Qt not installed")
class ProfileDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "profiles.json"
        self.policy = Policy()

    def tearDown(self):
        self.app.processEvents()
        self.temp.cleanup()

    def test_opening_preserves_current_flags_without_apply_or_capture(self):
        flags = PROFILE_PRESETS["Solo chat"].copy()
        flags["voice"] = True
        dialog = PrivacyProfilesDialog(self.policy,self.path,flags)
        self.assertEqual(dialog.flags(),flags)
        self.assertIsNone(dialog.selected_flags)
        self.assertFalse(self.path.exists())
        self.policy.grant("memory.read")
        with self.assertRaises(Denied):
            self.policy.begin("memory.read")
        self.assertNotIn("api",dialog.flags())
        dialog.close();dialog.deleteLater()

    def test_explicit_apply_only_returns_boolean_flags(self):
        dialog = PrivacyProfilesDialog(self.policy,self.path,PROFILE_PRESETS["Completo"])
        applied = []
        dialog.applied.connect(applied.append)
        dialog.set_flags(PROFILE_PRESETS["Concentrado"])
        self.assertEqual(applied,[])
        dialog.apply_profile()
        self.assertEqual(dialog.selected_flags,PROFILE_PRESETS["Concentrado"])
        self.assertEqual(applied,[PROFILE_PRESETS["Concentrado"]])
        self.policy.grant("memory.read")
        with self.assertRaises(Denied):
            self.policy.begin("memory.read")
        self.assertFalse(self.path.exists())
        dialog.deleteLater()

    def test_private_dialog_does_not_read_profiles_and_cannot_apply(self):
        self.policy.enter_private()
        with patch.object(PrivacyProfileStore,"_load") as read:
            dialog = PrivacyProfilesDialog(self.policy,self.path,PROFILE_PRESETS["Completo"])
            dialog.apply_profile()
            read.assert_not_called()
        self.assertIsNone(dialog.selected_flags)
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertFalse(any(dialog.flags().values()))
        dialog.close();dialog.deleteLater()

    def test_entering_private_clears_selection_and_disables_controls(self):
        dialog = PrivacyProfilesDialog(self.policy,self.path,PROFILE_PRESETS["Completo"])
        self.policy.enter_private()
        dialog.refresh_state()
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertFalse(dialog.name.isEnabled())
        self.assertFalse(any(dialog.flags().values()))
        self.assertEqual(dialog.profile.count(),1)
        dialog.close();dialog.deleteLater()

    def test_declining_overwrite_preserves_saved_profile_and_does_not_apply(self):
        from PySide6.QtWidgets import QMessageBox
        dialog = PrivacyProfilesDialog(self.policy,self.path,PROFILE_PRESETS["Completo"])
        dialog.store.save("Synthetic existing",PROFILE_PRESETS["Solo chat"])
        dialog.reload_profiles()
        dialog.name.setText("Synthetic existing")
        with patch("companion.privacy_profiles.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            dialog.save_profile()
        self.assertEqual(dialog.store.get("Synthetic existing"),PROFILE_PRESETS["Solo chat"])
        self.assertIsNone(dialog.selected_flags)
        dialog.close();dialog.deleteLater()
