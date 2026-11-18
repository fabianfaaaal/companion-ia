"""Portable snapshot tests use synthetic records and temporary local folders."""
from pathlib import Path
from contextlib import closing
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from companion.archive_transfer import ArchiveTransfer,ArchiveError,ArchiveCancelled,apply_pending_restore
from companion.memory_store import MemoryStore
from companion.policy import Policy,Denied,State
from companion.preferences import Preferences


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.data=self.root/"source"
        self.data.mkdir()
        self.policy=self.make_policy(self.data)
        self.store=MemoryStore(self.data/"memory.sqlite",self.policy)
        self.store.save_note("Synthetic garden","Synthetic apricot tree")
        self.transfer=ArchiveTransfer(self.data,self.policy)

    def tearDown(self):
        self.temp.cleanup()

    def make_policy(self,directory):
        policy=Policy(directory/"privacy.json")
        policy.grant("memory.read")
        policy.grant("memory.write")
        policy.set_memory(True)
        return policy

    def pasted(self):
        bundle=self.transfer.export()
        target=self.data/"transfer/incoming"/bundle.name
        shutil.copytree(bundle,target)
        return target

    def update_manifest(self,bundle):
        manifest=json.loads((bundle/"manifest.json").read_text())
        for record in manifest["files"]:
            file=bundle/record["path"]
            record["size"]=file.stat().st_size
            record["sha256"]=hashlib.sha256(file.read_bytes()).hexdigest()
        (bundle/"manifest.json").write_text(json.dumps(manifest))

    def test_snapshot_roundtrip_and_credential_exclusion(self):
        (self.data/"provider-accounts.json").write_text('{"protected_key":"synthetic-secret"}')
        (self.data/"privacy.json").write_text('{"private":false}')
        (self.data/"personality.json").write_text('{"name":"Synthetic companion"}')
        bundle=self.pasted()
        self.assertFalse((bundle/"provider-accounts.json").exists())
        self.assertFalse((bundle/"privacy.json").exists())
        self.assertEqual(self.transfer.scan_incoming()[0]["path"],bundle)
        self.transfer.prepare_restore(bundle)
        self.store.save_note("Later synthetic note","not in snapshot")
        self.assertIn("Restauración completada",apply_pending_restore(self.data))
        notes=self.store.list_notes()
        self.assertEqual([row["title"] for row in notes],["Synthetic garden"])
        self.assertEqual(json.loads((self.data/"personality.json").read_text())["name"],"Synthetic companion")
        self.assertIn("synthetic-secret",(self.data/"provider-accounts.json").read_text())
        self.assertTrue(list((self.data/"transfer/rollback").iterdir()))

    def test_import_to_another_installation(self):
        bundle=self.transfer.export()
        other=self.root/"other"
        other.mkdir()
        policy=self.make_policy(other)
        transfer=ArchiveTransfer(other,policy)
        inbox,_=transfer.folders()
        copied=inbox/bundle.name
        shutil.copytree(bundle,copied)
        transfer.prepare_restore(copied)
        self.assertIsNotNone(apply_pending_restore(other))
        self.assertEqual(MemoryStore(other/"memory.sqlite",policy).search("apricot")[0]["title"],"Synthetic garden")

    def test_tampered_copy_never_prepares_or_modifies_active_archive(self):
        bundle=self.pasted()
        (bundle/"memory.sqlite").write_bytes(b"broken")
        with self.assertRaises(ArchiveError):
            self.transfer.prepare_restore(bundle)
        self.assertEqual(len(self.store.list_notes()),1)
        self.assertFalse((self.data/"transfer/pending-restore.json").exists())

    def test_traversal_unknown_payload_duplicate_and_future_version_rejected(self):
        for alter in (lambda m:m["files"].append(dict(path="../escape",size=0,sha256="0"*64)),
                      lambda m:m["files"].append(dict(path="run.exe",size=0,sha256="0"*64)),
                      lambda m:m["files"].append(m["files"][0].copy()),
                      lambda m:m.update(version=99)):
            with self.subTest(alter=alter):
                bundle=self.transfer.export()
                m=json.loads((bundle/"manifest.json").read_text())
                alter(m)
                (bundle/"manifest.json").write_text(json.dumps(m))
                with self.assertRaises(ArchiveError):
                    self.transfer.validate_bundle(bundle)

    def test_unknown_file_and_incomplete_copy_not_offered(self):
        bundle=self.pasted()
        (bundle/"extra.exe").write_bytes(b"synthetic")
        self.assertEqual(self.transfer.scan_incoming(),[])
        (bundle/"extra.exe").unlink()
        (bundle/"memory.sqlite").unlink()
        self.assertEqual(self.transfer.scan_incoming(),[])

    def test_sql_trigger_and_unknown_component_rejected(self):
        bundle=self.transfer.export()
        with closing(sqlite3.connect(bundle/"memory.sqlite")) as db, db:
            db.execute("CREATE TRIGGER malicious AFTER INSERT ON notes BEGIN SELECT 1; END")
        self.update_manifest(bundle)
        with self.assertRaises(ArchiveError):
            self.transfer.validate_bundle(bundle)
        bundle=self.transfer.export()
        with closing(sqlite3.connect(bundle/"memory.sqlite")) as db, db:
            db.execute("CREATE TABLE component_versions(name TEXT PRIMARY KEY,version INTEGER NOT NULL)")
            db.execute("INSERT INTO component_versions VALUES('unrecognized',1)")
        self.update_manifest(bundle)
        with self.assertRaises(ArchiveError):
            self.transfer.validate_bundle(bundle)

    def test_memory_off_and_private_block_even_scan(self):
        self.policy.set_memory(False)
        with self.assertRaises(Denied):
            self.transfer.scan_incoming()
        self.policy.set_memory(True)
        self.policy.enter_private()
        for operation in (self.transfer.export,self.transfer.scan_incoming):
            with self.assertRaises(Denied):
                operation()

    def test_cancelled_export_publishes_no_partial_bundle(self):
        self.transfer.cancel()
        with self.assertRaises(ArchiveCancelled):
            self.transfer.export()
        self.assertFalse(self.transfer.outgoing.exists())

    def test_permission_revocation_during_copy_cannot_publish(self):
        original=self.transfer._digest
        def revoke(path,lease):
            result=original(path,lease)
            self.policy.set_memory(False)
            return result
        with patch.object(self.transfer,"_digest",side_effect=revoke):
            with self.assertRaises(Denied):
                self.transfer.export()
        self.assertEqual(list(self.transfer.outgoing.iterdir()),[])

    def test_startup_honors_private_memory_off_and_active_profile(self):
        self.transfer.prepare_restore(self.pasted())
        (self.data/"privacy.json").write_text('{"private":true}')
        self.assertIsNone(apply_pending_restore(self.data))
        (self.data/"privacy.json").write_text('{"private":false}')
        prefs=Preferences(self.data/"preferences.json")
        options=prefs.load()
        options["memory"]=False
        prefs.save(options)
        self.assertIsNone(apply_pending_restore(self.data))
        options["memory"]=True
        prefs.save(options)
        (self.data/"active-privacy-profile.json").write_text('{"memory":false}')
        self.assertIsNone(apply_pending_restore(self.data))
        self.assertTrue((self.data/"transfer/pending-restore.json").exists())

    def test_interrupted_restore_recovers_original_from_journal(self):
        self.transfer.prepare_restore(self.pasted())
        self.store.save_note("Later synthetic note","not in snapshot")
        original=Path.replace
        def interrupt(path,target):
            if path.parent.parent.name == "prepared" and path.name == "memory.sqlite":
                raise KeyboardInterrupt("synthetic power loss")
            return original(path,target)
        # Simulate power loss bypassing same-process recovery, leaving applying journal.
        with patch("pathlib.Path.replace",new=interrupt),patch.object(self.transfer,"_recover",side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.transfer.apply_pending()
        message=apply_pending_restore(self.data)
        self.assertIn("recuperó",message)
        self.assertEqual(len(self.store.list_notes()),2)
        self.assertFalse((self.data/"transfer/pending-restore.json").exists())

    def test_symlink_payload_rejected_where_supported(self):
        bundle=self.transfer.export()
        victim=self.root/"outside.json"
        victim.write_text('{}')
        link=bundle/"personality.json"
        try:
            link.symlink_to(victim)
        except OSError:
            self.skipTest("OS does not allow synthetic symlink creation")
        with self.assertRaises(ArchiveError):
            self.transfer.validate_bundle(bundle)

    def test_photos_context_proposals_conversations_and_agenda_roundtrip(self):
        from PIL import Image
        from companion.context_store import ContextStore
        from companion.media_store import MediaStore
        from companion.conversation_store import ConversationStore
        from companion.agenda import AgendaStore
        from datetime import datetime,timedelta,timezone
        for permission in ("media.read","media.write","conversation.read","conversation.write","agenda.read","agenda.write"):
            self.policy.grant(permission)
        image=self.root/"synthetic.png"
        Image.new("RGB",(16,16),"teal").save(image)
        media=MediaStore(self.data/"memory.sqlite",self.policy)
        moment=media.import_photo(image,"Synthetic image","Synthetic photo context")
        contexts=ContextStore(self.data/"memory.sqlite",self.policy)
        contexts.set_enabled(True)
        card=contexts.save_card("Proyectos","Synthetic project","First version",["apricot project"])
        proposal=contexts.propose(card["id"],"Revised version",source_ref="Synthetic user message")
        conversation=ConversationStore(self.data/"memory.sqlite",self.policy)
        saved=conversation.create("Synthetic conversation")
        conversation.append(saved["id"],"user","Synthetic saved words")
        agenda=AgendaStore(self.data/"memory.sqlite",self.policy)
        alarm=agenda.add("Synthetic reminder","Synthetic",datetime.now(timezone.utc)+timedelta(days=1))
        bundle=self.pasted()
        self.transfer.prepare_restore(bundle)
        media.delete_moment(moment["id"])
        contexts.delete_card(card["id"])
        self.assertIsNotNone(apply_pending_restore(self.data))
        self.assertEqual(media.list_moments()[0]["id"],moment["id"])
        self.assertEqual(contexts.list_cards()[0]["id"],card["id"])
        self.assertEqual(len(contexts.list_proposals()),1)
        self.assertEqual(len(conversation.messages(saved["id"])),1)

    def test_windows_reparse_point_is_rejected_without_real_link(self):
        from types import SimpleNamespace
        from companion.archive_transfer import _ordinary
        with patch("pathlib.Path.lstat",return_value=SimpleNamespace(st_mode=0o100600,st_file_attributes=0x400,st_nlink=1)):
            with self.assertRaises(ArchiveError):
                _ordinary(self.root/"synthetic-reparse")

    def test_interrupted_swap_recovers_even_if_restored_preferences_disabled_memory(self):
        preferences=Preferences(self.data/"preferences.json")
        options=preferences.load()
        options["memory"]=False
        preferences.save(options)
        bundle=self.pasted()
        options["memory"]=True
        preferences.save(options)
        self.transfer.prepare_restore(bundle)
        original=Path.replace
        def interrupt(path,target):
            if path.parent.parent.name=="prepared" and path.name=="memory.sqlite":
                raise KeyboardInterrupt("synthetic interruption after preferences")
            return original(path,target)
        with patch("pathlib.Path.replace",new=interrupt),patch.object(self.transfer,"_recover",side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.transfer.apply_pending()
        self.assertFalse(preferences.load()["memory"])
        self.assertIn("recuperó",apply_pending_restore(self.data))
        self.assertTrue(preferences.load()["memory"])
        self.assertEqual(len(self.store.list_notes()),1)

    def test_interrupted_swap_in_private_blocks_startup_without_recovery_reads(self):
        self.transfer.prepare_restore(self.pasted())
        pending=self.data/"transfer/pending-restore.json"
        journal=json.loads(pending.read_text())
        journal["state"]="applying"
        pending.write_text(json.dumps(journal))
        (self.data/"privacy.json").write_text('{"private":true}')
        with patch.object(ArchiveTransfer,"_recover") as recover:
            with self.assertRaises(ArchiveError):
                apply_pending_restore(self.data)
            recover.assert_not_called()
        self.assertTrue(json.loads((self.data/"privacy.json").read_text())["private"])


class ArchivePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_old_worker_result_cannot_reappear_after_private_resume(self):
        from companion.archive_panel import ArchivePanel
        from companion.policy import Policy
        with tempfile.TemporaryDirectory() as folder:
            policy=Policy(Path(folder)/"privacy.json")
            policy.grant("memory.read")
            policy.set_memory(True)
            panel=ArchivePanel(policy,folder)
            worker=object()
            panel._worker=worker
            panel.enabled=True
            lease=policy.begin("memory.read")
            policy.enter_private()
            policy.resume(local_action=True)
            with patch.object(panel,"_result") as publish:
                panel._accept(worker,lease,"scan",[{"name":"synthetic"}])
                publish.assert_not_called()
            panel._worker=None
            panel.stop()
            panel.deleteLater()
            self.app.processEvents()

    def test_queued_scan_cannot_recreate_folders_after_stop_and_close(self):
        from companion.archive_panel import ArchivePanel
        with tempfile.TemporaryDirectory() as folder:
            policy=Policy(Path(folder)/"privacy.json")
            policy.grant("memory.read")
            policy.set_memory(True)
            panel=ArchivePanel(policy,folder)
            panel.refresh(True)
            panel.stop(permanent=True)
            # Single-shot initial scan may still be queued, as may worker-finished.
            for _ in range(5):
                self.app.processEvents()
            panel.refresh(True)
            self.app.processEvents()
            self.assertIsNone(panel._worker)
            self.assertFalse(panel.timer.isActive())
            self.assertFalse((Path(folder)/"transfer").exists())
            panel.deleteLater()
            self.app.processEvents()
