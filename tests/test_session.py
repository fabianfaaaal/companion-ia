import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.session import build_messages, final_answer

class SessionTests(unittest.TestCase):
    def test_session_bounds_and_current_message(self):
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 1000} for i in range(40)]
        result = build_messages(history, "hello", "Amiga", "Trabajo")
        self.assertLessEqual(len(result), 14)
        self.assertEqual(result[-1], {"role": "user", "content": "hello"})
        self.assertIn("trabajar", result[0]["content"])
    def test_empty_and_oversize_are_rejected(self):
        for text in (" ", "x" * 4001):
            with self.assertRaises(ValueError): build_messages([], text, "", "Charla")
    def test_thought_markup_is_not_a_final_answer(self):
        self.assertEqual(final_answer("hidden</think>Hola"), "Hola")
        with self.assertRaises(ValueError): final_answer("<think>unfinished")
        with self.assertRaises(ValueError): final_answer("hidden</think>")
    def test_name_is_bounded(self):
        result = build_messages([], "hola", "A" * 500, "Charla")
        self.assertNotIn("A" * 41, result[0]["content"])
