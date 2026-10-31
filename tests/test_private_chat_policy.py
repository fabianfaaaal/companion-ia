"""Private mode permits only the dedicated local ephemeral-chat channel."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.policy import Denied, Policy, State
from companion.conversation_store import ConversationStore


class PrivateChatPolicyTests(unittest.TestCase):
    def test_private_chat_is_allowed_only_in_private_without_a_grant(self):
        policy = Policy()
        with self.assertRaises(Denied):
            policy.begin("private.chat")
        policy.grant("private.chat")
        with self.assertRaises(Denied):
            policy.begin("private.chat")
        policy.enter_private()
        lease = policy.begin("private.chat")
        self.assertEqual(policy.publish(lease, lambda: "synthetic ephemeral reply"), "synthetic ephemeral reply")
        with self.assertRaises(Denied):
            policy.grant("private.chat")

    def test_private_chat_never_authorizes_restricted_access(self):
        policy = Policy()
        policy.open_intimacy(local_action=True)
        policy.enter_private()
        for restricted in (True, None, 0, ""):
            with self.assertRaises(Denied):
                policy.begin("private.chat", restricted=restricted)
        self.assertEqual(policy.begin("private.chat").operation, "private.chat")

    def test_other_operations_remain_denied_even_when_previously_granted(self):
        policy = Policy()
        operations = ("memory.read", "memory.write", "local.request", "remote.request",
                      "sensor.foreground", "microphone.capture", "providers.read",
                      "conversation.read", "conversation.write", "history.read",
                      "private.chat.read", "PRIVATE.chat")
        for operation in operations:
            policy.grant(operation)
        policy.set_memory(True)
        policy.enter_private()
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(Denied):
                    policy.begin(operation)
        policy.publish(policy.begin("private.chat"), lambda: None)

    def test_private_lease_is_invalid_after_resume_and_after_reentering_private(self):
        policy = Policy()
        policy.enter_private()
        lease = policy.begin("private.chat")
        policy.resume(local_action=True)
        with self.assertRaises(Denied):
            policy.publish(lease, lambda: self.fail("late private response"))
        with self.assertRaises(Denied):
            policy.begin("private.chat")
        policy.enter_private()
        with self.assertRaises(Denied):
            policy.publish(lease, lambda: self.fail("old private response after reentry"))
        self.assertEqual(policy.publish(policy.begin("private.chat"), lambda: "new session"), "new session")

    def test_restored_private_latch_allows_only_the_private_chat_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "privacy.json"
            Policy(path).enter_private()
            policy = Policy(path)
            self.assertEqual(policy.state, State.PRIVATE)
            self.assertTrue(policy.publish(policy.begin("private.chat"), lambda: True))
            with self.assertRaises(Denied):
                policy.begin("local.request")
            self.assertEqual(path.read_text(encoding="utf-8"), '{"private": true}')

    def test_stopping_and_shutdown_error_keep_private_chat_blocked(self):
        policy = Policy()
        def stop():
            self.assertEqual(policy.state, State.STOPPING)
            with self.assertRaises(Denied):
                policy.begin("private.chat")
            raise RuntimeError("synthetic stop failure")
        policy.register_stopper(stop)
        with self.assertRaises(RuntimeError):
            policy.enter_private()
        self.assertEqual(policy.state, State.ERROR)
        with self.assertRaises(Denied):
            policy.begin("private.chat")

    def test_private_chat_does_not_make_conversation_storage_available(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite"
            policy = Policy()
            for operation in ("memory.read", "memory.write", "conversation.read", "conversation.write"):
                policy.grant(operation)
            policy.set_memory(True)
            policy.enter_private()
            self.assertTrue(policy.publish(policy.begin("private.chat"), lambda: True))
            store = ConversationStore(path, policy)
            with self.assertRaises(Denied):
                store.create("Must remain ephemeral")
            with self.assertRaises(Denied):
                store.list_conversations()
            self.assertFalse(path.exists())
