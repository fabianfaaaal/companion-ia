"""Explicit manual-note consent and UI. No automatic chat persistence."""
from pathlib import Path
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)
from companion.memory_store import MemoryStore, MemoryStoreError
from companion.policy import Denied, State


class MemoryPanel(QWidget):
    changed = Signal(bool)

    def __init__(self, policy, data_dir: Path, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.enabled = False
        self.store = MemoryStore(data_dir / "memory.sqlite", policy)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = QCheckBox("Memoria local")
        self.toggle.toggled.connect(self._toggle)
        self.save = QPushButton("Guardar nota")
        self.save.clicked.connect(self.save_dialog)
        self.browse = QPushButton("Ver / borrar notas")
        self.browse.clicked.connect(self.browse_dialog)
        self.info = QLabel("Notas, avisos y fotos solo al confirmar.")
        self.info.setObjectName("muted")
        for widget in (self.toggle, self.save, self.browse, self.info):
            layout.addWidget(widget)
        self.refresh()

    def refresh(self, busy=False):
        active = self.policy.state is State.ACTIVE
        self.toggle.setEnabled(active)
        self.save.setEnabled(active and self.enabled and not busy)
        self.browse.setEnabled(active and self.enabled and not busy)

    def _toggle(self, value):
        if value:
            answer = QMessageBox.question(self, "Activar memoria de notas",
                "Habilita el archivo local de notas, agenda y fotos. Cada alta requiere confirmación y podrá usarse como contexto descriptivo. "
                "No se copia el chat automáticamente ni se importa esta sesión. "
                "Este prototipo no cifra las notas: usá información general. "
                "Desactivar memoria limpia la charla actual, pero conserva las notas. ¿Activar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self.toggle.blockSignals(True)
                self.toggle.setChecked(False)
                self.toggle.blockSignals(False)
                return
        self.set_enabled(value)

    def set_enabled(self, value):
        if self.policy.state is not State.ACTIVE:
            return
        if value:
            self.policy.grant("memory.read")
            self.policy.grant("memory.write")
        self.policy.set_memory(value)
        if not value:
            self.policy.revoke("memory.read")
            self.policy.revoke("memory.write")
        self.enabled = value
        self.toggle.blockSignals(True)
        self.toggle.setChecked(value)
        self.toggle.blockSignals(False)
        self.changed.emit(value)
        self.refresh()

    def deactivate(self):
        if self.policy.state is State.ACTIVE and self.enabled:
            self.set_enabled(False)

    def retrieve(self, query):
        if not self.enabled:
            return []
        return self.store.search(query, limit=3)

    def _error(self):
        QMessageBox.warning(self, "Memoria", "No se pudo completar la operación de memoria. No se confirma ningún cambio.")

    def save_dialog(self):
        if not self.enabled or self.policy.state is not State.ACTIVE:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Guardar nota · Acción manual")
        dialog.resize(580, 440)
        layout = QVBoxLayout(dialog)
        label = QLabel("Escribí qué querés conservar. No se programa ningún recordatorio y no se copia el chat.")
        label.setWordWrap(True)
        layout.addWidget(label)
        title = QLineEdit()
        title.setMaxLength(120)
        title.setPlaceholderText("Título")
        body = QTextEdit()
        body.setPlaceholderText("Nota general que querés guardar…")
        layout.addWidget(title)
        layout.addWidget(body)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.store.save_note(title.text(), body.toPlainText())
            except (ValueError, MemoryStoreError, Denied):
                self._error()
            else:
                QMessageBox.information(self, "Nota guardada", "La nota quedó guardada en esta PC. No se creó un aviso futuro.")

    def browse_dialog(self):
        if not self.enabled or self.policy.state is not State.ACTIVE:
            return
        try:
            notes = self.store.list_notes()
        except (MemoryStoreError, Denied):
            self._error()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Notas guardadas · Solo memoria manual")
        dialog.resize(680, 550)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        detail = QTextEdit()
        detail.setReadOnly(True)
        def fill():
            listing.clear()
            for note in notes:
                listing.addItem(note["title"])
            detail.clear()
        def selected(row):
            if 0 <= row < len(notes):
                note = notes[row]
                detail.setPlainText(note["created_at"] + "\n\n" + note["body"])
        def delete():
            row = listing.currentRow()
            if not 0 <= row < len(notes):
                return
            if QMessageBox.question(dialog, "Borrar nota", "¿Borrar esta nota y su índice de búsqueda?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            try:
                self.store.delete_note(notes[row]["id"])
                notes.pop(row)
                fill()
                # Removing the note also invalidates conversation-derived context.
                self.changed.emit(self.enabled)
            except (MemoryStoreError, Denied):
                self._error()
        listing.currentRowChanged.connect(selected)
        remove = QPushButton("Borrar nota seleccionada")
        remove.clicked.connect(delete)
        layout.addWidget(listing)
        layout.addWidget(detail, 1)
        layout.addWidget(remove)
        fill()
        dialog.exec()
