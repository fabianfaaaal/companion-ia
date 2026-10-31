"""Synthetic protocol checks; no model downloads, GPU or hardware access."""
import base64
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion import qwen_voice_engine as engine
try:
    import numpy as np
except ImportError:
    np = None


class QwenVoiceEngineTests(unittest.TestCase):
    def setUp(self):
        engine.load_model.cache_clear()

    def tearDown(self):
        engine.load_model.cache_clear()

    def test_protocol_never_echoes_text_or_dependency_exception(self):
        output = io.StringIO()
        with patch.object(engine, "handle", side_effect=RuntimeError("synthetic-secret")):
            engine.serve(io.BytesIO(b'{"text":"synthetic-secret"}\n'), output)
        self.assertEqual(json.loads(output.getvalue()),
                         {"ok": False, "error": "engine_unavailable"})

    def test_invalid_requests_and_unsupported_operations(self):
        output = io.StringIO()
        engine.serve(io.BytesIO(b'[]\nnot json\n{"op":"stt"}\n'), output)
        self.assertEqual([json.loads(line)["error"] for line in output.getvalue().splitlines()],
                         ["invalid_request", "invalid_request", "invalid_operation"])

    def test_oversize_request_is_not_processed(self):
        output = io.StringIO()
        with patch.object(engine, "MAX_REQUEST_BYTES", 16), patch.object(engine, "handle") as handle:
            self.assertEqual(engine.serve(io.BytesIO(b"x" * 17), output), 2)
        handle.assert_not_called()

    def test_empty_and_oversize_text_do_not_load_model(self):
        with patch.object(engine, "load_model") as load:
            for text in (None, "", " ", "x" * 2001):
                with self.assertRaisesRegex(engine.SpeechError, "invalid_text"):
                    engine.handle({"op": "tts", "text": text})
        load.assert_not_called()

    def test_model_must_already_be_present_even_with_remote_input_path(self):
        with patch.object(Path, "is_dir", return_value=False), patch.object(engine, "load_model") as load:
            with self.assertRaisesRegex(engine.SpeechError, "model_missing"):
                engine.handle({"op": "tts", "text": "Synthetic", "voice_path": "https://example.invalid"})
        load.assert_not_called()

    def test_load_uses_local_only_and_native_attention(self):
        constructor = Mock(return_value=object())
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True), bfloat16="bf16", set_num_threads=Mock())
        qwen = SimpleNamespace(Qwen3TTSModel=SimpleNamespace(from_pretrained=constructor))
        with patch.dict("sys.modules", {"torch": torch, "qwen_tts": qwen}):
            result = engine.load_model("synthetic-model-directory")
            self.assertIs(result, engine.load_model("synthetic-model-directory"))
        torch.set_num_threads.assert_called_once_with(4)
        constructor.assert_called_once_with("synthetic-model-directory", device_map="cuda:0",
                                            dtype="bf16", attn_implementation="sdpa",
                                            local_files_only=True)

    def test_missing_cuda_fails_without_silent_remote_or_cpu_fallback(self):
        constructor = Mock()
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        qwen = SimpleNamespace(Qwen3TTSModel=SimpleNamespace(from_pretrained=constructor))
        with patch.dict("sys.modules", {"torch": torch, "qwen_tts": qwen}):
            with self.assertRaisesRegex(engine.SpeechError, "cuda_unavailable"):
                engine.load_model("synthetic")
        constructor.assert_not_called()

    @unittest.skipIf(np is None, "Optional NumPy not installed")
    def test_pcm_contract_uses_spanish_serena_without_style_instruction(self):
        model = SimpleNamespace(generate_custom_voice=Mock(return_value=([np.array([0, 0.5, -0.5])], 24000)))
        with patch.object(engine, "require_model", return_value=Path("synthetic")), \
                patch.object(engine, "load_model", return_value=model):
            result = engine.handle({"op": "tts", "text": "Synthetic phrase"})
        model.generate_custom_voice.assert_called_once_with(
            text="Synthetic phrase", language="Spanish", speaker="Serena", max_new_tokens=384)
        self.assertEqual(result["format"], "s16le")
        self.assertEqual(result["channels"], 1)
        self.assertEqual(len(base64.b64decode(result["pcm"])), 6)

    @unittest.skipIf(np is None, "Optional NumPy not installed")
    def test_invalid_or_long_audio_is_not_published(self):
        for wav, rate in [(np.array([np.nan]), 24000), (np.zeros((1, 2)), 24000),
                          (np.zeros(24000 * 30 + 1), 24000), (np.zeros(1), True)]:
            model = SimpleNamespace(generate_custom_voice=Mock(return_value=([wav], rate)))
            with patch.object(engine, "require_model", return_value=Path("synthetic")), \
                    patch.object(engine, "load_model", return_value=model):
                with self.assertRaises(engine.SpeechError):
                    engine.handle({"op": "tts", "text": "Synthetic phrase"})
