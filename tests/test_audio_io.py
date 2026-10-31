"""Audio adapter tests never open microphones or speakers."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.audio_io import AudioIO, AudioError
from companion.policy import Policy, Denied
try:
    import numpy as np
except ImportError:
    np = None


class CallbackStop(Exception):
    pass


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.aborted = False
        self.closed = False
    def start(self):
        self.started = True
    def abort(self):
        self.aborted = True
    def close(self):
        self.closed = True


class FakeAudio:
    CallbackStop = CallbackStop
    def __init__(self):
        self.inputs = []
        self.outputs = []
    def InputStream(self, **kwargs):
        stream = FakeStream(**kwargs)
        self.inputs.append(stream)
        return stream
    def OutputStream(self, **kwargs):
        stream = FakeStream(**kwargs)
        self.outputs.append(stream)
        return stream
    def query_devices(self):
        return [{"name":"synthetic input", "max_input_channels":1},
                {"name":"synthetic output", "max_input_channels":0}]


class AudioPermissionTests(unittest.TestCase):
    def test_constructor_is_lazy_and_grants_no_permissions(self):
        policy = Policy()
        audio = AudioIO(policy)
        with patch.object(audio, "_dependencies") as dependencies:
            for action in (audio.begin_recording, audio.devices, lambda: audio.play_samples([], 16000)):
                with self.assertRaises(Denied):
                    action()
            dependencies.assert_not_called()
        self.assertFalse(audio.recording)
        audio.cancel()


@unittest.skipIf(np is None, "Optional NumPy is not installed")
class AudioTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy()
        self.module = FakeAudio()
        self.audio = AudioIO(self.policy, module=self.module, numpy_module=np)
        self.addCleanup(self.audio.cancel)
        for operation in ("microphone.capture", "microphone.devices", "audio.playback"):
            self.policy.grant(operation)

    def capture(self, samples):
        self.module.inputs[-1].kwargs["callback"](samples, len(samples), None, None)

    def test_device_listing_filters_outputs(self):
        self.assertEqual(self.audio.devices(), [{"index":0, "name":"synthetic input"}])
        self.policy.revoke("microphone.devices")
        with self.assertRaises(Denied):
            self.audio.devices()

    def test_record_returns_owned_mono_samples_and_closes_stream(self):
        self.audio.begin_recording()
        stream = self.module.inputs[-1]
        self.assertEqual(stream.kwargs["samplerate"], 16000)
        self.assertEqual(stream.kwargs["channels"], 1)
        self.assertEqual(stream.kwargs["dtype"], "float32")
        self.assertTrue(self.audio.recording)
        data = np.array([[0.1],[0.2]], dtype="float32")
        self.capture(data)
        data.fill(0)
        result = self.audio.finish_recording()
        np.testing.assert_array_equal(result, np.array([0.1,0.2], dtype="float32"))
        self.assertTrue(stream.aborted and stream.closed)
        self.assertFalse(self.audio.recording)

    def test_capture_has_hard_limit_and_never_closes_inside_callback(self):
        with patch("companion.audio_io.MAX_CAPTURE_FRAMES", 5):
            self.audio.begin_recording()
            stream = self.module.inputs[-1]
            with self.assertRaises(CallbackStop):
                self.capture(np.ones((8,1), dtype="float32"))
            self.assertFalse(self.audio.recording)
            self.assertFalse(stream.closed)
            self.assertEqual(len(self.audio.finish_recording()), 5)
            self.assertTrue(stream.closed)

    def test_revoked_capture_discards_new_and_prior_samples(self):
        self.audio.begin_recording()
        self.capture(np.ones((2,1), dtype="float32"))
        self.policy.revoke("microphone.capture")
        with self.assertRaises(CallbackStop):
            self.capture(np.ones((2,1), dtype="float32"))
        self.assertFalse(self.audio.recording)
        with self.assertRaises(Denied):
            self.audio.finish_recording()
        self.assertTrue(self.module.inputs[-1].closed)

    def test_private_stopper_cancels_owned_capture_and_discards_data(self):
        self.policy.register_stopper(self.audio.cancel)
        self.audio.begin_recording()
        self.capture(np.ones((2,1), dtype="float32"))
        self.policy.enter_private()
        self.assertTrue(self.module.inputs[-1].closed)
        self.assertFalse(self.audio.recording)
        with self.assertRaises(AudioError):
            self.audio.finish_recording()
        with self.assertRaises(Denied):
            self.audio.begin_recording()

    def test_revocation_during_start_gate_prevents_hardware_open(self):
        original = self.policy.publish
        def revoke(lease, effect):
            self.policy.revoke("microphone.capture")
            return original(lease, effect)
        with patch.object(self.policy, "publish", side_effect=revoke):
            with self.assertRaises(Denied):
                self.audio.begin_recording()
        self.assertEqual(self.module.inputs, [])

    def test_playback_stream_copies_and_clips_samples(self):
        data = np.array([0.5,2.0], dtype="float32")
        self.audio.play_samples(data, 22050)
        data.fill(0)
        stream = self.module.outputs[-1]
        output = np.empty((4,1), dtype="float32")
        with self.assertRaises(CallbackStop):
            stream.kwargs["callback"](output, 4, None, None)
        np.testing.assert_array_equal(output[:,0], np.array([0.5,1.0,0,0], dtype="float32"))
        self.assertFalse(self.audio.playing)
        self.assertFalse(stream.closed)
        self.audio.cancel()
        self.assertTrue(stream.closed)

    def test_playback_revocation_produces_only_silence(self):
        self.audio.play_samples(np.ones(10, dtype="float32"), 16000)
        self.policy.revoke("audio.playback")
        output = np.ones((5,1), dtype="float32")
        with self.assertRaises(CallbackStop):
            self.module.outputs[-1].kwargs["callback"](output, 5, None, None)
        np.testing.assert_array_equal(output, np.zeros((5,1), dtype="float32"))

    def test_cancel_closes_only_adapter_streams(self):
        self.audio.begin_recording()
        capture = self.module.inputs[-1]
        self.audio.cancel()
        self.assertTrue(capture.aborted and capture.closed)
        self.audio.play_samples(np.ones(10, dtype="float32"), 16000)
        playback = self.module.outputs[-1]
        self.audio.cancel()
        self.assertTrue(playback.aborted and playback.closed)
        self.audio.cancel()

    def test_bad_samples_and_rate_do_not_open_output(self):
        for data, rate in ((np.array([float("nan")]),16000), (np.ones((3,2)),16000),
                           (np.array([]),16000), (np.ones(2),True), (np.ones(2),1)):
            with self.subTest(rate=rate), self.assertRaises(AudioError):
                self.audio.play_samples(data, rate)
        self.assertEqual(self.module.outputs, [])

    def test_cancel_during_construction_closes_unstarted_stream(self):
        original = self.module.InputStream
        def create(**kwargs):
            stream = original(**kwargs)
            self.audio.cancel()
            return stream
        with patch.object(self.module, "InputStream", side_effect=create):
            with self.assertRaises(AudioError):
                self.audio.begin_recording()
        self.assertFalse(self.module.inputs[-1].started)
        self.assertTrue(self.module.inputs[-1].closed)

    def test_hardware_error_is_safe(self):
        with patch.object(self.module, "InputStream", side_effect=RuntimeError("PRIVATE hardware data")):
            with self.assertRaises(AudioError) as caught:
                self.audio.begin_recording()
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertFalse(self.audio.recording)


if __name__ == "__main__":
    unittest.main()
