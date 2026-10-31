"""Plain-text chat bubbles, conversation navigation and an offline emoji picker.

These widgets perform no network or persistence operations. Text is never HTML.
"""
from __future__ import annotations

from datetime import datetime
import math

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget, QWidgetAction,
)


class _BubbleText(QTextEdit):
    """Selectable plain text that grows vertically and wraps even long URLs."""
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setPlainText(text)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(0)
        self.setFont(QFont("Segoe UI", 11))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            "QTextEdit { background:transparent; border:0; border-radius:0;"
            "padding:0; color:#ecf4fa; selection-background-color:#39776e; }")
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)

    def _fit_height(self, *_):
        height = max(24, math.ceil(self.document().size().height()) + 4)
        if self.height() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()


class _Bubble(QFrame):
    def __init__(self, role, text, label, meta, parent=None):
        super().__init__(parent)
        self.role = role
        self.setObjectName("chatBubble")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        user = role == "user"
        background = "#225547" if user else "#19293b"
        border = "#347d68" if user else "#2d4259"
        self.setStyleSheet(
            f"QFrame#chatBubble {{ background:{background}; border:1px solid {border};"
            "border-radius:17px; }"
            "QLabel { background:transparent; border:0; }")
        self.heading = QLabel(label or ("Vos" if user else "Companion"))
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        self.heading.setStyleSheet(
            "background:transparent; color:" + ("#a9f1cf" if user else "#9db9d6") +
            "; font-size:11px; font-weight:600;")
        layout.addWidget(self.heading)
        self.body = _BubbleText(text, self)
        self.body.setAccessibleName("Tu mensaje" if user else "Mensaje del companion")
        self.natural_width = max(150, min(1200, max(
            (self.body.fontMetrics().horizontalAdvance(line) for line in text.splitlines()),
            default=100)) + 34)
        layout.addWidget(self.body)
        if meta:
            detail = QLabel(meta)
            detail.setTextFormat(Qt.TextFormat.PlainText)
            detail.setWordWrap(True)
            detail.setStyleSheet("background:transparent; color:#9cb4c7; font-size:10px;")
            layout.addWidget(detail)


