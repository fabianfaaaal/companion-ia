"""Explicit companion profile preferences, never account secrets or history."""
import json
from pathlib import Path
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel,QLineEdit,QComboBox,QTextEdit,QPushButton
from .policy import State,Denied

DEFAULT = dict(name="Amiga", kind="Compañera", relationship="Amistosa", instructions="")


class PersonalityPanel(QWidget):
    applied = Signal(object)
    notice = Signal(str)
    def __init__(self, policy, directory, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.path = Path(directory) / "personality.json"
        layout = QVBoxLayout(self)
        self.name = QLineEdit("Amiga")
        self.name.setMaxLength(40)
        self.kind = QComboBox()
        self.kind.addItems(["Compañera", "Compañero", "Mascota", "Asistente"])
        self.relationship = QComboBox()
        self.relationship.addItems(["Amistosa", "Afectiva", "Trabajo"])
        self.instructions = QTextEdit()
        self.instructions.setPlaceholderText("Tono, humor, vocabulario y preferencias. No escribas claves ni recuerdos íntimos.")
        for label, widget in (("Nombre", self.name), ("Perfil", self.kind), ("Vínculo/estilo", self.relationship), ("Instrucciones propias", self.instructions)):
            layout.addWidget(QLabel(label))
            layout.addWidget(widget)
        self.save = QPushButton("Guardar y aplicar personalidad")
        self.save.clicked.connect(self.apply)
        layout.addWidget(self.save)
        info = QLabel("El vínculo afectivo es una simulación configurable y desactivable. No implica reciprocidad humana ni exclusividad. Las reglas del proveedor siguen vigentes: no se promete contenido sin límites. Esta configuración local aún no está cifrada; no es el archivo íntimo.")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.values = DEFAULT.copy()
        if policy.state is State.ACTIVE:
            self.policy.grant("profile.read")
            self.policy.grant("profile.write")
            try:
                lease = self.policy.begin("profile.read")
                saved = self.policy.publish(lease, lambda: json.loads(self.path.read_text(encoding="utf-8"))) if self.path.exists() else DEFAULT
                self.values = self.validate(saved)
            except (OSError, ValueError, Denied):
                pass
        self.restore()

    @staticmethod
    def validate(value):
        if not isinstance(value, dict) or value.get("kind") not in ("Compañera","Compañero","Mascota","Asistente") or value.get("relationship") not in ("Amistosa","Afectiva","Trabajo"):
            raise ValueError("Perfil no válido")
        if not isinstance(value.get("name"),str) or not value["name"].strip() or len(value["name"])>40:
            raise ValueError("Nombre no válido")
        if not isinstance(value.get("instructions"),str) or len(value["instructions"])>1200 or "\x00" in value["instructions"]:
            raise ValueError("Instrucciones no válidas")
        return {key:value[key] for key in DEFAULT}

    def reload(self):
        if self.policy.state is not State.ACTIVE:
            return
        self.policy.grant("profile.read")
        try:
            lease=self.policy.begin("profile.read")
            saved=self.policy.publish(lease, lambda: json.loads(self.path.read_text(encoding="utf-8"))) if self.path.exists() else DEFAULT
            self.values=self.validate(saved)
        except (OSError,ValueError,Denied):
            self.values=DEFAULT.copy()
        self.restore()

    def restore(self):
        self.name.setText(self.values["name"])
        self.kind.setCurrentText(self.values["kind"])
        self.relationship.setCurrentText(self.values["relationship"])
        self.instructions.setPlainText(self.values["instructions"])

    def apply(self):
        if self.policy.state is not State.ACTIVE:
            return
        try:
            values = self.validate(dict(name=self.name.text(),kind=self.kind.currentText(),relationship=self.relationship.currentText(),instructions=self.instructions.toPlainText()))
            lease = self.policy.begin("profile.write")
            def save():
                self.path.parent.mkdir(parents=True,exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(values,ensure_ascii=False),encoding="utf-8")
                temporary.replace(self.path)
            self.policy.publish(lease,save)
            self.values=values
            self.applied.emit(dict(values))
            self.notice.emit("Personalidad guardada y aplicada; conversación derivada reiniciada.")
        except (ValueError,OSError,Denied):
            self.notice.emit("No se guardó la personalidad: revisá tamaño, nombre y permisos.")

    def refresh(self,active,busy=False):
        allowed=bool(active) and self.policy.state is State.ACTIVE
        for widget in (self.name,self.kind,self.relationship,self.instructions,self.save):
            widget.setEnabled(allowed and not busy)
        if allowed:
            self.policy.grant("profile.read");self.policy.grant("profile.write")

    def context(self):
        if self.policy.state is not State.ACTIVE:
            return {}
        return dict(self.values)

    def stop(self):
        self.instructions.clear()
        self.name.clear()
        self.values=DEFAULT.copy()
        self.policy.revoke("profile.read");self.policy.revoke("profile.write")
