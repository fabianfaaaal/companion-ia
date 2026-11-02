"""Explicit manual exchange; never reads browser sessions or clipboard."""
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QTextEdit, QPushButton, QApplication
from .policy import State, Denied

SITES = {"DeepSeek": "https://chat.deepseek.com/", "ChatGPT": "https://chatgpt.com/"}


def prepare_prompt(message, name):
    message = message.strip()
    if not message or len(message) > 4000:
        raise ValueError("Escribí entre 1 y 4000 caracteres en el chat primero.")
    name = name.strip()[:60] or "Amiga"
    return (f"Interpretá una compañera virtual llamada {name}. Respondé en español rioplatense, "
            "con calidez y humor natural, sin exagerar el lunfardo. Sé clara y adaptá la extensión "
            "a lo pedido. No inventes recuerdos, hora, ubicación ni acciones realizadas. "
            "No afirmes que guardaste notas o creaste alarmas: este intercambio es manual. "
            "No tenés acceso a la PC ni al archivo de Companion.\n\nMensaje del usuario:\n" + message)


class BrowserChatDialog(QDialog):
    def __init__(self, policy, message, name, parent=None):
        super().__init__(parent)
        self.policy = policy
        self.message = message.strip()
        self.answer = ""
        self.provider = ""
        self.setWindowTitle("Chat web · Intercambio manual")
        self.resize(740, 640)
        layout = QVBoxLayout(self)
        intro = QLabel("1 · Revisá y copiá el mensaje. 2 · Abrí el sitio, pegalo y enviá vos. "
                       "3 · Copiá la respuesta del sitio y pegala abajo para incorporarla al chat y leerla con voz. "
                       "No requiere clave API; el sitio mantiene sus cuentas, límites y condiciones.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.site = QComboBox()
        self.site.addItems(SITES)
        layout.addWidget(self.site)
        self.prompt = QTextEdit()
        self.prompt.setPlainText(prepare_prompt(message, name))
        layout.addWidget(self.prompt)
        self.copy = QPushButton("1 · Copiar mensaje revisado")
        self.copy.clicked.connect(self.copy_prompt)
        layout.addWidget(self.copy)
        self.open = QPushButton("2 · Abrir chat en mi navegador")
        self.open.clicked.connect(self.open_site)
        layout.addWidget(self.open)
        note = QLabel("Solo se incluye el mensaje actual y una personalidad básica. Sin recuerdos, historial ni sensores. "
                      "Copiar no envía: el envío ocurre cuando lo pegás y confirmás en el sitio. "
                      "El portapapeles y el navegador quedan fuera del modo privado de Companion.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.response = QTextEdit()
        self.response.setPlaceholderText("Pegá únicamente la respuesta que querés incorporar…")
        layout.addWidget(self.response)
        self.import_button = QPushButton("3 · Incorporar respuesta al chat")
        self.import_button.clicked.connect(self.import_answer)
        layout.addWidget(self.import_button)
        self.status = QLabel("No se lee el navegador ni el portapapeles automáticamente.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def allowed(self):
        return self.policy.state is State.ACTIVE

    def perform(self, action):
        if not self.allowed():
            return False
        try:
            self.policy.grant("browser.manual")
            lease = self.policy.begin("browser.manual")
            self.policy.publish(lease, action)
            return True
        except Denied:
            return False
        finally:
            self.policy.revoke("browser.manual")

    def copy_prompt(self):
        text = self.prompt.toPlainText().strip()
        if not text or len(text) > 12000:
            self.status.setText("El mensaje revisado debe tener entre 1 y 12000 caracteres.")
            return
        self.perform(lambda: QApplication.clipboard().setText(text))

    def open_site(self):
        def launch():
            if not QDesktopServices.openUrl(QUrl(SITES[self.site.currentText()])):
                self.status.setText("No se pudo abrir el navegador predeterminado.")
        self.perform(launch)

    def import_answer(self):
        text = self.response.toPlainText().strip()
        if not text or len(text) > 16000:
            self.status.setText("Pegá entre 1 y 16000 caracteres de respuesta.")
            return
        def accept_reply():
            self.answer = text
            self.provider = self.site.currentText()
            self.accept()
        self.perform(accept_reply)

    def clear_private(self):
        self.prompt.clear()
        self.response.clear()
        self.message = self.answer = self.provider = ""
        self.reject()
