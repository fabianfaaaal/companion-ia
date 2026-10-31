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

    def test_unavailable_reminder_is_handled_by_program(self):
        from companion.session import direct_reply
        self.assertIn("No quedó ninguno creado", direct_reply("Crear un recordatorio mañana"))
        self.assertIn("No quedó ninguno creado", direct_reply("Crear unr ecordatorio mañana"))
        self.assertIn("No quedó ninguno creado", direct_reply("Recordame mañana el proyecto"))
        self.assertIsNone(direct_reply("Recordame lo del proyecto semilla"))
        self.assertIsNone(direct_reply("Hola, cómo estás"))
    def test_memory_off_never_injects_notes(self):
        note = {"id": "fake", "title": "Semilla", "body": "dato sintético"}
        off = build_messages([], "hola", "Amiga", "Charla", memories=[note])
        self.assertEqual(len(off), 2)
        on = build_messages([], "hola", "Amiga", "Charla", memories=[note], memory_enabled=True)
        self.assertIn("DATOS RECUPERADOS", on[1]["content"])
        self.assertIn("fake", on[1]["content"])
