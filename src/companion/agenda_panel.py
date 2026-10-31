"""In-app calendar and explicitly enabled notices. No OS notification scheduler."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCalendarWidget, QCheckBox, QComboBox, QDateTimeEdit,
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget)

from .agenda import AgendaStore
from .memory_store import MemoryStoreError
from .policy import Denied, State


class AgendaPanel(QWidget):
    notice = Signal(str)
    alert = Signal(dict)

    def __init__(self, policy, db_path: Path, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.store = AgendaStore(db_path, policy)
        self.memory_enabled = False
        self._busy = False
        self._items = []
        layout = QVBoxLayout(self)
        self.info = QLabel("Agenda local. Los avisos requieren esta aplicación abierta, memoria y avisos activos.")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.toggle = QCheckBox("Activar avisos mientras la app está abierta")
        self.toggle.toggled.connect(self.set_active)
        layout.addWidget(self.toggle)
        self.calendar = QCalendarWidget()
        self.calendar.selectionChanged.connect(self._fill)
        layout.addWidget(self.calendar)
        self.listing = QListWidget()
        layout.addWidget(self.listing)
        row = QHBoxLayout()
        self.create_button = QPushButton("Nuevo recordatorio / alarma")
        self.create_button.clicked.connect(lambda: self.request_create())
        self.cancel_button = QPushButton("Cancelar seleccionado")
        self.cancel_button.clicked.connect(self.cancel_selected)
        row.addWidget(self.create_button)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.banner = QLabel()
        self.banner.setTextFormat(Qt.TextFormat.PlainText)
        self.banner.setWordWrap(True)
        self.banner.hide()
        layout.addWidget(self.banner)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.refresh(False)

    def _allowed(self):
        return self.memory_enabled and self.policy.state is State.ACTIVE

    def refresh(self, memory_enabled, busy=False):
        self.memory_enabled = bool(memory_enabled)
        self._busy = bool(busy)
        allowed = self._allowed()
        if allowed:
            self.policy.grant("agenda.read")
            self.policy.grant("agenda.write")
        else:
            self.stop()
        self.toggle.setEnabled(allowed)
        self.calendar.setEnabled(allowed)
        self.listing.setEnabled(allowed)
        self.create_button.setEnabled(allowed and not busy)
        self.cancel_button.setEnabled(allowed and not busy)
        self._reload()
        if allowed and self.toggle.isChecked():
            self.timer.start()

    def set_active(self, enabled):
        enabled = bool(enabled) and self._allowed()
        self.toggle.blockSignals(True)
        self.toggle.setChecked(enabled)
        self.toggle.blockSignals(False)
        if enabled:
            self.timer.start()
        else:
            self.timer.stop()
            self.banner.clear()
            self.banner.hide()

    def stop(self):
        self.set_active(False)
        self.policy.revoke("agenda.read")
        self.policy.revoke("agenda.write")
        self._items = []
        self.listing.clear()

    def _reload(self):
        self._items = []
        if self._allowed():
            try:
                self._items = self.store.list_items()
            except (MemoryStoreError, Denied):
                self.notice.emit("No se pudo leer la agenda.")
        self._fill()

    def _fill(self):
        self.listing.clear()
        if not self._allowed():
            return
        day = self.calendar.selectedDate().toString("yyyy-MM-dd")
        for item in self._items:
            local = datetime.fromisoformat(item["due_at"]).astimezone()
            if local.date().isoformat() != day:
                continue
            status = {"pending": "pendiente", "delivered": "avisado", "cancelled": "cancelado"}[item["status"]]
            row = QListWidgetItem(f"{local:%H:%M} · {item['title']} · {status}")
            row.setData(Qt.ItemDataRole.UserRole, item["id"])
            self.listing.addItem(row)

    def request_create(self, text=""):
        if not self._allowed() or self._busy:
            return None
        dialog = QDialog(self)
        dialog.setWindowTitle("Programar aviso · Confirmación manual")
        form = QFormLayout(dialog)
        title = QLineEdit()
        title.setMaxLength(120)
        body = QTextEdit()
        body.setPlainText((text or getattr(self,"draft_text","")) if isinstance(text,str) else "")
        due = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        due.setCalendarPopup(True)
        due.setDisplayFormat("dd/MM/yyyy HH:mm")
        kind = QComboBox()
        kind.addItem("Recordatorio", "reminder")
        kind.addItem("Alarma", "alarm")
        sound = QCheckBox("Permitir sonido para este aviso")
        consent = QCheckBox("Guardar este aviso en la memoria local (sin cifrado en este prototipo)")
        for label, widget in (("Título", title), ("Detalle", body), ("Fecha y hora local", due),
                              ("Tipo", kind), ("", sound), ("", consent)):
            form.addRow(label, widget)
        warning = QLabel("Solo se avisa con la aplicación abierta y los avisos activos. No se garantiza una alarma con la PC suspendida.")
        warning.setWordWrap(True)
        form.addRow(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        consent.toggled.connect(buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not consent.isChecked():
            return None
        if not self._allowed():
            return None
        try:
            item = self.store.add(title.text(), body.toPlainText(),
                datetime.fromtimestamp(due.dateTime().toSecsSinceEpoch(), timezone.utc),
                kind.currentData(), sound.isChecked())
        except (ValueError, MemoryStoreError, Denied):
            self.notice.emit("No se guardó el aviso. Revisá la fecha futura, el título y los permisos.")
            return None
        self._reload()
        self.notice.emit("Aviso guardado. Activá los avisos para recibirlo con la app abierta.")
        return item

    def cancel_selected(self):
        if not self._allowed() or self._busy:
            return
        row = self.listing.currentItem()
        if row is None:
            return
        if QMessageBox.question(self, "Cancelar aviso", "¿Cancelar el aviso seleccionado?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.cancel(row.data(Qt.ItemDataRole.UserRole))
            self._reload()
        except (MemoryStoreError, Denied):
            self.notice.emit("No se pudo cancelar el aviso.")

    def _tick(self):
        if not self._allowed() or not self.toggle.isChecked():
            self.stop()
            return
        try:
            for item in self.store.due(datetime.now(timezone.utc)):
                lease = self.policy.begin("agenda.read")
                memory_lease = self.policy.begin("memory.read")
                def deliver():
                    if not self._allowed() or not self.toggle.isChecked():
                        return
                    if self.store.mark_delivered(item["id"]):
                        self.banner.setText(f"{item['title']}\n{item['body']}")
                        self.banner.show()
                        self.alert.emit(item)
                self.policy.publish(lease, lambda: self.policy.publish(memory_lease, deliver))
        except (MemoryStoreError, Denied):
            self.notice.emit("No se pudieron comprobar los avisos.")
            if self.policy.state is not State.ACTIVE:
                self.stop()
