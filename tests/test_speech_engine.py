import base64
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion import speech_engine as engine


class SpeechEngineTests(unittest.TestCase):
    def setUp(self):
        engine.load_voice.cache_clear()

    def tearDown(self):
        engine.load_voice.cache_clear()

    def test_invalid_requests_never_echo_content(self):
        source = io.BytesIO(b'{"private":"synthetic-secret","op":"bad"}\nnot json\n[]\n')
        output = io.StringIO()
        self.assertEqual(engine.serve(source, output), 0)
        self.assertNotIn("synthetic-secret", output.getvalue())
        self.assertEqual([json.loads(line)["error"] for line in output.getvalue().splitlines()],
                         ["invalid_operation", "invalid_request", "invalid_request"])

    def test_oversize_request_exits_without_processing(self):
        output = io.StringIO()
        with patch.object(engine, "MAX_REQUEST_BYTES", 16), patch.object(engine, "handle") as handle:
            self.assertEqual(engine.serve(io.BytesIO(b"x" * 17), output), 2)
        handle.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["error"], "request_too_large")

    def test_generic_failure_does_not_expose_exception(self):
        output = io.StringIO()
        with patch.object(engine, "handle", side_effect=RuntimeError("synthetic-secret")):
            engine.serve(io.BytesIO(b'{}\n'), output)
        self.assertEqual(json.loads(output.getvalue()), {"ok": False, "error": "engine_unavailable"})

    def test_dependency_output_is_not_protocol(self):
        def noisy(request):
            print("synthetic-secret")
            return {"ok": True}
        output = io.StringIO()
        with patch.object(engine, "handle", side_effect=noisy):
            engine.serve(io.BytesIO(b'{}\n'), output)
        self.assertEqual(output.getvalue().strip(), '{"ok":true}')

    def test_text_limits_checked_before_loading_model(self):
        for text in (None, "", " ", "x" * 2001):
            with self.subTest(text_type=type(text)), self.assertRaisesRegex(engine.SpeechError, "invalid_text"):
                engine.handle({"op": "tts", "text": text})

    def test_local_model_required_no_implicit_download(self):
        for path in ("tiny", "https://example.invalid/model", ""):
            with self.subTest(path=path), self.assertRaisesRegex(engine.SpeechError, "model_missing"):
                engine.handle({"op": "tts", "text": "Hola", "voice_path": path})

    def test_tts_chunk_api_and_pcm_contract(self):
        path = Path(__file__).resolve().parent / "synthetic-voice.onnx"
        with patch.object(Path, "is_file", return_value=True):
            chunk = SimpleNamespace(sample_width=2, sample_channels=1, sample_rate=22050,
                                    audio_int16_bytes=b"\x00\x00\x01\x00")
            voice = SimpleNamespace(synthesize=lambda text: iter([chunk, chunk]))
            fake = SimpleNamespace(PiperVoice=SimpleNamespace(load=lambda *a, **kw: voice))
            with patch.dict("sys.modules", {"piper": fake}):
                result = engine.handle({"op": "tts", "text": "Hola", "voice_path": str(path)})
            self.assertEqual(result["format"], "s16le")
            self.assertEqual(result["rate"], 22050)
            self.assertEqual(base64.b64decode(result["pcm"]), chunk.audio_int16_bytes * 2)

    def test_tts_duration_is_bounded(self):
        path = Path(__file__).resolve().parent / "synthetic-voice.onnx"
        with patch.object(Path, "is_file", return_value=True):
            chunk = SimpleNamespace(sample_width=2, sample_channels=1, sample_rate=8000,
                                    audio_int16_bytes=b"\0" * (8000 * 2 * 30 + 2))
            fake = SimpleNamespace(PiperVoice=SimpleNamespace(load=lambda *a, **kw:
                                   SimpleNamespace(synthesize=lambda text: iter([chunk]))))
            with patch.dict("sys.modules", {"piper": fake}), self.assertRaisesRegex(engine.SpeechError, "audio_too_long"):
                engine.handle({"op": "tts", "text": "Hola", "voice_path": str(path)})

    def test_voice_cache_does_not_skip_file_checks_or_store_audio(self):
        path = Path(__file__).resolve().parent / "synthetic-voice.onnx"
        chunk = SimpleNamespace(sample_width=2, sample_channels=1, sample_rate=22050,
                                audio_int16_bytes=b"\x00\x00")
        synthesize = Mock(side_effect=lambda text: iter([chunk]))
        loader = Mock(return_value=SimpleNamespace(synthesize=synthesize))
        fake = SimpleNamespace(PiperVoice=SimpleNamespace(load=loader))
        with patch.dict("sys.modules", {"piper": fake}), patch.object(Path, "is_file", return_value=True):
            for text in ("Primera frase", "Segunda frase"):
                engine.handle({"op": "tts", "text": text, "voice_path": str(path)})
        loader.assert_called_once()
        self.assertEqual(synthesize.call_count, 2)
        with patch.object(Path, "is_file", return_value=False), self.assertRaisesRegex(engine.SpeechError, "model_missing"):
            engine.handle({"op": "tts", "text": "Tercera frase", "voice_path": str(path)})

    def test_tuned_session_uses_bounded_cpu_threads(self):
        path = str(Path(__file__).resolve().parent / "synthetic-voice.onnx")
        options = SimpleNamespace()
        session = Mock(return_value=object())
        config = object()
        factory = Mock(return_value=object())
        modules = {"piper": SimpleNamespace(PiperVoice=factory),
                   "piper.config": SimpleNamespace(PiperConfig=SimpleNamespace(from_dict=lambda value: config)),
                   "piper.phonemize_espeak": SimpleNamespace(ESPEAK_DATA_DIR="synthetic-espeak"),
                   "onnxruntime": SimpleNamespace(SessionOptions=lambda: options, InferenceSession=session)}
        with patch.dict("sys.modules", modules), patch("builtins.open", mock_open(read_data="{}")):
            first = engine.load_voice(path)
            self.assertIs(engine.load_voice(path), first)
        self.assertEqual(options.intra_op_num_threads, 2)
        self.assertEqual(options.inter_op_num_threads, 1)
        session.assert_called_once_with(path, sess_options=options, providers=["CPUExecutionProvider"])
        factory.assert_called_once_with(config=config, session=session.return_value,
                                        espeak_data_dir="synthetic-espeak", download_dir=Path(path).parent)

    def test_stt_rejects_bad_rate_and_audio_before_import(self):
        for request in ({"rate": 44100, "pcm": "AA=="}, {"rate": True, "pcm": "AA=="},
                        {"rate": 16000, "pcm": "invalid%%%"}, {"rate": 16000, "pcm": ""},
                        {"rate": 16000, "pcm": "AA=="}):
            with self.subTest(request=request), self.assertRaises(engine.SpeechError):
                engine.handle({"op": "stt", **request})

    def test_stt_uses_local_cpu_int8_model_and_array(self):
        samples = object()
        finite = SimpleNamespace(all=lambda: True)
        class Absolute:
            def __gt__(self, value):
                return SimpleNamespace(any=lambda: False)
        numpy = SimpleNamespace(frombuffer=Mock(return_value=samples),
                                isfinite=lambda value: finite, abs=lambda value: Absolute())
        model = SimpleNamespace(transcribe=Mock(return_value=(iter([
            SimpleNamespace(text=" Hola "), SimpleNamespace(text=" mundo ")]), None)))
        constructor = Mock(return_value=model)
        path = Path(__file__).resolve().parent / "synthetic-whisper"
        with patch.dict("sys.modules", {"numpy": numpy, "faster_whisper": SimpleNamespace(WhisperModel=constructor)}), \
                patch.object(Path, "is_file", return_value=True), patch.object(Path, "is_dir", return_value=True):
            result = engine.handle({"op": "stt", "rate": 16000, "pcm": "AAAAAA==", "model_path": str(path)})
        self.assertEqual(result, {"ok": True, "op": "stt", "text": "Hola mundo"})
        constructor.assert_called_once_with(str(path), device="cpu", compute_type="int8", cpu_threads=4,
                                            local_files_only=True)
        model.transcribe.assert_called_once_with(samples, language="es", beam_size=1,
                                                  condition_on_previous_text=False, vad_filter=False)

    def test_stt_rejects_nonfinite_before_loading_model(self):
        numpy = SimpleNamespace(frombuffer=lambda *a, **kw: object(),
                                isfinite=lambda value: SimpleNamespace(all=lambda: False))
        constructor = Mock()
        path = Path(__file__).resolve().parent / "synthetic-whisper"
        with patch.dict("sys.modules", {"numpy": numpy, "faster_whisper": SimpleNamespace(WhisperModel=constructor)}), \
                patch.object(Path, "is_file", return_value=True), patch.object(Path, "is_dir", return_value=True), \
                self.assertRaisesRegex(engine.SpeechError, "invalid_audio"):
            engine.handle({"op": "stt", "rate": 16000, "pcm": "AAAAAA==", "model_path": str(path)})
        constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
