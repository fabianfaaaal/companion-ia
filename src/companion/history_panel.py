"""Manual, selected history imports. No API login or automatic harvesting."""
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)
from .history_store import HistoryStore, HistoryFormatError
from .memory_store import MemoryStoreError
from .policy import Denied, State


def conversation_text(record, limit=40000):
    content = "\n\n".join(("Vos (historial):\n" if message["role"] == "user"
                            else "Asistente externo (historial):\n") + message["content"]
                           for message in record["messages"])
    if len(content) > limit:
        return content[:limit] + "\n\n[Vista abreviada; la copia contiene el texto completo importado.]"
    return content


class HistoryPanel(QWidget):
    notice = Signal(str)
    changed = Signal()

    def __init__(self, policy, db_path: Path, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.store = HistoryStore(db_path, policy)
        self.memory_enabled = False
        self._busy = False
        self.items = []
        self._dialogs = set()
        self._preview = []
        self._account_filter = None
        self._scope_loaded = False
        layout = QVBoxLayout(self)
        info = QLabel("Copias locales de conversaciones externas elegidas por vos. Cuenta es un alias: no inicia sesión ni accede al historial del proveedor. Archivo sin cifrar; no importar intimidad en este preview.")
        info.setWordWrap(True)
        layout.addWidget(info)
        row = QHBoxLayout()
        self.import_button = QPushButton("Importar historial JSON")
        self.import_button.clicked.connect(self.import_dialog)
        self.accounts = QComboBox()
        self.accounts.addItem("Todas las cuentas (vista y contexto)", None)
        self.accounts.currentIndexChanged.connect(self._filter_changed)
        self.accounts.activated.connect(lambda _: self._save_scope())
        row.addWidget(self.import_button)
        row.addWidget(self.accounts, 1)
        layout.addLayout(row)
        self.use_context = QCheckBox("Usar los historiales de este filtro como contexto")
        self.use_context.setChecked(True)
        self.use_context.toggled.connect(lambda _: self.changed.emit())
        self.use_context.clicked.connect(lambda _: self._save_scope())
        layout.addWidget(self.use_context)
        self.listing = QListWidget()
        self.listing.itemDoubleClicked.connect(lambda _: self.detail())
        layout.addWidget(self.listing, 1)
        row = QHBoxLayout()
        self.open_button = QPushButton("Abrir conversación")
        self.open_button.clicked.connect(self.detail)
        self.remove_button = QPushButton("Borrar copia seleccionada")
        self.remove_button.clicked.connect(self.delete)
        self.remove_account_button = QPushButton("Borrar copias de esta cuenta")
        self.remove_account_button.clicked.connect(self.delete_account)
        for button in (self.open_button, self.remove_button, self.remove_account_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel("Activá memoria para importar historiales.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _allowed(self):
        return self.memory_enabled and self.policy.state is State.ACTIVE

    def refresh(self, enabled, busy=False):
        self.memory_enabled = bool(enabled)
        self._busy = bool(busy)
        allowed = self._allowed()
        if allowed:
            self.policy.grant("history.read")
            self.policy.grant("history.write")
            if not self._scope_loaded:
                self._restore_scope()
        else:
            self.stop()
        for widget in (self.import_button, self.listing, self.open_button,
                       self.remove_button, self.remove_account_button):
            widget.setEnabled(allowed and not busy)
        # Scope and revocation must remain reachable while inference is running.
        self.accounts.setEnabled(allowed)
        self.use_context.setEnabled(allowed)
        if allowed:
            self.reload()

    def stop(self):
        self.policy.revoke("history.read")
        self.policy.revoke("history.write")
        self._preview.clear()
        for dialog in tuple(self._dialogs):
            for view in dialog.findChildren(QTextEdit):
                view.clear()
            for listing in dialog.findChildren(QListWidget):
                listing.clear()
            dialog.reject()
        self.items = []
        self.listing.clear()
        self.accounts.blockSignals(True)
        self.accounts.clear()
        self.accounts.addItem("Todas las cuentas (vista y contexto)", None)
        self.accounts.blockSignals(False)
        self.status.setText("Historiales cerrados. Las copias guardadas se conservan.")

    def _restore_scope(self):
        if not self._allowed():
            return
        try:
            scope = self.store.load_scope()
        except (MemoryStoreError, HistoryFormatError, Denied):
            scope = dict(provider=None, account=None, use_context=False)
            self.notice.emit("No se pudo leer el alcance de historiales. Su uso como contexto queda desactivado.")
        self._account_filter = (scope["provider"], scope["account"]) if scope["provider"] is not None else None
        self.use_context.blockSignals(True)
        self.use_context.setChecked(scope["use_context"])
        self.use_context.blockSignals(False)
        self._scope_loaded = True

    def _save_scope(self):
        if not self._allowed() or not self._scope_loaded:
            return
        provider, account = self._account_filter if self._account_filter is not None else (None, None)
        try:
            self.store.save_scope(provider, account, self.use_context.isChecked())
        except (MemoryStoreError, HistoryFormatError, Denied):
            self.notice.emit("No se pudo guardar el alcance de historiales. El cambio se aplica solo a esta sesión.")

    def _filter_changed(self):
        selected = self.accounts.currentData()
        self._account_filter = tuple(selected) if selected is not None else None
        self.reload()
        self.changed.emit()

    def reload(self):
        if not self._allowed():
            return
        try:
            selected = self._account_filter
            entries = self.store.list_accounts()
            self.accounts.blockSignals(True)
            self.accounts.clear()
            self.accounts.addItem("Todas las cuentas (vista y contexto)", None)
            for entry in entries:
                pair = (entry["provider"], entry["account"])
                self.accounts.addItem(f"{entry['provider']} / {entry['account']} ({entry['count']})", pair)
                if selected is not None and tuple(selected) == pair:
                    self.accounts.setCurrentIndex(self.accounts.count() - 1)
            if selected is not None and self.accounts.currentData() is None:
                # Do not widen a selected scope to all accounts after deleting
                # its copies or resuming memory/private mode.
                self.accounts.addItem("Cuenta seleccionada sin copias", tuple(selected))
                self.accounts.setCurrentIndex(self.accounts.count() - 1)
            self.accounts.blockSignals(False)
            selected = self.accounts.currentData()
            self.items = self.store.list_conversations(*(tuple(selected) if selected is not None else ()))
            self.listing.clear()
            for record in self.items:
                item = QListWidgetItem(f"{record['title']} · {record['provider']} / {record['account']}")
                item.setData(Qt.ItemDataRole.UserRole, record["id"])
                self.listing.addItem(item)
            self.status.setText(f"Conversaciones visibles: {len(self.items)} (máximo 100 por vista). Las respuestas de asistentes externos no son hechos verificados.")
            self.remove_account_button.setEnabled(selected is not None and not self._busy)
        except (MemoryStoreError, Denied, HistoryFormatError):
            self.notice.emit("No se pudieron leer las copias de historiales.")

    def _current(self):
        index = self.listing.currentRow()
        return self.items[index] if 0 <= index < len(self.items) else None

    def import_dialog(self):
        if not self._allowed() or self._busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Elegí una exportación JSON", "", "Historial JSON (*.json)")
        if not path:
            return
        try:
            self._preview = self.store.preview_file(path)
        except (MemoryStoreError, HistoryFormatError, Denied):
            self.notice.emit("No se pudo previsualizar el historial. Usá JSON ChatGPT compatible o el formato normalizado documentado; máximo 20 MiB.")
            return
        if not self._allowed():
            self._preview.clear()
            return
        dialog = QDialog(self)
        self._dialogs.add(dialog)
        dialog.setWindowTitle("Elegir conversaciones para guardar")
        dialog.resize(850, 650)
        layout = QVBoxLayout(dialog)
        warning = QLabel("Se guardarán únicamente las conversaciones marcadas, sin cifrado. No importes información íntima. Importar no autoriza enviar este archivo completo a una API.")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        provider = QComboBox()
        provider.addItems(["ChatGPT", "DeepSeek", "Otro proveedor"])
        account = QLineEdit()
        account.setMaxLength(80)
        account.setPlaceholderText("Alias local de esta cuenta, por ejemplo Cuenta de prueba")
        row = QHBoxLayout()
        row.addWidget(provider)
        row.addWidget(account, 1)
        layout.addLayout(row)
        listing = QListWidget()
        for index, record in enumerate(self._preview):
            item = QListWidgetItem(record["title"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, index)
            listing.addItem(item)
        layout.addWidget(listing, 1)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setMaximumHeight(180)
        layout.addWidget(view)
        def show_preview(index):
            view.setPlainText(conversation_text(self._preview[index]) if 0 <= index < len(self._preview) else "")
        listing.currentRowChanged.connect(show_preview)
        all_button = QPushButton("Marcar todas")
        all_button.clicked.connect(lambda: [listing.item(index).setCheckState(Qt.CheckState.Checked) for index in range(listing.count())])
        layout.addWidget(all_button)
        consent = QCheckBox("Confirmo guardar las conversaciones marcadas en memoria local sin cifrar")
        layout.addWidget(consent)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        def validate():
            checked = any(listing.item(index).checkState() == Qt.CheckState.Checked for index in range(listing.count()))
            save.setEnabled(consent.isChecked() and bool(account.text().strip()) and checked)
        consent.toggled.connect(validate)
        account.textChanged.connect(validate)
        listing.itemChanged.connect(validate)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        validate()
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted or not self._allowed() or not consent.isChecked():
                return
            chosen = [self._preview[index] for index in range(listing.count()) if listing.item(index).checkState() == Qt.CheckState.Checked]
            result = self.store.import_conversations(provider.currentText(), account.text(), chosen)
            self.notice.emit(f"Copias importadas: {result['imported']}; actualizadas: {result['updated']}; sin cambios: {result['skipped']}.")
            self.reload()
            self.changed.emit()
        except (MemoryStoreError, HistoryFormatError, Denied):
            self.notice.emit("No se guardó la selección de historiales.")
        finally:
            self._preview.clear()
            self._dialogs.discard(dialog)
            dialog.deleteLater()

    def detail(self):
        record = self._current()
        if record is None or not self._allowed():
            return
        try:
            full = self.store.conversation(record["id"])
            if full is None:
                return
        except (MemoryStoreError, Denied):
            self.notice.emit("No se pudo abrir la conversación.")
            return
        dialog = QDialog(self)
        self._dialogs.add(dialog)
        dialog.setWindowTitle("Copia importada · " + full["title"])
        dialog.resize(750, 580)
        layout = QVBoxLayout(dialog)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setPlainText(f"Proveedor: {full['provider']}\nCuenta: {full['account']}\nOrigen: {full['external_id']}\n\n" + conversation_text(full))
        layout.addWidget(view)
        try:
            dialog.exec()
        finally:
            self._dialogs.discard(dialog)
            dialog.deleteLater()

    def delete(self):
        record = self._current()
        if record is None or not self._allowed() or self._busy:
            return
        if QMessageBox.question(self, "Borrar copia local", "¿Borrar esta copia y su índice local? No borra la conversación del proveedor.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete_conversation(record["id"])
            self.reload()
            self.changed.emit()
        except (MemoryStoreError, Denied):
            self.notice.emit("No se pudo borrar la copia.")

    def delete_account(self):
        selected = self.accounts.currentData()
        if selected is None or not self._allowed() or self._busy:
            return
        if QMessageBox.question(self, "Borrar copias de esta cuenta", "¿Borrar todas las copias importadas bajo este alias y sus índices? No elimina ni modifica la cuenta del proveedor.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete_account(*tuple(selected))
            self.reload()
            self.changed.emit()
        except (MemoryStoreError, Denied):
            self.notice.emit("No se pudieron borrar las copias de esta cuenta.")

    def retrieve(self, query):
        if not self._allowed() or not self.use_context.isChecked():
            return []
        selected = self.accounts.currentData()
        provider, account = tuple(selected) if selected is not None else (None, None)
        return self.store.search(query, 3, provider=provider, account=account)
