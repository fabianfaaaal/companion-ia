"""Policy boundary tests using fictitious actions and temporary technical state."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.policy import Denied, Policy, State


class PolicyTests(unittest.TestCase):
    def test_memory_is_opt_in_even_with_operation_grant(self):
        policy = Policy()
        policy.grant("memory.write")
        with self.assertRaises(Denied):
            policy.begin("memory.write")
        policy.set_memory(True)
        lease = policy.begin("memory.write")
        policy.set_memory(False)
        with self.assertRaises(Denied):
            policy.publish(lease, lambda: self.fail("must not persist"))

    def test_revocation_blocks_already_queued_result(self):
        policy = Policy()
        policy.grant("llm.send")
        lease = policy.begin("llm.send")
        policy.revoke("llm.send")
        with self.assertRaises(Denied):
            policy.publish(lease, lambda: self.fail("must not send"))

    def test_private_blocks_late_output_and_releases_registered_resource(self):
        policy = Policy()
        released = []
        policy.grant("chat.output")
        policy.register_stopper(lambda: released.append(True))
        lease = policy.begin("chat.output")
        policy.enter_private()
        self.assertEqual(policy.state, State.PRIVATE)
        self.assertEqual(released, [True])
        with self.assertRaises(Denied):
            policy.publish(lease, lambda: self.fail("late response leaked"))
        with self.assertRaises(Denied):
            policy.begin("chat.output")
        with self.assertRaises(Denied):
            policy.resume(local_action=False)

    def test_private_persists_and_intimacy_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "runtime.json"
            policy = Policy(state_path)
            policy.open_intimacy(local_action=True)
            policy.enter_private()
            restored = Policy(state_path)
            self.assertEqual(restored.state, State.PRIVATE)
            restored.resume(local_action=True)
            restored.grant("chat.output")
            with self.assertRaises(Denied):
                restored.begin("chat.output", restricted=True)
            self.assertEqual(state_path.read_text(), '{"private": false}')

    def test_closing_intimacy_invalidates_restricted_generation(self):
        policy = Policy()
        policy.grant("chat.output")
        with self.assertRaises(Denied):
            policy.open_intimacy(local_action=False)
        with self.assertRaises(Denied):
            policy.begin("chat.output", restricted=True)
        policy.open_intimacy(local_action=True)
        lease = policy.begin("chat.output", restricted=True)
        policy.close_intimacy()
        with self.assertRaises(Denied):
            policy.publish(lease, lambda: self.fail("restricted result leaked"))

    def test_shutdown_error_remains_blocked_and_attempts_other_stoppers(self):
        policy = Policy()
        released = []
        def fail():
            raise RuntimeError("synthetic resource failure")
        policy.register_stopper(fail)
        policy.register_stopper(lambda: released.append(True))
        with self.assertRaises(RuntimeError):
            policy.enter_private()
        self.assertEqual(policy.state, State.ERROR)
        self.assertEqual(released, [True])
        with self.assertRaises(Denied):
            policy.resume(local_action=True)
        with self.assertRaises(Denied):
            policy.grant("llm.send")

    def test_corrupt_persisted_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime.json"
            path.write_text("not json")
            self.assertEqual(Policy(path).state, State.ERROR)

    def test_failure_writing_latch_still_releases_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            parent_file = Path(folder) / "not-a-directory"
            parent_file.write_text("synthetic")
            policy = Policy(parent_file / "runtime.json")
            released = []
            policy.register_stopper(lambda: released.append(True))
            with self.assertRaises(RuntimeError):
                policy.enter_private()
            self.assertEqual(policy.state, State.ERROR)
            self.assertEqual(released, [True])

    def test_lease_cannot_cross_policy_instances(self):
        first, second = Policy(), Policy()
        first.grant("chat.output")
        second.grant("chat.output")
        with self.assertRaises(Denied):
            second.publish(first.begin("chat.output"), lambda: self.fail("wrong owner"))

    def test_authorized_action_publishes(self):
        policy = Policy()
        policy.grant("chat.output")
        result = policy.publish(policy.begin("chat.output"), lambda: "synthetic reply")
        self.assertEqual(result, "synthetic reply")


if __name__ == "__main__":
    unittest.main()
