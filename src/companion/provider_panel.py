"""Account selection and outbound context controls; no inference on configuration."""
import json
import os
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QTextEdit, QSpinBox)
from .provider_accounts import AccountStore, AccountError
from .policy import State, Denied
from .remote_client import RemoteClient, RemoteError
from .ollama_client import Cancelled


class BalanceJob(QThread):
    result = Signal(object)
    failure = Signal(str)
    def __init__(self, provider, key):
        super().__init__()
        self.client = RemoteClient(provider.lower(), key)
    def cancel(self):
        self.client.cancel()
    def run(self):
        try:
            self.result.emit(self.client.balance())
        except Cancelled:
            pass
        except RemoteError as error:
            self.failure.emit(str(error))
        except Exception:
            self.failure.emit("No se pudo consultar el saldo. Revisá la cuenta y la conexión.")


class ProviderPanel(QWidget):
    changed = Signal()
    notice = Signal(str)
    def __init__(self, policy, directory, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.store = AccountStore(Path(directory) / "provider-accounts.json", policy)
        self.options_path = Path(directory) / "provider-options.json"
        self._loading_options = True
        self._session_keys = {}
        self._jobs = set()
        self._balance_lease = None
        self._balance_generation = 0
        layout = QVBoxLayout(self)
        intro = QLabel("Por defecto: Ollama local, sin tarifa de API. Cada cuenta externa tiene sus propias credenciales y costos. Una suscripción al chat no concede automáticamente API ni acceso a su historial.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.account = QComboBox()
        layout.addWidget(self.account)
        row = QHBoxLayout()
        self.add = QPushButton("Agregar cuenta")
        self.add.clicked.connect(self.add_dialog)
        self.remove = QPushButton("Quitar cuenta seleccionada")
        self.remove.clicked.connect(self.delete_account)
        self.balance = QPushButton("Consultar saldo reportado")
        self.balance.clicked.connect(self.query_balance)
        for item in (self.add, self.remove, self.balance):
            row.addWidget(item)
        layout.addLayout(row)
        self.allow = QCheckBox("Permitir API externa y asumir sus cargos/cuotas con mi cuenta")
        layout.addWidget(self.allow)
        self.fallback = QCheckBox("Si falla el proveedor, responder con Ollama e informar el cambio")
        self.fallback.setChecked(True)
        layout.addWidget(self.fallback)
        self.prepare = QCheckBox("Preparar instrucciones/contexto con la IA local antes del envío externo")
        self.prepare.setChecked(True)
        layout.addWidget(self.prepare)
        layout.addWidget(QLabel("Además del mensaje actual y personalidad, permitir enviar:"))
        self.share_history = QCheckBox("Conversación reciente de esta sesión")
        self.share_memory = QCheckBox("Notas y descripciones de fotos autorizadas")
        self.share_imports = QCheckBox("Fragmentos de historiales importados de las cuentas elegidas")
        self.share_environment = QCheckBox("Contexto temporal habilitado en Web y Entorno")
        for item in (self.share_history, self.share_memory, self.share_imports, self.share_environment):
            layout.addWidget(item)
            item.clicked.connect(self.changed.emit)
        row = QHBoxLayout()
        self.depth = QComboBox()
        self.depth.addItems(["Auto", "Breve", "Profunda"])
        self.limit = QSpinBox()
        self.limit.setRange(128, 4096)
        self.limit.setValue(1024)
        row.addWidget(QLabel("Profundidad:"))
        row.addWidget(self.depth)
        row.addWidget(QLabel("Máximo de tokens de salida:"))
        row.addWidget(self.limit)
        layout.addLayout(row)
        self.status = QLabel("Sin solicitudes externas. Uso, saldo y cuotas: no informados.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        help_button = QPushButton("Abrir plataforma DeepSeek para crear cuenta/clave")
        help_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://platform.deepseek.com/")))
        layout.addWidget(help_button)
        layout.addStretch()
        self.account.currentIndexChanged.connect(self._changed)
        self.allow.clicked.connect(self._permission_changed)
        self.reload()
        self._load_options()
        self._loading_options = False
        for control in (self.fallback, self.prepare, self.share_history, self.share_memory, self.share_imports, self.share_environment):
            control.clicked.connect(self._save_options)
        self.depth.activated.connect(self._save_options)
        self.limit.valueChanged.connect(self._save_options)

    def _option_controls(self):
        return dict(fallback=self.fallback, prepare=self.prepare, history=self.share_history,
                    memory=self.share_memory, imports=self.share_imports, environment=self.share_environment)

    def _load_options(self):
        if self.policy.state is not State.ACTIVE:
            return
        try:
            lease = self.policy.begin("providers.read")
            def read():
                if not self.options_path.exists():
                    return None
                if self.options_path.stat().st_size > 4096:
                    raise ValueError()
                return json.loads(self.options_path.read_text(encoding="utf-8"))
            saved = self.policy.publish(lease, read)
            if saved is None:
                return
            if saved.get("version") != 1 or any(type(saved.get(key)) is not bool for key in self._option_controls()):
                raise ValueError()
            if saved.get("depth") not in ("Auto", "Breve", "Profunda") or type(saved.get("limit")) is not int or not 128 <= saved["limit"] <= 4096:
                raise ValueError()
            for key, control in self._option_controls().items():
                control.setChecked(saved[key])
            self.depth.setCurrentText(saved["depth"])
            self.limit.setValue(saved["limit"])
        except (OSError,ValueError,TypeError,AttributeError,Denied):
            for control in self._option_controls().values():
                control.setChecked(False)
            self.status.setText("Preferencias externas no válidas; no se habilitó contexto adicional ni respaldo automáticamente.")

    def _save_options(self, *_):
        if self._loading_options or self.policy.state is not State.ACTIVE:
            return
        try:
            lease = self.policy.begin("providers.write")
            values = {key: control.isChecked() for key, control in self._option_controls().items()}
            values.update(version=1, depth=self.depth.currentText(), limit=self.limit.value())
            def save():
                self.options_path.parent.mkdir(parents=True,exist_ok=True)
                temporary = self.options_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(values),encoding="utf-8")
                temporary.replace(self.options_path)
            self.policy.publish(lease,save)
        except (OSError,Denied):
            self.notice.emit("No se guardaron las preferencias externas; se aplican a esta sesión.")

    def _invalidate_balance(self):
        self._balance_generation += 1
        self._balance_lease = None
        for job in tuple(self._jobs):
            job.cancel()

    def _permission_changed(self):
        self._invalidate_balance()
        self.changed.emit()

    def _changed(self):
        self._invalidate_balance()
        self.allow.setChecked(False)
        self.status.setText("Selección modificada. Para usar API externa autorizá sus cargos. Saldo/cuotas: no informados.")
        self.changed.emit()

    def reload(self):
        self.account.blockSignals(True)
        self.account.clear()
        self.account.addItem("Local · Ollama (sin tarifa de API)", None)
        if self.policy.state is State.ACTIVE:
            self.policy.grant("providers.read")
            self.policy.grant("providers.write")
            try:
                for record in self.store.list():
                    self.account.addItem(record["provider"] + " · " + record["alias"] + " · " + record["model"], record)
            except (AccountError, Denied):
                self.notice.emit("No se pudieron leer las cuentas. Se mantiene Ollama local.")
        self.account.blockSignals(False)

    def refresh(self, active, busy=False):
        allowed = bool(active) and self.policy.state is State.ACTIVE
        for widget in (self.account, self.add, self.remove, self.balance, self.allow, self.fallback,
                       self.prepare, self.share_history, self.share_memory, self.share_imports,
                       self.share_environment, self.depth, self.limit):
            widget.setEnabled(allowed and not busy)
        if allowed:
            self.policy.grant("providers.read")
            self.policy.grant("providers.write")
        for control in (self.allow, self.share_history, self.share_memory, self.share_imports, self.share_environment):
            control.setEnabled(allowed)
        self.balance.setEnabled(allowed and not busy and not self._jobs and bool(self.account.currentData()))

    def add_dialog(self):
        if self.policy.state is not State.ACTIVE:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Cuenta de API · credenciales propias")
        form = QFormLayout(dialog)
        provider = QComboBox()
        provider.addItems(["DeepSeek", "OpenAI"])
        alias = QLineEdit()
        alias.setMaxLength(40)
        model = QLineEdit("deepseek-flash")
        model.setMaxLength(128)
        provider.currentTextChanged.connect(lambda name: model.setText("deepseek-flash" if name == "DeepSeek" else ""))
        key = QLineEdit()
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setMaxLength(1024)
        persist = QCheckBox("Recordar clave protegida para este usuario Windows")
        persist.setEnabled(os.name == "nt")
        for title, widget in (("Proveedor", provider), ("Alias local", alias), ("Modelo de API", model),
                              ("Clave (no contraseña del chat)", key), ("", persist)):
            form.addRow(title, widget)
        info = QLabel("Por defecto la clave vive solo en esta sesión. Guardarla usa DPAPI en Windows; nunca va al chat ni al repositorio. Agregar una cuenta no realiza consultas ni carga saldo.")
        info.setWordWrap(True)
        form.addRow(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            key.clear()
            return
        secret = key.text()
        key.clear()
        try:
            item = self.store.add(provider.currentText(), alias.text(), model.text(), secret, persist_key=persist.isChecked())
            if secret and not persist.isChecked():
                self._session_keys[item["id"]] = secret
            self.reload()
            self.notice.emit("Cuenta agregada. Ollama sigue seleccionado hasta que elijas y autorices la cuenta externa.")
        except (AccountError, Denied, OSError):
            self.notice.emit("No se pudo guardar la cuenta. Revisá proveedor, modelo, alias y protección de clave.")

    def delete_account(self):
        record = self.account.currentData()
        if not record or self.policy.state is not State.ACTIVE:
            return
        try:
            self.store.delete(record["id"])
            self._session_keys.pop(record["id"], None)
            self.reload()
            self._changed()
        except (AccountError, Denied, OSError):
            self.notice.emit("No se pudo quitar la cuenta.")

    def request(self):
        record = self.account.currentData()
        if record is None:
            return None
        key = self._session_keys.get(record["id"], "")
        error = ""
        if self.allow.isChecked():
            try:
                key = key or self.store.key(record["id"])
            except (AccountError, Denied):
                error = "No se pudo recuperar la clave."
        else:
            error = "La API externa no está autorizada para esta sesión."
        if not key:
            error = error or "Falta una clave de API para esta cuenta."
        return dict(provider=record["provider"], model=record["model"], alias=record["alias"], key=key,
                    allowed=self.allow.isChecked(), fallback=self.fallback.isChecked(),
                    prepare=self.prepare.isChecked(), max_tokens=self.limit.value(), error=error)

    def query_balance(self):
        if self._jobs or self.policy.state is not State.ACTIVE:
            return
        request = self.request()
        if request is None or request["error"]:
            self.status.setText(request["error"] if request else "Elegí una cuenta externa.")
            return
        self.policy.grant("provider.balance")
        self._balance_lease = self.policy.begin("provider.balance")
        job = BalanceJob(request["provider"], request["key"])
        self._jobs.add(job)
        generation = self._balance_generation
        account_id = self.account.currentData()["id"]
        lease = self._balance_lease
        job.result.connect(lambda result: self._accept_balance(result, generation, account_id, lease))
        job.failure.connect(lambda message: self._balance_failure(message, generation, account_id, lease))
        job.finished.connect(lambda: self._finished(job))
        self.policy.publish(self._balance_lease, job.start)
        self.balance.setEnabled(False)

    def _balance_current(self, generation, account_id):
        selected = self.account.currentData()
        return self.allow.isChecked() and generation == self._balance_generation and selected and selected["id"] == account_id

    def _balance_failure(self, message, generation, account_id, lease):
        if not self._balance_current(generation, account_id):
            return
        try:
            self.policy.publish(lease, lambda: self.status.setText(message))
        except (Denied, AttributeError):
            pass

    def _accept_balance(self, result, generation, account_id, lease):
        if not self._balance_current(generation, account_id):
            return
        try:
            self.policy.publish(lease, lambda: self.status.setText(
                self._balance_text(result)))
        except (Denied, AttributeError):
            pass

    @staticmethod
    def _balance_text(result):
        if not result.get("reported"):
            return "El proveedor no informa saldo por este adaptador."
        available = result.get("is_available")
        lead = "Conexión correcta. El proveedor informa saldo disponible." if available else "Conexión correcta. El proveedor informa saldo insuficiente para llamadas. Podés seguir con Ollama local."
        values = ["Saldo reportado: " + str(item.get("currency","")) + " " + str(item.get("total_balance","no informado")) for item in result.get("balance_infos", [])]
        return "\n".join([lead, *values])

    def _finished(self, job):
        self._jobs.discard(job)
        job.deleteLater()
        self.balance.setEnabled(self.policy.state is State.ACTIVE and bool(self.account.currentData()))

    def show_usage(self, result):
        usage = result.get("usage")
        limits = result.get("limits")
        self.status.setText("Uso reportado: " + (json.dumps(usage, ensure_ascii=False) if usage else "no informado") +
                            "\nCuotas/restante reportados: " + (json.dumps(limits, ensure_ascii=False) if limits else "no informado") +
                            "\nNo es un cálculo de costo ni garantía de saldo disponible.")

    def stop(self):
        self._invalidate_balance()
        for job in tuple(self._jobs):
            job.cancel()
        for job in tuple(self._jobs):
            if not job.wait(5000):
                raise RuntimeError("El proveedor no confirmó el cierre.")
        self._session_keys.clear()
        self.allow.setChecked(False)
        self.policy.revoke("provider.balance")
        self.policy.revoke("remote.request")
        self.policy.revoke("providers.read")
        self.policy.revoke("providers.write")
        self.account.blockSignals(True)
        self.account.clear()
        self.account.addItem("Local · Ollama (sin tarifa de API)", None)
        self.account.blockSignals(False)
        self.status.setText("Sesión externa cerrada; claves temporales y datos de uso descartados.")
