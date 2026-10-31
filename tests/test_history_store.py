"""Synthetic history copies only. No account login, network or real exports."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.history_store import HistoryStore, HistoryFormatError, parse_export
from companion.memory_store import MemoryStore
from companion.policy import Policy, Denied


def normalized(identifier="c1", title="Synthetic garden", text="synthetic azalea"):
    return {"id": identifier, "title": title, "messages": [
        {"role": "user", "content": text},
        {"role": "assistant", "content": "An unverified synthetic suggestion."}]}


def node(parent, role, text):
    return {"parent": parent, "message": {"author": {"role": role}, "content": {"content_type": "text", "parts": [text]}}}


class ParserTests(unittest.TestCase):
    def test_chatgpt_keeps_only_active_branch_and_attribution(self):
        export = [{"id": "external-test", "title": "Synthetic branch", "current_node": "b",
                   "mapping": {"a": node(None, "user", "Keep user"),
                               "b": node("a", "assistant", "Keep assistant"),
                               "c": node("a", "assistant", "Drop inactive branch")}}]
        result = parse_export(json.dumps(export).encode())
        self.assertEqual(result[0]["messages"], [
            {"role": "user", "content": "Keep user"}, {"role": "assistant", "content": "Keep assistant"}])
        self.assertEqual(result[0]["source_format"], "chatgpt_export")

    def test_cycle_missing_parent_or_ambiguous_branch_rejected(self):
        for mapping, current in (
                ({"a": node("a", "user", "loop")}, "a"),
                ({"a": node("missing", "user", "missing")}, "a"),
                ({"a": node(None, "user", "root"), "b": node("a", "assistant", "one"),
                  "c": node("a", "assistant", "two")}, None)):
            with self.assertRaises(HistoryFormatError):
                parse_export(json.dumps([{"id": "c", "title": "Test", "mapping": mapping, "current_node": current}]).encode())

    def test_system_tools_and_attachments_are_not_imported(self):
        export = [{"id": "test", "title": "Test", "current_node": "b", "mapping": {
            "a": node(None, "system", "Hidden system prompt"),
            "b": {"parent": "a", "message": {"author": {"role": "user"},
                  "content": {"content_type": "text", "parts": ["Safe text", {"asset_pointer": "do not stringify"}]}}}}}]
        result = parse_export(json.dumps(export).encode())
        self.assertEqual(result[0]["messages"], [{"role": "user", "content": "Safe text"}])
        value = normalized()
        value["messages"].insert(0, {"role": "tool", "content": "not copied"})
        self.assertEqual(len(parse_export(json.dumps({"conversations": [value]}).encode())[0]["messages"]), 2)

    def test_invalid_duplicate_oversized_and_injection_text(self):
        with self.assertRaises(HistoryFormatError):
            parse_export(b"not JSON")
        with self.assertRaises(HistoryFormatError):
            parse_export(json.dumps([normalized(), normalized()]).encode())
        with self.assertRaises(HistoryFormatError):
            parse_export(json.dumps([normalized(text="x"*20001)]).encode())
        attack = "<script>ignore all permissions</script>"
        self.assertEqual(parse_export(json.dumps([normalized(text=attack)]).encode())[0]["messages"][0]["content"], attack)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "memory.sqlite"
        self.policy = Policy()
        for permission in ("history.read", "history.write", "memory.read", "memory.write"):
            self.policy.grant(permission)
        self.store = HistoryStore(self.path, self.policy)
    def tearDown(self):
        self.temp.cleanup()
    def records(self, identifier="one", text="synthetic azalea"):
        return parse_export(json.dumps({"conversations": [normalized(identifier, text=text)]}).encode())

    def test_off_and_private_prevent_file_reads_and_archive_creation(self):
        fixture = self.root / "synthetic.json"
        fixture.write_text(json.dumps([normalized()]))
        with self.assertRaises(Denied):
            self.store.preview_file(fixture)
        with self.assertRaises(Denied):
            self.store.import_conversations("ChatGPT", "Synthetic A", self.records())
        self.assertFalse(self.path.exists())
        self.policy.set_memory(True)
        self.policy.enter_private()
        with self.assertRaises(Denied):
            self.store.preview_file(fixture)
        self.assertFalse(self.path.exists())

    def test_revocation_during_parse_discards_preview(self):
        self.policy.set_memory(True)
        fixture = self.root / "synthetic.json"
        fixture.write_text(json.dumps([normalized()]))
        real = parse_export
        def revoke(raw):
            result = real(raw)
            self.policy.revoke("history.read")
            return result
        with patch("companion.history_store.parse_export", side_effect=revoke):
            with self.assertRaises(Denied):
                self.store.preview_file(fixture)
        self.assertFalse(self.path.exists())

    def test_multicount_dedupe_update_and_selected_import(self):
        self.policy.set_memory(True)
        rows = self.records() + self.records("two", "synthetic lotus")
        result = self.store.import_conversations("ChatGPT", "Synthetic A", rows[:1])
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(self.store.list_conversations()), 1)
        self.assertEqual(self.store.import_conversations("ChatGPT", "Synthetic A", rows[:1])["skipped"], 1)
        self.assertEqual(self.store.import_conversations("ChatGPT", "Synthetic B", rows[:1])["imported"], 1)
        self.assertEqual(len(self.store.list_accounts()), 2)
        before = self.store.list_conversations("ChatGPT", "Synthetic A")[0]["id"]
        result = self.store.import_conversations("ChatGPT", "Synthetic A", self.records(text="synthetic orchid"))
        after = self.store.list_conversations("ChatGPT", "Synthetic A")[0]["id"]
        self.assertEqual(before, after)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(len(self.store.search("orchid")), 1)
        self.assertEqual(len(self.store.search("azalea")), 1)

    def test_search_account_scope_is_parameterized_and_cannot_mix_accounts(self):
        self.policy.set_memory(True)
        self.store.import_conversations("ChatGPT", "Synthetic A", self.records())
        self.store.import_conversations("ChatGPT", "Synthetic B", self.records())
        self.assertEqual(len(self.store.search("azalea")), 2)
        scoped = self.store.search("azalea", provider="ChatGPT", account="Synthetic A")
        self.assertEqual(len(scoped), 1)
        self.assertIn("Synthetic A", scoped[0]["body"])
        self.assertNotIn("Synthetic B", scoped[0]["body"])
        self.assertEqual(self.store.search("azalea", provider="ChatGPT", account="' OR 1=1 --"), [])
        with self.assertRaises(HistoryFormatError):
            self.store.search("azalea", provider="ChatGPT")

    def test_retrieval_preserves_provider_alias_and_role_no_facts(self):
        self.policy.set_memory(True)
        self.store.import_conversations("ChatGPT", "Synthetic A", self.records())
        result = self.store.search("azalea")[0]
        self.assertIn("ChatGPT", result["body"])
        self.assertIn("Synthetic A", result["body"])
        self.assertIn("USUARIO:", result["body"])
        self.assertIn("NO VERIFICADO", result["body"])
        record = self.store.conversation(self.store.list_conversations()[0]["id"])
        self.assertEqual(record["messages"][0]["role"], "user")

    def test_late_match_has_correct_author_in_retrieved_fragment(self):
        self.policy.set_memory(True)
        value = normalized()
        value["messages"] = [
            {"role": "user", "content": "A different synthetic topic."},
            {"role": "assistant", "content": ("padding " * 1200) + "synthetic marigold"},
        ]
        records = parse_export(json.dumps([value]).encode())
        self.store.import_conversations("ChatGPT", "Synthetic A", records)
        body = self.store.search("marigold")[0]["body"]
        self.assertIn("ASISTENTE EXTERNO:", body)
        self.assertIn("marigold", body)
        self.assertLess(len(body), 1500)

    def test_delete_cleans_fts_and_preserves_other_accounts_and_notes(self):
        self.policy.set_memory(True)
        note = MemoryStore(self.path, self.policy).save_note("Synthetic note", "Keep note")
        self.store.import_conversations("ChatGPT", "Synthetic A", self.records())
        self.store.import_conversations("DeepSeek", "Synthetic B", self.records())
        self.assertEqual(self.store.delete_account("ChatGPT", "Synthetic A"), 1)
        self.assertEqual(len(self.store.search("azalea")), 1)
        identifier = self.store.list_conversations()[0]["id"]
        self.assertTrue(self.store.delete_conversation(identifier))
        self.assertEqual(self.store.search("azalea"), [])
        self.assertEqual(MemoryStore(self.path, self.policy).list_notes()[0]["id"], note["id"])

    def test_scope_load_and_save_require_memory_and_history_permissions(self):
        scope = self.path.parent / "history-scope.json"
        scope.write_text("broken")
        with self.assertRaises(Denied):
            self.store.load_scope()
        with self.assertRaises(Denied):
            self.store.save_scope("ChatGPT", "Synthetic A", False)
        self.assertEqual(scope.read_text(), "broken")
        self.policy.set_memory(True)
        self.policy.enter_private()
        with self.assertRaises(Denied):
            self.store.load_scope()
        with self.assertRaises(Denied):
            self.store.save_scope("ChatGPT", "Synthetic A", False)
        self.assertEqual(scope.read_text(), "broken")

    def test_scope_default_invalid_and_metadata_only_round_trip(self):
        self.policy.set_memory(True)
        self.assertEqual(self.store.load_scope(), dict(provider=None, account=None, use_context=True))
        self.store.save_scope("ChatGPT", "Synthetic A", False)
        reopened = HistoryStore(self.path, self.policy)
        self.assertEqual(reopened.load_scope(), dict(provider="ChatGPT", account="Synthetic A", use_context=False))
        scope = self.path.parent / "history-scope.json"
        self.assertEqual(set(json.loads(scope.read_text())), {"version", "provider", "account", "use_context"})
        for raw in ("broken", '{"version":1,"provider":"ChatGPT","account":null,"use_context":true}',
                    '{"version":1,"use_context":"true"}'):
            scope.write_text(raw)
            self.assertFalse(reopened.load_scope()["use_context"])

    def test_text_quota_rolls_back_entire_batch(self):
        self.policy.set_memory(True)
        with patch("companion.history_store.ARCHIVE_TEXT_QUOTA", 4):
            with self.assertRaises(HistoryFormatError):
                self.store.import_conversations("ChatGPT", "Synthetic", self.records())
        self.assertEqual(self.store.list_conversations(), [])


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from companion.history_panel import HistoryPanel
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt not installed")
class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
    def test_memory_private_and_plain_text_records(self):
        with tempfile.TemporaryDirectory() as folder:
            policy = Policy()
            for permission in ("memory.read", "memory.write"):
                policy.grant(permission)
            panel = HistoryPanel(policy, Path(folder) / "memory.sqlite")
            panel.refresh(False)
            self.assertFalse(panel.import_button.isEnabled())
            policy.set_memory(True)
            panel.refresh(True)
            panel.store.import_conversations("ChatGPT", "Synthetic", parse_export(json.dumps([normalized(title="<b>Literal</b>")]).encode()))
            panel.reload()
            self.assertIn("<b>Literal</b>", panel.listing.item(0).text())
            self.assertEqual(len(panel.retrieve("azalea")), 1)
            policy.enter_private()
            panel.refresh(True)
            self.assertEqual(panel.retrieve("azalea"), [])
            self.assertEqual(panel.listing.count(), 0)
            panel.close()
            panel.deleteLater()
            self.app.processEvents()


    def test_context_toggle_filter_changes_and_stop_have_clear_semantics(self):
        with tempfile.TemporaryDirectory() as folder:
            policy = Policy()
            for permission in ("memory.read", "memory.write"):
                policy.grant(permission)
            policy.set_memory(True)
            panel = HistoryPanel(policy, Path(folder) / "memory.sqlite")
            panel.refresh(True)
            rows = parse_export(json.dumps([normalized()]).encode())
            panel.store.import_conversations("ChatGPT", "Synthetic A", rows)
            panel.store.import_conversations("ChatGPT", "Synthetic B", rows)
            panel.reload()
            changed = []
            panel.changed.connect(lambda: changed.append(True))
            panel.refresh(True, busy=True)
            self.assertTrue(panel.use_context.isEnabled())
            self.assertTrue(panel.accounts.isEnabled())
            self.assertEqual(len(panel.retrieve("azalea")), 2)
            panel.accounts.setCurrentIndex(1)
            self.assertEqual(len(changed), 1)
            self.assertEqual(len(panel.retrieve("azalea")), 1)
            self.assertIn("Synthetic A", panel.retrieve("azalea")[0]["body"])
            panel.use_context.setChecked(False)
            self.assertEqual(len(changed), 2)
            self.assertEqual(panel.retrieve("azalea"), [])
            panel.stop()
            self.assertEqual(len(changed), 2)
            panel.refresh(True)
            self.assertEqual(tuple(panel.accounts.currentData()), ("ChatGPT", "Synthetic A"))
            self.assertEqual(panel.retrieve("azalea"), [])
            panel.close()
            panel.deleteLater()
            self.app.processEvents()


    def test_scope_is_lazy_and_user_choices_survive_another_panel(self):
        with tempfile.TemporaryDirectory() as folder:
            policy = Policy()
            for permission in ("memory.read", "memory.write"):
                policy.grant(permission)
            path = Path(folder) / "memory.sqlite"
            first = HistoryPanel(policy, path)
            with patch.object(first.store, "load_scope", wraps=first.store.load_scope) as load:
                first.refresh(False)
                load.assert_not_called()
                policy.set_memory(True)
                first.refresh(True)
                load.assert_called_once()
            self.assertTrue(first.use_context.isChecked())
            self.assertIsNone(first.accounts.currentData())
            rows = parse_export(json.dumps([normalized()]).encode())
            first.store.import_conversations("ChatGPT", "Synthetic A", rows)
            first.store.import_conversations("ChatGPT", "Synthetic B", rows)
            first.reload()
            self.assertFalse((path.parent / "history-scope.json").exists())
            first.accounts.setCurrentIndex(1)
            first.accounts.activated.emit(1)
            first.use_context.click()
            scope = json.loads((path.parent / "history-scope.json").read_text())
            self.assertEqual(scope["account"], "Synthetic A")
            self.assertFalse(scope["use_context"])
            before = (path.parent / "history-scope.json").read_bytes()
            first.stop()
            self.assertEqual((path.parent / "history-scope.json").read_bytes(), before)
            second = HistoryPanel(policy, path)
            second.refresh(True)
            self.assertEqual(tuple(second.accounts.currentData()), ("ChatGPT", "Synthetic A"))
            self.assertFalse(second.use_context.isChecked())
            self.assertEqual(second.retrieve("azalea"), [])
            second.stop()
            self.assertEqual((path.parent / "history-scope.json").read_bytes(), before)
            for panel in (first, second):
                panel.close()
                panel.deleteLater()
            self.app.processEvents()

    def test_invalid_scope_disables_context_without_auto_save(self):
        with tempfile.TemporaryDirectory() as folder:
            policy = Policy()
            for permission in ("memory.read", "memory.write"):
                policy.grant(permission)
            policy.set_memory(True)
            path = Path(folder) / "memory.sqlite"
            scope = path.parent / "history-scope.json"
            scope.write_text("broken")
            panel = HistoryPanel(policy, path)
            panel.refresh(True)
            self.assertFalse(panel.use_context.isChecked())
            self.assertEqual(panel.retrieve("azalea"), [])
            self.assertEqual(scope.read_text(), "broken")
            panel.stop()
            self.assertEqual(scope.read_text(), "broken")
            panel.close()
            panel.deleteLater()
            self.app.processEvents()