class ChatView(QScrollArea):
    """Conversation surface compatible with existing plain-text inspections."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("conversationView")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(160)
        self.setStyleSheet(
            "QScrollArea#conversationView {background:#101b29; border:1px solid #24384b;"
            "border-radius:15px;}"
            "QScrollBar:vertical {background:transparent; width:9px; margin:5px 0;}"
            "QScrollBar::handle:vertical {background:#36546a; border-radius:4px; min-height:24px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}")
        self.content = QWidget()
        self.content.setStyleSheet("background:transparent;")
        self.content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.rows = QVBoxLayout(self.content)
        self.rows.setContentsMargins(18, 18, 18, 18)
        self.rows.setSpacing(13)
        self.placeholder = QLabel("Tu conversación empieza acá.")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet("color:#829bb1; background:transparent; font-size:15px; padding:24px;")
        self.rows.addWidget(self.placeholder)
        self.rows.addStretch(1)
        self.setWidget(self.content)
        self._messages = []
        self._bubbles = []
        self._stick_bottom = True
        self._automatic_scroll = False
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.verticalScrollBar().rangeChanged.connect(self._range_changed)

    def setReadOnly(self, value):
        # All bodies are permanently read-only; retained for QTextEdit callers.
        pass

    def setPlaceholderText(self, text):
        self.placeholder.setText(text)

    def append_message(self, role, text, label="", meta=""):
        text = str(text)
        role = role if role in ("user", "assistant") else "notice"
        at_bottom = self.verticalScrollBar().value() >= self.verticalScrollBar().maximum() - 12
        self._stick_bottom = at_bottom
        self.placeholder.hide()
        row = QWidget(self.content)
        row.setStyleSheet("background:transparent;")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        if role == "notice":
            notice = QLabel(text)
            notice.setTextFormat(Qt.TextFormat.PlainText)
            notice.setWordWrap(True)
            notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
            notice.setStyleSheet(
                "background:#132131; color:#a7bacb; border:1px solid #253a4d;"
                "border-radius:9px; padding:7px 12px; font-size:11px;")
            layout.addStretch(1)
            layout.addWidget(notice, 4)
            layout.addStretch(1)
            self._bubbles.append((notice, role))
        else:
            bubble = _Bubble(role, text, str(label), str(meta), row)
            if role == "user":
                layout.addStretch(1)
                layout.addWidget(bubble)
            else:
                layout.addWidget(bubble)
                layout.addStretch(1)
            self._bubbles.append((bubble, role))
        self.rows.insertWidget(self.rows.count() - 1, row)
        self._messages.append(dict(role=role, text=text, label=str(label), meta=str(meta)))
        self._fit_bubbles()
        self.rows.activate()
        if at_bottom:
            QTimer.singleShot(0, self._scroll_bottom)

    def _fit_bubbles(self):
        usable = max(1, self.viewport().width() - 38)
        width = max(1, min(usable, int(usable * .74)))
        for bubble, role in self._bubbles:
            bubble.setMaximumWidth(usable if role == "notice" else width)
            if isinstance(bubble, _Bubble):
                bubble.setFixedWidth(min(width, bubble.natural_width))
                bubble.body._fit_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_bubbles"):
            self._fit_bubbles()
            if self._stick_bottom:
                QTimer.singleShot(0, self._scroll_bottom)

    def _scrolled(self, value):
        if not self._automatic_scroll:
            self._stick_bottom = value >= self.verticalScrollBar().maximum() - 12

    def _range_changed(self, *_):
        if self._stick_bottom:
            QTimer.singleShot(0, self._scroll_bottom)

    def _scroll_bottom(self):
        if self._stick_bottom:
            self._automatic_scroll = True
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
            self._automatic_scroll = False

    def clear(self):
        self._messages.clear()
        self._bubbles.clear()
        while self.rows.count() > 2:
            item = self.rows.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                for text_view in widget.findChildren(QTextEdit):
                    text_view.clear()
                for text_label in widget.findChildren(QLabel):
                    text_label.clear()
                widget.deleteLater()
        self.placeholder.show()
        self._stick_bottom = True

    def setPlainText(self, text):
        self.clear()
        if text:
            self.append_message("notice", text)

    def toPlainText(self):
        sections = []
        for message in self._messages:
            prefix = message["label"] or (
                "Vos" if message["role"] == "user" else
                "Companion" if message["role"] == "assistant" else "")
            sections.append((prefix + "\n" if prefix else "") + message["text"])
        return "\n\n".join(sections)


class ConversationSidebar(QWidget):
    selected = Signal(str)
    new_requested = Signal()
    rename_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("conversationSidebar")
        self.setMinimumWidth(210)
        self.setMaximumWidth(340)
        self.setStyleSheet(
            "QWidget#conversationSidebar {background:#101a27; border-right:1px solid #26394c;}"
            "QListWidget {background:transparent; border:0; outline:0; padding:3px;}"
            "QListWidget::item {padding:12px 10px; border-radius:9px; margin:3px 0; color:#a8bdd0;}"
            "QListWidget::item:selected {background:#203b42; color:#d8fff0;}"
            "QListWidget::item:hover:!selected {background:#172a3c;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 14)
        layout.setSpacing(10)
        title = QLabel("CONVERSACIONES")
        title.setStyleSheet("font-size:11px; letter-spacing:1px; color:#91a9bd; font-weight:600;")
        layout.addWidget(title)
        self.new_button = QPushButton("+  Nueva conversación")
        self.new_button.setAccessibleName("Nueva conversación")
        self.new_button.setStyleSheet(
            "QPushButton {background:#75e4c2; color:#10281f; border:0; border-radius:9px;"
            "padding:11px 12px; font-weight:600;}"
            "QPushButton:hover {background:#a2f5db;}")
        self.new_button.clicked.connect(self.new_requested.emit)
        layout.addWidget(self.new_button)
        self.listing = QListWidget()
        self.listing.setAccessibleName("Conversaciones guardadas")
        self.listing.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.listing.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.listing.currentItemChanged.connect(self._selected)
        layout.addWidget(self.listing, 1)
        self.empty = QLabel("Tus conversaciones aparecerán acá.")
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color:#819aad; font-size:12px; padding:8px;")
        layout.addWidget(self.empty)
        row = QHBoxLayout()
        self.rename_button = QPushButton("Renombrar")
        self.delete_button = QPushButton("Borrar")
        self.rename_button.clicked.connect(lambda: self._action(self.rename_requested))
        self.delete_button.clicked.connect(lambda: self._action(self.delete_requested))
        row.addWidget(self.rename_button)
        row.addWidget(self.delete_button)
        layout.addLayout(row)
        self.hint = QLabel("Memoria activa: historial local.\nMemoria apagada: sesión temporal.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#819aad; font-size:11px;")
        layout.addWidget(self.hint)
        self._buttons()

    def _action(self, signal):
        item = self.listing.currentItem()
        if item is not None:
            signal.emit(item.data(Qt.ItemDataRole.UserRole))

    def _selected(self, current, previous):
        self._buttons()
        if current is not None:
            self.selected.emit(current.data(Qt.ItemDataRole.UserRole))

    def _buttons(self):
        available = self.listing.currentItem() is not None
        self.rename_button.setEnabled(available)
        self.delete_button.setEnabled(available)

    def set_items(self, items):
        current = self.listing.currentItem()
        identifier = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.listing.blockSignals(True)
        self.listing.clear()
        for record in items:
            title = str(record.get("title") or "Nueva conversación")
            stamp = str(record.get("updated_at", ""))
            try:
                stamp = datetime.fromisoformat(stamp).astimezone().strftime("%d/%m · %H:%M")
            except ValueError:
                stamp = stamp[:19].replace("T", " ")
            item = QListWidgetItem(title + ("\n" + stamp if stamp else ""))
            item.setData(Qt.ItemDataRole.UserRole, str(record["id"]))
            item.setToolTip(title)
            self.listing.addItem(item)
            if record["id"] == identifier:
                self.listing.setCurrentItem(item)
        self.listing.blockSignals(False)
        self.empty.setVisible(not items)
        self._buttons()

    def set_current(self, identifier):
        self.listing.blockSignals(True)
        self.listing.clearSelection()
        self.listing.setCurrentRow(-1)
        for index in range(self.listing.count()):
            item = self.listing.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == identifier:
                self.listing.setCurrentItem(item)
                break
        self.listing.blockSignals(False)
        self._buttons()


class EmojiButton(QPushButton):
    emoji_selected = Signal(str)
    EMOJIS = ("😊", "😂", "❤️", "👍", "✨", "🤔", "🙌", "😎", "🎉", "🥰",
              "😅", "🙂", "👀", "💡", "🔥", "👏", "🐱", "🐶", "☕", "💚")

    def __init__(self, parent=None):
        super().__init__("😊", parent)
        self.setAccessibleName("Insertar emoji")
        self.setToolTip("Insertar un emoji en tu mensaje")
        self.setFont(QFont("Segoe UI Emoji", 14))
        self.setFixedWidth(48)
        self.setStyleSheet("QPushButton {font-family:'Segoe UI Emoji'; font-size:21px; padding:6px;"
                           "background:#203446; border:1px solid #38546a; border-radius:10px;}"
                           "QPushButton:hover {background:#2b4d5a;}"
                           "QPushButton::menu-indicator {image:none; width:0;}")
        menu = QMenu(self)
        menu.setStyleSheet("QMenu {background:#17283a; border:1px solid #36536b; border-radius:10px; padding:8px;}")
        action = QWidgetAction(menu)
        grid_widget = QWidget(menu)
        grid_widget.setStyleSheet("background:#17283a;")
        grid = QGridLayout(grid_widget)
        grid.setSpacing(3)
        for index, emoji in enumerate(self.EMOJIS):
            button = QPushButton(emoji)
            button.setFont(QFont("Segoe UI Emoji", 17))
            button.setFixedSize(43, 41)
            button.setAccessibleName("Insertar " + emoji)
            button.setStyleSheet("QPushButton {font-family:'Segoe UI Emoji'; font-size:22px; background:transparent; border:0; padding:3px; border-radius:7px;}"
                                 "QPushButton:hover {background:#2b4d5a;}")
            button.clicked.connect(lambda checked=False, value=emoji: self._choose(value))
            grid.addWidget(button, index // 5, index % 5)
        action.setDefaultWidget(grid_widget)
        menu.addAction(action)
        self.setMenu(menu)

    def _choose(self, emoji):
        self.emoji_selected.emit(emoji)
        self.menu().close()
