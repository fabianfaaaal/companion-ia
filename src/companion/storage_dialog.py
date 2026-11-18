"""Choose a personal archive folder; accepting does not persist the selection."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QLabel, QPushButton, QVBoxLayout
from .storage_location import StorageLocationError


class StorageLocationDialog(QDialog):
    def __init__(self, location, parent=None):
        super().__init__(parent)
        self.location = location
        self.chosen_path = None
        self._candidate = location.default_directory
        self.setWindowTitle("Dónde guardar tu archivo personal")
        self.resize(620, 330)
        layout = QVBoxLayout(self)
        intro = QLabel("El programa y sus motores van por separado de tus conversaciones, fichas y recuerdos. "
                       "Podés usar la carpeta recomendada o elegir una carpeta propia en otra unidad local.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.directory = QLabel(str(self._candidate))
        self.directory.setTextFormat(Qt.TextFormat.PlainText)
        self.directory.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.directory.setWordWrap(True)
        layout.addWidget(self.directory)
        choose = QPushButton("Elegir otra carpeta…")
        choose.clicked.connect(self.choose_folder)
        layout.addWidget(choose)
        note = QLabel("Elegir otra carpeta no mueve ni borra tu archivo anterior. Para transferir información, usá Exportar/Restaurar. "
                      "El archivo todavía no está cifrado. Cancelar cierra este inicio sin crear un archivo nuevo.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aceptar esta carpeta")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Elegí una carpeta vacía o un archivo Companion", str(self._candidate))
        if not folder:
            return
        try:
            self._candidate = self.location.validate(folder)
            self.directory.setText(str(self._candidate))
            self.status.clear()
        except StorageLocationError as exc:
            self.status.setText(str(exc))

    def accept(self):
        try:
            self.chosen_path = self.location.validate(self._candidate)
        except StorageLocationError as exc:
            self.chosen_path = None
            self.status.setText(str(exc))
            return
        super().accept()

    def reject(self):
        self.chosen_path = None
        super().reject()
