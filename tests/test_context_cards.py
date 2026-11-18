"""Synthetic context cards, keyword resolution and review contracts."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.context_store import ContextStore, normalize_phrase, TEMPLATES
from companion.memory_store import MemoryStoreError
from companion.policy import Policy, Denied, State


class ContextStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "memory.sqlite"
        self.policy = Policy()
        self.store = ContextStore(self.path, self.policy)

    def tearDown(self):
        self.temp.cleanup()

    def persist(self):
        self.policy.grant("memory.read")
        self.policy.grant("memory.write")
        self.policy.set_memory(True)
        self.store.set_enabled(True)

    def card(self, title="Proyecto demo", body="Contenido confirmado sintético", keywords=None, **kwargs):
        return self.store.save_card("Proyectos", title, body, ["proyecto demo", "mi demo"] if keywords is None else keywords, **kwargs)

    def test_templates_empty_and_constructor_lazy(self):
        self.assertIn("Mascotas", TEMPLATES)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.store.list_cards(), [])
        self.assertFalse(self.path.exists())

    def test_off_session_matches_without_disk_and_does_not_carry_to_on(self):
        card = self.card()
        self.assertEqual(self.store.retrieve("Hablemos de mi demo")["matches"][0]["id"], card["id"])
        self.store.set_enabled(False)
        self.assertEqual(len(self.store.list_cards()), 1, "Repeated OFF refresh must preserve current temporary session")
        self.assertFalse(self.path.exists())
        self.persist()
        self.assertEqual(self.store.list_cards(), [])
        self.assertFalse(self.path.exists())

    def test_persistent_reopen_and_versioned_shared_database(self):
        self.persist()
        card = self.card()
        other = ContextStore(self.path, self.policy)
        other.set_enabled(True)
        self.assertEqual(other.list_cards()[0]["id"], card["id"])
        self.assertEqual(other.retrieve("MI DEMO")["matches"][0]["version"], 1)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT version FROM component_versions WHERE name='context_cards'").fetchone()[0], 1)
            self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name='notes'").fetchone())

    def test_memory_revocation_blocks_persistent_read_write(self):
        self.persist()
        self.card()
        self.policy.set_memory(False)
        with self.assertRaises(Denied):
            self.store.list_cards()
        with self.assertRaises(Denied):
            self.card("Otra ficha")

    def test_private_blocks_ram_and_archive_and_clears_explicitly(self):
        self.card()
        self.policy.enter_private()
        with self.assertRaises(Denied):
            self.store.retrieve("mi demo")
        with self.assertRaises(Denied):
            self.card()
        self.store.set_enabled(False)
        self.policy.resume(local_action=True)
        self.assertEqual(self.store.list_cards(), [])

    def test_error_state_blocks_even_ephemeral_cards(self):
        self.policy.register_stopper(lambda: (_ for _ in ()).throw(RuntimeError("synthetic")))
        with self.assertRaises(RuntimeError):
            self.policy.enter_private()
        self.assertEqual(self.policy.state, State.ERROR)
        with self.assertRaises(Denied):
            self.store.list_cards()

    def test_unicode_aliases_are_whole_phrases(self):
        card = self.card(keywords=["Árbol Azul", "mi árbol"])
        for text in ("El ARBOL AZUL está acá", "MI A\u0301RBOL está acá"):
            self.assertEqual(self.store.retrieve(text)["matches"][0]["id"], card["id"])
        self.assertEqual(self.store.retrieve("arbol azulado")["matches"], [])
        self.assertEqual(self.store.retrieve("mi arbolito")["matches"], [])
        self.assertEqual(normalize_phrase("  Café, AZUL "), "cafe azul")

    def test_longest_phrase_wins_only_at_overlapping_position(self):
        short = self.card("Gato genérico", keywords=["gato"])
        long = self.card("Gato de demo", keywords=["gato de demo"])
        matches = self.store.retrieve("gato de demo")["matches"]
        self.assertEqual([match["id"] for match in matches], [long["id"]])
        matches = self.store.retrieve("gato y gato de demo")["matches"]
        self.assertEqual({match["id"] for match in matches}, {short["id"], long["id"]})

    def test_ambiguous_key_never_selects_arbitrary_card(self):
        a = self.card("Proyecto uno", keywords=["demo"])
        b = self.card("Proyecto dos", keywords=["demo"])
        result = self.store.retrieve("demo")
        self.assertEqual(result["matches"], [])
        self.assertEqual({candidate["id"] for candidate in result["ambiguities"][0]["candidates"]}, {a["id"], b["id"]})
        chosen = self.store.retrieve("demo", selections={"demo": b["id"]})
        self.assertEqual(chosen["ambiguities"], [])
        self.assertEqual(chosen["matches"][0]["id"], b["id"])
        self.assertEqual(chosen["matches"][0]["matched_keywords"], ["demo"])
        invalid = self.store.retrieve("demo", selections={"demo": "missing"})
        self.assertTrue(invalid["ambiguities"])

    def test_disabled_deleted_and_edited_bindings_take_effect_immediately(self):
        self.persist()
        card = self.card()
        updated = self.card(card_id=card["id"], expected_version=1, keywords=["nuevo alias"], enabled=False)
        self.assertEqual(self.store.retrieve("mi demo nuevo alias")["matches"], [])
        active = self.card(card_id=card["id"], expected_version=updated["version"], keywords=["nuevo alias"])
        self.assertEqual(self.store.retrieve("mi demo")["matches"], [])
        self.assertEqual(self.store.retrieve("nuevo alias")["matches"][0]["version"], active["version"])
        self.store.delete_card(card["id"])
        self.assertEqual(self.store.retrieve("nuevo alias")["matches"], [])

    def test_stale_card_edit_does_not_overwrite_newer_version(self):
        self.persist()
        card = self.card()
        self.card(body="Cambió explícitamente", card_id=card["id"], expected_version=1)
        with self.assertRaises(ValueError):
            self.card(body="Cambio viejo", card_id=card["id"], expected_version=1)
        self.assertEqual(self.store.list_cards()[0]["body"], "Cambió explícitamente")

    def test_learning_pending_does_not_change_card_and_can_be_edited_before_accept(self):
        for persistent in (False, True):
            if persistent:
                self.persist()
            card = self.card()
            proposal = self.store.propose(card["id"], "Texto no verificado", source_ref="Cita sintética elegida por usuario")
            self.assertEqual(self.store.retrieve("mi demo")["matches"][0]["body"], card["body"])
            self.store.review(proposal["id"], accept=True, edited_body="Texto corregido y confirmado")
            revised = self.store.list_cards()[0]
            self.assertEqual(revised["body"], "Texto corregido y confirmado")
            self.assertEqual(revised["version"], 2)
            self.assertEqual(self.store.list_proposals(), [])
            self.assertEqual(self.store.list_proposals(include_reviewed=True)[0]["status"], "accepted")
            self.store.delete_card(card["id"])

    def test_rejected_proposal_deduplicates_and_keeps_card_unchanged(self):
        self.persist()
        card = self.card()
        proposal = self.store.propose(card["id"], "No confirmado", source_ref="Mensaje sintético")
        self.store.review(proposal["id"], accept=False)
        again = self.store.propose(card["id"], "No confirmado", source_ref="Otra fuente")
        self.assertEqual(again["id"], proposal["id"])
        self.assertEqual(again["status"], "rejected")
        self.assertEqual(self.store.list_proposals(), [])
        self.assertEqual(self.store.list_cards()[0]["body"], card["body"])

    def test_proposal_conflict_requires_new_review_and_retains_previous_revision(self):
        self.persist()
        card = self.card()
        proposal = self.store.propose(card["id"], "Propuesta antigua", source_ref="Mensaje sintético")
        self.card(body="Nueva declaración", card_id=card["id"], expected_version=1)
        with self.assertRaises(ValueError):
            self.store.review(proposal["id"], accept=True)
        self.assertEqual(self.store.list_cards()[0]["body"], "Nueva declaración")
        self.assertEqual(self.store.list_proposals()[0]["status"], "proposed")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM context_revisions").fetchone()[0], 2)

    def test_card_delete_cascades_proposals_aliases_and_revisions(self):
        self.persist()
        card = self.card()
        self.store.propose(card["id"], "Cambio", source_ref="Fuente sintética")
        self.store.delete_card(card["id"])
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for table in ("context_cards", "context_bindings", "context_proposals", "context_revisions"):
                self.assertEqual(connection.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)

    def test_unknown_component_version_rejected(self):
        self.persist()
        self.card()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE component_versions SET version=99 WHERE name='context_cards'")
        with self.assertRaises(MemoryStoreError):
            self.store.list_cards()

    def test_invalid_fields_and_queries_rejected_without_io(self):
        for keywords in ([], ["!!!"], [" ".join(["word"] * 17)]):
            with self.assertRaises(ValueError):
                self.card(keywords=keywords)
        with self.assertRaises(ValueError):
            self.store.save_card("No existe", "Título", "Body", ["clave"])
        with self.assertRaises(ValueError):
            self.store.retrieve("x" * 4001)
        self.assertFalse(self.path.exists())


try:
    from PySide6.QtWidgets import QApplication, QDialog
    from companion.context_panel import ContextPanel
    QT = True
except ImportError:
    QT = False


@unittest.skipUnless(QT, "Qt not installed")
class ContextPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.policy = Policy()
        self.panel = ContextPanel(self.policy, Path(self.temp.name))

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_empty_templates_create_no_records_and_repeated_off_keeps_ram(self):
        self.assertEqual(self.panel.listing.count(), 0)
        card = self.panel.store.save_card("Temas", "Sintético", "Tema de prueba", ["tema demo"])
        self.panel.set_enabled(False)
        self.panel.refresh(True)
        self.assertEqual(self.panel.retrieve("tema demo")["matches"][0]["id"], card["id"])
        self.assertFalse((Path(self.temp.name) / "memory.sqlite").exists())

    def test_private_refresh_erases_display_and_temporary_proposals(self):
        card = self.panel.store.save_card("Temas", "Sintético", "Texto temporal", ["demo"])
        self.panel.store.propose(card["id"], "Texto propuesto", source_ref="Fuente sintética")
        self.panel.reload()
        self.assertEqual(self.panel.listing.count(), 1)
        self.policy.enter_private()
        self.panel.refresh(False)
        self.assertEqual(self.panel.listing.count(), 0)
        self.assertEqual(self.panel.proposals.count(), 0)
        self.assertEqual(self.panel.detail.toPlainText(), "")
        self.policy.resume(local_action=True)
        self.assertEqual(self.panel.store.list_cards(), [])

    def test_private_during_dialog_never_publishes_new_content(self):
        def enter_private(*args):
            self.policy.enter_private()
            self.panel.stop()
            return QDialog.DialogCode.Accepted
        with patch("companion.context_panel.QDialog.exec", side_effect=enter_private):
            self.assertIsNone(self.panel._edit_dialog())
        self.assertFalse((Path(self.temp.name) / "memory.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
