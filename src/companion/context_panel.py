"""Context cards and explicit review UI. Empty templates never invent facts."""
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)
from .context_store import ContextStore, TEMPLATES
from .memory_store import MemoryStoreError
from .policy import Denied, State


class ContextPanel(QWidget):
    changed = Signal()
    notice = Signal(str)

    def __init__(self, policy, data_dir, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.store = ContextStore(Path(data_dir) / "memory.sqlite", policy)
        self._busy = False
        self._dialogs = set()
        layout = QVBoxLayout(self)
        intro = QLabel("Las fichas reúnen contexto que elegís compartir. Una palabra o frase clave recupera la ficha completa; "
                       "los alias tienen el mismo significado. Si una clave apunta a varias fichas, se pide aclaración. "
                       "Las categorías son plantillas vacías: no contienen datos sobre vos.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.mode_note = QLabel("Memoria apagada: fichas y propuestas temporales, solo para esta sesión.")
        self.mode_note.setWordWrap(True)
        layout.addWidget(self.mode_note)
        row = QHBoxLayout()
        self.new_button = QPushButton("Nueva ficha")
        self.new_button.clicked.connect(self.new_card)
        self.edit_button = QPushButton("Editar ficha")
        self.edit_button.clicked.connect(self.edit_card)
        self.delete_button = QPushButton("Borrar ficha")
        self.delete_button.clicked.connect(self.delete_card)
        for widget in (self.new_button, self.edit_button, self.delete_button):
            row.addWidget(widget)
        layout.addLayout(row)
        self.listing = QListWidget()
        self.listing.itemSelectionChanged.connect(self._show_card)
        self.listing.itemDoubleClicked.connect(lambda _: self.edit_card())
        layout.addWidget(self.listing, 1)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setAcceptRichText(False)
        layout.addWidget(self.detail, 1)
        proposals_title = QLabel("Aprendizajes por revisar")
        layout.addWidget(proposals_title)
        self.proposals = QListWidget()
        self.proposals.itemDoubleClicked.connect(lambda _: self.review_proposal())
        layout.addWidget(self.proposals, 1)
        row = QHBoxLayout()
        self.propose_button = QPushButton("Proponer cambio para la ficha")
        self.propose_button.clicked.connect(lambda: self.propose_text(""))
        self.review_button = QPushButton("Revisar propuesta")
        self.review_button.clicked.connect(self.review_proposal)
        row.addWidget(self.propose_button)
        row.addWidget(self.review_button)
        layout.addLayout(row)
        note = QLabel("Proponer no guarda un hecho confirmado. Revisá texto, fuente y diferencias antes de aceptar. "
                      "La aceptación reemplaza el contenido de esa ficha y conserva su revisión anterior; "
                      "rechazar impide que la misma propuesta reaparezca. Una ficha nunca concede permisos ni ejecuta acciones.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.refresh(policy.state is State.ACTIVE)

    def set_enabled(self, value):
        before = self.store.enabled
        self.store.set_enabled(value)
        if before != self.store.enabled:
            self.reload()
        self.mode_note.setText("Memoria activa: fichas y propuestas se conservan en el archivo local." if self.store.enabled
                               else "Memoria apagada: fichas y propuestas temporales; se descartan al activar memoria, cerrar o entrar en privado.")

    def refresh(self, active, busy=False):
        self._busy = bool(busy)
        allowed = bool(active) and self.policy.state is State.ACTIVE
        for widget in (self.new_button, self.edit_button, self.delete_button, self.propose_button, self.review_button):
            widget.setEnabled(allowed and not busy)
        self.listing.setEnabled(allowed)
        self.proposals.setEnabled(allowed)
        if not allowed:
            self.stop()

    def stop(self):
        for dialog in tuple(self._dialogs):
            dialog.reject()
        self.store.clear_session()
        self.listing.clear()
        self.proposals.clear()
        self.detail.clear()

    def _run_dialog(self, dialog):
        self._dialogs.add(dialog)
        try:
            return dialog.exec() == QDialog.DialogCode.Accepted and self.policy.state is State.ACTIVE
        finally:
            self._dialogs.discard(dialog)

    @staticmethod
    def _dispose_dialog(dialog):
        # Closed Qt dialogs otherwise retain hidden copies of user content.
        for widget in dialog.findChildren(QLineEdit):
            widget.clear()
        for widget in dialog.findChildren(QTextEdit):
            widget.clear()
        for widget in dialog.findChildren(QComboBox):
            widget.blockSignals(True)
            widget.clear()
        for widget in dialog.findChildren(QLabel):
            widget.clear()
        dialog.deleteLater()

    def _allowed(self):
        return self.policy.state is State.ACTIVE and not self._busy

    def _failure(self, exc):
        self.notice.emit(str(exc))

    def reload(self):
        if self.policy.state is not State.ACTIVE:
            self.stop()
            return
        selected = self.listing.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole)["id"] if selected else None
        try:
            cards = self.store.list_cards()
            proposals = self.store.list_proposals()
        except (MemoryStoreError, ValueError, Denied) as exc:
            self._failure(exc)
            return
        self.listing.clear()
        titles = {card["id"]: card["title"] for card in cards}
        for card in cards:
            item = QListWidgetItem(("" if card["enabled"] else "[Desactivada] ") + card["title"] + " · " + card["category"])
            item.setData(Qt.ItemDataRole.UserRole, card)
            self.listing.addItem(item)
            if card["id"] == selected_id:
                self.listing.setCurrentItem(item)
        self.proposals.clear()
        for proposal in proposals:
            item = QListWidgetItem(titles.get(proposal["card_id"], "Ficha eliminada") + " · " + proposal["body"][:80])
            item.setData(Qt.ItemDataRole.UserRole, proposal)
            self.proposals.addItem(item)
        self._show_card()

    def _show_card(self):
        item = self.listing.currentItem()
        if item is None:
            self.detail.clear()
            return
        card = item.data(Qt.ItemDataRole.UserRole)
        self.detail.setPlainText(card["title"] + "\n\n" + card["body"] + "\n\nClaves y alias: " + ", ".join(card["keywords"])
                                 + "\nFuente: " + card["source_ref"] + "\nEstado: confirmado por el usuario · versión " + str(card["version"]))

    def _edit_dialog(self, previous=None):
        dialog = QDialog(self)
        dialog.setWindowTitle("Editar ficha" if previous else "Nueva ficha de contexto")
        dialog.resize(620, 630)
        layout = QVBoxLayout(dialog)
        category = QComboBox()
        category.addItems(TEMPLATES)
        title = QLineEdit()
        title.setMaxLength(120)
        keywords = QLineEdit()
        keywords.setPlaceholderText("Frases o alias separados por coma; ejemplo ficticio: proyecto demo, mi demo")
        body = QTextEdit()
        body.setAcceptRichText(False)
        source = QLineEdit("Declaración confirmada por el usuario")
        source.setMaxLength(500)
        enabled = QCheckBox("Usar esta ficha al detectar sus claves")
        enabled.setChecked(True)
        hint = QLabel()
        hint.setWordWrap(True)
        def update_hint():
            hint.setText(TEMPLATES[category.currentText()])
        category.currentTextChanged.connect(update_hint)
        update_hint()
        for label, widget in (("Categoría", category), ("Título", title), ("Palabras/frases clave y alias", keywords),
                              ("Contenido confirmado", body), ("Fuente o procedencia", source)):
            layout.addWidget(QLabel(label))
            layout.addWidget(widget)
            if widget is category:
                layout.addWidget(hint)
        layout.addWidget(enabled)
        if previous:
            category.setCurrentText(previous["category"])
            title.setText(previous["title"])
            keywords.setText(", ".join(previous["keywords"]))
            body.setPlainText(previous["body"])
            source.setText(previous["source_ref"])
            enabled.setChecked(previous["enabled"])
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if self._run_dialog(dialog):
            try:
                card = self.store.save_card(category.currentText(), title.text(), body.toPlainText(),
                    [key.strip() for key in keywords.text().split(",") if key.strip()],
                    source_ref=source.text(), card_id=previous["id"] if previous else None,
                    expected_version=previous["version"] if previous else None, enabled=enabled.isChecked())
                self.reload()
                self.changed.emit()
                self.notice.emit("Ficha confirmada y " + ("guardada." if self.store.enabled else "disponible solo en esta sesión."))
                self._dispose_dialog(dialog)
                return card
            except (MemoryStoreError, ValueError, Denied) as exc:
                self._failure(exc)
        self._dispose_dialog(dialog)
        return None

    def new_card(self):
        if self._allowed():
            self._edit_dialog()

    def edit_card(self):
        if self._allowed() and self.listing.currentItem():
            self._edit_dialog(self.listing.currentItem().data(Qt.ItemDataRole.UserRole))

    def delete_card(self):
        if not self._allowed() or self.listing.currentItem() is None:
            return
        card = self.listing.currentItem().data(Qt.ItemDataRole.UserRole)
        if QMessageBox.question(self, "Borrar ficha", "¿Borrar esta ficha, claves, propuestas y revisiones del archivo actual? Las copias exportadas se conservan.") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete_card(card["id"])
            self.reload()
            self.changed.emit()
        except (MemoryStoreError, ValueError, Denied) as exc:
            self._failure(exc)

    def retrieve(self, text, selections=None):
        return self.store.retrieve(text, selections=selections)

    def propose_text(self, text, source_ref="Texto seleccionado por el usuario; contenido por verificar"):
        if not self._allowed():
            return
        try:
            cards = self.store.list_cards()
        except (MemoryStoreError, Denied) as exc:
            self._failure(exc)
            return
        if not cards:
            self.notice.emit("Primero creá una ficha para revisar qué información querés incorporar.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Proponer aprendizaje · Todavía sin confirmar")
        dialog.resize(620, 490)
        layout = QVBoxLayout(dialog)
        targets = QComboBox()
        selected = self.listing.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole)["id"] if selected else None
        for card in cards:
            targets.addItem(card["title"], card)
            if card["id"] == selected_id:
                targets.setCurrentIndex(targets.count() - 1)
        layout.addWidget(QLabel("Ficha que actualizaría la propuesta"))
        layout.addWidget(targets)
        info = QLabel("Escribí el contenido completo que debería tener la ficha. Esto crea una propuesta pendiente; después podés editarla, aceptarla o rechazarla.")
        info.setWordWrap(True)
        layout.addWidget(info)
        body = QTextEdit()
        body.setAcceptRichText(False)
        body.setPlainText(text[:8000])
        layout.addWidget(body)
        source = QLineEdit(source_ref[:500])
        source.setMaxLength(500)
        layout.addWidget(QLabel("Fuente o procedencia (no convierte el texto en hecho)"))
        layout.addWidget(source)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Crear propuesta para revisar")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if self._run_dialog(dialog):
            try:
                proposal = self.store.propose(targets.currentData()["id"], body.toPlainText(), source_ref=source.text())
                self.reload()
                self.notice.emit("Propuesta pendiente de revisión." if proposal["status"] == "proposed" else "Esta propuesta ya fue revisada; no se creó otra copia.")
            except (MemoryStoreError, ValueError, Denied) as exc:
                self._failure(exc)

        self._dispose_dialog(dialog)

    def review_proposal(self):
        if not self._allowed() or self.proposals.currentItem() is None:
            return
        proposal = self.proposals.currentItem().data(Qt.ItemDataRole.UserRole)
        try:
            card = next((card for card in self.store.list_cards() if card["id"] == proposal["card_id"]), None)
        except (MemoryStoreError, Denied) as exc:
            self._failure(exc)
            return
        if card is None:
            self.reload()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Revisar aprendizaje · " + card["title"])
        dialog.resize(680, 650)
        layout = QVBoxLayout(dialog)
        info = QLabel("Fuente: " + proposal["source_ref"] + "\nPropuesta sobre versión " + str(proposal["base_version"]) + "; ficha actual " + str(card["version"]) + ". Aceptar reemplaza el contenido por el texto revisado.")
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addWidget(QLabel("Contenido actual"))
        current = QTextEdit()
        current.setReadOnly(True)
        current.setPlainText(card["body"])
        layout.addWidget(current)
        layout.addWidget(QLabel("Propuesta editable"))
        proposed = QTextEdit()
        proposed.setAcceptRichText(False)
        proposed.setPlainText(proposal["body"])
        layout.addWidget(proposed)
        row = QHBoxLayout()
        accept = QPushButton("Aceptar texto revisado")
        reject = QPushButton("Rechazar propuesta")
        cancel = QPushButton("Cancelar")
        decision = {"accept": None}
        def choose(value):
            decision["accept"] = value
            dialog.accept()
        accept.clicked.connect(lambda: choose(True))
        reject.clicked.connect(lambda: choose(False))
        cancel.clicked.connect(dialog.reject)
        for button in (accept, reject, cancel):
            row.addWidget(button)
        layout.addLayout(row)
        if self._run_dialog(dialog) and decision["accept"] is not None:
            try:
                self.store.review(proposal["id"], accept=decision["accept"], edited_body=proposed.toPlainText() if decision["accept"] else None)
                self.reload()
                self.changed.emit()
                self.notice.emit("Aprendizaje aceptado y ficha actualizada." if decision["accept"] else "Propuesta rechazada; la ficha no cambió.")
            except (MemoryStoreError, ValueError, Denied) as exc:
                self._failure(exc)
        self._dispose_dialog(dialog)
