"""Optional floating presentation for the same Companion application.

No capture, inference, context, persistence or application-exit behavior lives here.
The owning window connects actions and supplies already-established runtime state.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QPen, QPalette
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)


SKINS = {
    "Mint": dict(accent="#75e4c2", soft="#193e3a", edge="#3d776d", top="#1b3038"),
    "Ámbar": dict(accent="#ffd18a", soft="#473827", edge="#8c7049", top="#322d2d"),
    "Violeta": dict(accent="#c8b3ff", soft="#352d4e", edge="#766497", top="#29283d"),
}


class _Avatar(QLabel):
    """Vector face avoids missing-glyph boxes on systems without emoji fonts."""
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.palette().color(QPalette.ColorRole.WindowText), 4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        cx, cy = self.width()/2, self.height()/2
        painter.drawArc(QRectF(cx-31, cy-16, 20, 20), 0, 180*16)
        painter.drawArc(QRectF(cx+11, cy-16, 20, 20), 0, 180*16)
        painter.drawArc(QRectF(cx-20, cy-5, 40, 25), 180*16, 180*16)


class _DragHeader(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._offset = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._offset = None
        super().mouseReleaseEvent(event)


class CompanionDevice(QDialog):
    open_chat = Signal()
    open_memory = Signal()
    record_requested = Signal()
    private_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(False)
        self.setWindowTitle("Companion · dispositivo")
        self.setAccessibleName("Dispositivo flotante de Companion")
        self.resize(244, 336)
        self.setMinimumSize(224, 320)
        self.setMaximumWidth(300)
        self._private = False
        self._recording = False
        self._busy = False
        self._blocked = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(9, 9, 9, 9)
        self.body = QFrame()
        self.body.setObjectName("deviceBody")
        outer.addWidget(self.body)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(15, 10, 15, 14)
        layout.setSpacing(10)
        self.header = _DragHeader(self.body)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("COMPANION")
        title.setObjectName("deviceTitle")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header_layout.addWidget(title, 1)
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("deviceClose")
        self.close_button.setFixedSize(24, 24)
        self.close_button.setAccessibleName("Volver al chat y ocultar dispositivo")
        self.close_button.clicked.connect(self.close)
        header_layout.addWidget(self.close_button)
        layout.addWidget(self.header)
        self.avatar = _Avatar("")
        self.avatar.setObjectName("deviceAvatar")
        self.avatar.setTextFormat(Qt.TextFormat.PlainText)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setMinimumHeight(84)
        self.avatar.setAccessibleName("Avatar decorativo")
        layout.addWidget(self.avatar)
        self.status = QLabel()
        self.status.setObjectName("deviceStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setMinimumHeight(33)
        layout.addWidget(self.status)
        buttons = QGridLayout()
        buttons.setSpacing(7)
        self.chat_button = QPushButton("Chat")
        self.chat_button.setObjectName("devicePrimary")
        self.memory_button = QPushButton("Memoteca")
        self.record_button = QPushButton("Dictar")
        self.private_button = QPushButton("Privado")
        self.chat_button.clicked.connect(self.open_chat.emit)
        self.memory_button.clicked.connect(self.open_memory.emit)
        self.record_button.clicked.connect(self.record_requested.emit)
        self.private_button.clicked.connect(self.private_requested.emit)
        for index, button in enumerate((self.chat_button, self.memory_button,
                                         self.record_button, self.private_button)):
            button.setMinimumHeight(34)
            button.setAutoDefault(False)
            buttons.addWidget(button, index // 2, index % 2)
        self.chat_button.setAccessibleName("Abrir conversación")
        self.memory_button.setAccessibleName("Abrir Memoteca")
        self.record_button.setAccessibleName("Iniciar o terminar dictado")
        self.private_button.setAccessibleName("Activar o desactivar modo privado")
        layout.addLayout(buttons)
        bottom = QHBoxLayout()
        label = QLabel("Piel")
        label.setObjectName("deviceMuted")
        bottom.addWidget(label)
        self.skin = QComboBox()
        self.skin.setAccessibleName("Piel del dispositivo")
        self.skin.addItems(SKINS)
        self.skin.currentTextChanged.connect(self.set_skin)
        bottom.addWidget(self.skin, 1)
        layout.addLayout(bottom)
        self.set_skin("Mint")
        self.set_status()

    def set_skin(self, name):
        """Change this widget only; no application-wide or persisted setting."""
        colors = SKINS.get(name)
        if colors is None:
            return
        self.setStyleSheet(
            "QDialog {background:transparent;}"
            "QFrame#deviceBody {background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f"stop:0 {colors['top']},stop:1 #121e2d); border:1px solid {colors['edge']};"
            "border-radius:22px;}"
            "QWidget {font-family:'Segoe UI'; font-size:12px; color:#e9f3fb;}"
            "QLabel {background:transparent; border:0;}"
            f"QLabel#deviceTitle {{color:{colors['accent']}; font-size:10px;"
            "font-weight:700; letter-spacing:1px;}"
            f"QLabel#deviceAvatar {{background:{colors['soft']}; color:{colors['accent']};"
            f"border:1px solid {colors['edge']}; border-radius:20px;"
            "font-size:34px; font-weight:500;}"
            "QLabel#deviceStatus {font-size:12px; color:#d4e4ee; padding:0 2px;}"
            "QLabel#deviceMuted {font-size:10px; color:#9bb1c2;}"
            f"QPushButton {{background:#203447; border:1px solid {colors['edge']};"
            "border-radius:9px; padding:5px 7px; color:#eaf4fb;}"
            f"QPushButton:hover {{background:{colors['soft']};}}"
            "QPushButton:disabled {color:#6f879a; background:#192735; border-color:#2b3d4d;}"
            f"QPushButton#devicePrimary {{background:{colors['accent']}; color:#142329;"
            "font-weight:700;}"
            "QPushButton#deviceClose {background:transparent; border:0; font-size:19px; padding:0; color:#a8bdce;}"
            f"QPushButton#deviceClose:hover {{background:{colors['soft']}; color:#ffffff;}}"
            "QComboBox {background:#192c3d; border:1px solid #3a5265;"
            "border-radius:7px; padding:5px 8px; color:#c8dce9; font-size:11px;}"
            "QComboBox QAbstractItemView {background:#192c3d; color:#eaf4fb;"
            f"selection-background-color:{colors['soft']};}}")

    def set_status(self, private=False, recording=False, busy=False, blocked=False, can_record=True):
        """Reflect state supplied by the controller; never change actual state."""
        self._private = bool(private)
        self._recording = bool(recording)
        self._busy = bool(busy)
        self._blocked = bool(blocked)
        if self._blocked:
            message = "Bloqueado"
        elif self._private:
            message = "Modo privado"
        elif self._recording:
            message = "Micrófono activo · dictando"
        elif self._busy:
            message = "Preparando respuesta…"
        else:
            message = "Lista para conversar"
        self.status.setText(message)
        self.record_button.setEnabled(not self._private and not self._blocked and (self._recording or (not self._busy and bool(can_record))))
        self.record_button.setText("Terminar" if self._recording and not self._private and not self._blocked else "Dictar")
        self.private_button.setText("Reactivar" if self._private else "Privado")
        self.memory_button.setEnabled(not self._private and not self._blocked)
        self.private_button.setEnabled(not self._blocked)

    def closeEvent(self, event):
        # Closing this accessory returns to the main UI; it never quits the app.
        event.ignore()
        self.hide()
        self.open_chat.emit()
