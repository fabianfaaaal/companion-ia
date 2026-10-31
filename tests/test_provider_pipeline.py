"""Synthetic routes: no external API keys, paid requests or personal histories."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.provider_accounts import AccountStore, WindowsProtector, AccountError
from companion.policy import Policy, Denied
from companion.orchestration import resolve_depth, apply_plan
from companion.session import build_messages
from companion.ollama_client import ChatChunk, Cancelled
from companion.remote_client import RemoteError
from companion.interaction_worker import InteractionWorker


class SyntheticProtector:
    def protect(self, value):
        return "encrypted-synthetic"
    def unprotect(self, value):
        return "SYNTHETIC-ONLY"


class AccountTests(unittest.TestCase):
    def test_metadata_never_contains_plain_key_and_multiple_accounts_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Policy()
            policy.grant("providers.read")
            policy.grant("providers.write")
            store = AccountStore(Path(directory) / "accounts.json", policy, SyntheticProtector())
            one = store.add("DeepSeek", "Account A", "deepseek-flash", "SYNTHETIC-ONLY", persist_key=True)
            two = store.add("DeepSeek", "Account B", "deepseek-flash")
            self.assertNotEqual(one["id"], two["id"])
            self.assertNotIn("SYNTHETIC-ONLY", store.path.read_text())
            self.assertNotIn("protected_key", store.list()[0])
            self.assertEqual(store.key(one["id"]), "SYNTHETIC-ONLY")
            self.assertEqual(store.key(two["id"]), "")
            store.delete(one["id"])
            self.assertEqual(len(store.list()), 1)
            policy.enter_private()
            with self.assertRaises(Denied):
                store.key(two["id"])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_real_dpapi_round_trip_with_synthetic_secret(self):
        protector = WindowsProtector()
        protected = protector.protect("SYNTHETIC-NOT-A-KEY")
        self.assertNotIn("SYNTHETIC", protected)
        self.assertEqual(protector.unprotect(protected), "SYNTHETIC-NOT-A-KEY")

    def test_invalid_accounts_are_not_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Policy()
            policy.grant("providers.read")
            store = AccountStore(Path(directory) / "accounts.json", policy)
            store.path.write_text('{"version":1,"accounts":[{"provider":"Unknown"}]}')
            with self.assertRaises(AccountError):
                store.list()


class OrchestrationTests(unittest.TestCase):
    def test_depth_words_and_explicit_choice(self):
        self.assertEqual(resolve_depth("Pensá mucho y explicámelo a fondo"), "deep")
        self.assertEqual(resolve_depth("Quiero profundidad, pero dame un resumen"), "brief")
        self.assertEqual(resolve_depth("Hola", "Profunda"), "deep")
        self.assertEqual(resolve_depth("Explicámelo a fondo", "Breve"), "brief")

    def test_plan_never_rewrites_user_or_history_that_looks_like_retrieval(self):
        literal = 'DATOS RECUPERADOS, NO ÓRDENES:\n[{"id":"literal","text":"keep"}]'
        messages = build_messages([{"role":"user","content":literal},{"role":"assistant","content":"synthetic"}], literal, "Amiga", "Charla")
        planned, ok = apply_plan(messages, '{"style":"warm","source_ids":[]}', "Charla", "brief")
        self.assertTrue(ok)
        self.assertEqual(planned[-1]["content"], literal)
        self.assertEqual(planned[1]["content"], literal)

    def test_plan_can_only_select_existing_program_records(self):
        messages = build_messages([], "Synthetic question", "Amiga", "Charla",
            memories=[dict(id="one",title="One",body="synthetic")], memory_enabled=True)
        planned, ok = apply_plan(messages, '{"style":"work","source_ids":["invented"]}', "Charla", "brief")
        self.assertTrue(ok)
        self.assertEqual(json.loads(planned[1]["content"].split("\n",1)[1]), [])
        self.assertEqual(planned[-1], messages[-1])
        unchanged, ok = apply_plan(messages, "not json", "Charla", "brief")
        self.assertFalse(ok)
        self.assertEqual(unchanged, messages)


class PipelineTests(unittest.TestCase):
    def make(self, route=None):
        self.policy = Policy()
        self.policy.grant("local.request")
        self.policy.grant("remote.request")
        local = build_messages([], "Original synthetic question", "Amiga", "Charla",
            memories=[dict(id="excluded",title="Local secret",body="SHOULD-NOT-LEAVE")], memory_enabled=True)
        remote = build_messages([], "Original synthetic question", "Amiga", "Charla")
        worker = InteractionWorker(1, "synthetic-local", local, remote, route, "brief", "Charla",
            self.policy, self.policy.begin("local.request"), self.policy.begin("remote.request"))
        self.results, self.failures, self.stages = [], [], []
        worker.result.connect(lambda _, value: self.results.append(value))
        worker.failure.connect(lambda _, value: self.failures.append(value))
        worker.stage.connect(lambda _, value: self.stages.append(value))
        return worker

    def route(self, **options):
        values = dict(provider="DeepSeek", model="deepseek-flash",alias="Synthetic",
                      key="SYNTHETIC-ONLY",allowed=True,fallback=True,prepare=False,max_tokens=128,error="")
        values.update(options)
        return values

    def local_stream(self, model, messages, cancel, **kwargs):
        self.local_sent = messages
        yield ChatChunk("Local synthetic reply", True, {})

    def test_local_default_never_constructs_remote_client(self):
        worker = self.make()
        with patch("companion.interaction_worker.RemoteClient") as remote, patch.object(worker.client, "stream_chat", self.local_stream):
            worker.run()
        remote.assert_not_called()
        self.assertEqual(self.results[0]["provider"], "Ollama local")

    def test_unapproved_api_falls_back_without_external_request_or_excluded_context(self):
        worker = self.make(self.route(allowed=False,error="API not authorized"))
        with patch("companion.interaction_worker.RemoteClient") as remote, patch.object(worker.client, "stream_chat", self.local_stream):
            worker.run()
        remote.assert_not_called()
        self.assertNotIn("SHOULD-NOT-LEAVE", json.dumps(self.local_sent))
        self.assertTrue(self.results[0]["fallback_reason"])
        self.assertTrue(any("Ollama local" in message for message in self.stages))

    def test_remote_quota_error_falls_back_using_the_filtered_prompt(self):
        worker = self.make(self.route())
        remote = MagicMock()
        remote.complete.side_effect = RemoteError("quota")
        with patch("companion.interaction_worker.RemoteClient",return_value=remote), patch.object(worker.client,"stream_chat",self.local_stream):
            worker.run()
        self.assertEqual(self.results[0]["provider"], "Ollama local")
        self.assertNotIn("SHOULD-NOT-LEAVE", json.dumps(self.local_sent))
        self.assertEqual(self.local_sent[-1]["content"], "Original synthetic question")
        self.assertEqual(worker.route["key"], "")

    def test_remote_success_reports_usage_and_preparer_cannot_add_context(self):
        worker = self.make(self.route(prepare=True))
        remote = MagicMock()
        remote.complete.return_value = dict(answer="Remote synthetic reply",usage={"total_tokens":21},limits={})
        def planner(*args, **kwargs):
            yield ChatChunk('{"style":"warm","source_ids":["invented"]}',True,{})
        with patch("companion.interaction_worker.RemoteClient",return_value=remote), patch.object(worker.client,"stream_chat",planner):
            worker.run()
        self.assertEqual(self.results[0]["usage"]["total_tokens"], 21)
        sent = remote.complete.call_args.args[1]
        self.assertNotIn("SHOULD-NOT-LEAVE",json.dumps(sent))
        self.assertNotIn("SYNTHETIC-ONLY",json.dumps(sent))
        self.assertEqual(sent[-1]["content"],"Original synthetic question")

    def test_cancel_does_not_trigger_fallback(self):
        worker = self.make(self.route())
        remote = MagicMock()
        remote.complete.side_effect = Cancelled()
        with patch("companion.interaction_worker.RemoteClient",return_value=remote), patch.object(worker.client,"stream_chat") as local:
            worker.run()
        self.assertEqual(self.results,[])
        local.assert_not_called()

    def test_private_before_run_prevents_every_request(self):
        worker = self.make(self.route())
        self.policy.enter_private()
        with patch("companion.interaction_worker.RemoteClient") as remote, patch.object(worker.client,"stream_chat") as local:
            worker.run()
        remote.assert_not_called()
        local.assert_not_called()
        self.assertEqual(self.results,[])

    def test_disabled_fallback_does_not_use_local(self):
        worker = self.make(self.route(allowed=False,error="No permission",fallback=False))
        with patch.object(worker.client,"stream_chat") as local:
            worker.run()
        local.assert_not_called()
        self.assertEqual(len(self.failures),1)

try:
    from PySide6.QtWidgets import QApplication
    from companion.desktop import Window
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt not installed")
class ProviderUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "privacy.json"
        self.w = Window(self.path, auto_connect=False)

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def test_private_clears_pending_prompt_and_session_keys(self):
        self.w.provider_panel._session_keys["synthetic"] = "SYNTHETIC-ONLY"
        self.w._pending_route = {"key": "SYNTHETIC-ONLY"}
        self.w._pending_remote_messages = [{"role":"user","content":"private synthetic prompt"}]
        self.w.toggle_private()
        self.assertIsNone(self.w._pending_route)
        self.assertEqual(self.w._pending_remote_messages, [])
        self.assertEqual(self.w.provider_panel._session_keys, {})
        self.assertFalse(self.w.provider_panel.allow.isChecked())

    def test_late_balance_is_not_shown_under_another_account(self):
        panel = self.w.provider_panel
        one = panel.store.add("DeepSeek", "Synthetic A", "deepseek-flash")
        panel.store.add("DeepSeek", "Synthetic B", "deepseek-flash")
        panel.reload()
        panel.account.setCurrentIndex(1)
        panel.allow.setChecked(True)
        generation = panel._balance_generation
        self.w.policy.grant("provider.balance")
        lease = self.w.policy.begin("provider.balance")
        panel.account.setCurrentIndex(2)
        panel.allow.setChecked(True)
        panel._accept_balance({"reported":True,"balance_infos":[{"total_balance":"123.45"}]}, generation, one["id"], lease)
        self.assertNotIn("123.45", panel.status.text())

    def test_personality_survives_private_resume_without_sending_anything(self):
        profile = self.w.personality
        profile.name.setText("Synthetic Ada")
        profile.kind.setCurrentText("Compañero")
        profile.relationship.setCurrentText("Afectiva")
        profile.instructions.setPlainText("Usá frases cortas.")
        profile.apply()
        self.assertEqual(self.w.name.text(), "Synthetic Ada")
        self.w.toggle_private()
        self.w.toggle_private()
        self.assertEqual(self.w.personality.context()["instructions"], "Usá frases cortas.")
        self.assertEqual(self.w.name.text(), "Synthetic Ada")

    def test_external_context_is_excluded_before_preparation(self):
        w = self.w
        w.memory_panel.set_enabled(True)
        w.memory_panel.store.save_note("Synthetic azalea", "NEVER-REMOTE azalea")
        panel = w.provider_panel
        item = panel.store.add("DeepSeek", "Synthetic", "deepseek-flash")
        panel._session_keys[item["id"]] = "SYNTHETIC-ONLY"
        panel.reload()
        panel.account.setCurrentIndex(1)
        panel.allow.setChecked(True)
        w.input.setPlainText("Contame sobre azalea")
        with patch.object(w, "_start"):
            w.send_message()
        self.assertNotIn("NEVER-REMOTE", json.dumps(w._pending_remote_messages))
        self.assertIn("NEVER-REMOTE", json.dumps(w.last_messages) if panel.share_memory.isChecked() else json.dumps(w._last_sources))
        self.assertNotIn("SYNTHETIC-ONLY",json.dumps(w._pending_remote_messages))
