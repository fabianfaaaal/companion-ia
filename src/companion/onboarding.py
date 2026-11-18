"""Initial technical setup. No personal content, credentials or inference."""
import json
from pathlib import Path
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout
from companion.policy import State, Denied
from companion.preferences import DEFAULTS


class SetupState:
    def __init__(self, path, policy):
        self.path, self.policy = Path(path), policy

    def complete(self):
        if self.policy.state is not State.ACTIVE:
            return False
        self.policy.grant("setup.read")
        try:
            lease = self.policy.begin("setup.read")
            def read():
                if not self.path.exists() or self.path.stat().st_size > 1024:
                    return False
                return json.loads(self.path.read_text(encoding="utf-8")) == {"version": 1, "complete": True}
            return self.policy.publish(lease, read)
        except (OSError, ValueError, Denied):
            return False

    def finish(self):
        self.policy.grant("setup.write")
        lease = self.policy.begin("setup.write")
        def write():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"version": 1, "complete": True}), encoding="utf-8")
            temporary.replace(self.path)
        self.policy.publish(lease, write)


LABELS = {
    "memory": "Guardar los próximos mensajes y habilitar notas/fotos locales",
    "voice": "Escuchar bienvenida y respuestas (requiere motor de voz local)",
    "microphone": "Permitir Dictar por botón; sin escucha continua",
    "notices": "Avisos de agenda mientras la app esté abierta",
    "foreground": "Observar títulos de ventanas en RAM",
    "environment_context": "Incluir entorno autorizado al conversar",
    "web": "Permitir búsquedas web al pulsar Buscar",
    "web_context": "Usar resultados web como contexto",
    "natural_voice": "Preferir Qwen experimental (lento); desmarcar para Piper",
}


class SetupDialog(QDialog):
    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setWindowTitle("¡Hola! Preparemos tu companion")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        intro = QLabel("Elegí cómo comenzar. Podés conversar sin guardar información. "
                       "La memoria conserva mensajes visibles en esta PC; no concede permisos para APIs pagas. "
                       "El archivo aún no está cifrado: usá información general o de prueba.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.controls = {}
        for key in DEFAULTS:
            control = QCheckBox(LABELS[key])
            control.setChecked(options[key])
            self.controls[key] = control
            layout.addWidget(control)
        note = QLabel("Ollama y la voz se preparan por separado. En Personalidad podés elegir el nombre y estilo. "
                      "Modo privado bloquea voz, recuerdos y proveedores externos. "
                      "Más tarde conserva el chat temporal hasta completar esta configuración.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Guardar y comenzar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Más tarde")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self):
        return {key: control.isChecked() for key, control in self.controls.items()}
