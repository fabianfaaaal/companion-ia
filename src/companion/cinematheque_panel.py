from pathlib import Path
from PySide6.QtCore import Qt,QSize,QUrl,Signal
from PySide6.QtGui import QIcon,QDesktopServices
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QListWidget,QListView,QListWidgetItem,QFileDialog,QInputDialog,QMessageBox,QDialog,QTextEdit)
from .media_store import MediaStore
from .memory_store import MemoryStoreError
from .policy import State,Denied


class CinemathequePanel(QWidget):
    notice=Signal(str)
    changed=Signal()
    def __init__(self,policy,db_path:Path,parent=None):
        super().__init__(parent);self.policy=policy;self.store=MediaStore(db_path,policy)
        self.memory_enabled=False;self.offset=0;self.items=[];self._busy=False
        layout=QVBoxLayout(self)
        info=QLabel("Momentos con fotos elegidas por vos. Se conservan original, huella y miniatura; el chat recibe solo título y descripción, no la imagen.")
        info.setWordWrap(True);layout.addWidget(info)
        row=QHBoxLayout();self.import_button=QPushButton("Importar foto y crear momento")
        self.import_button.clicked.connect(self.import_dialog)
        self.previous=QPushButton("Anterior");self.previous.clicked.connect(lambda:self.page(-25))
        self.next=QPushButton("Siguiente");self.next.clicked.connect(lambda:self.page(25))
        for button in (self.import_button,self.previous,self.next):row.addWidget(button)
        layout.addLayout(row)
        self.gallery=QListWidget();self.gallery.setViewMode(QListView.ViewMode.IconMode)
        self.gallery.setIconSize(QSize(230,170));self.gallery.setResizeMode(QListView.ResizeMode.Adjust)
        self.gallery.setWordWrap(True);self.gallery.itemDoubleClicked.connect(lambda _:self.detail())
        layout.addWidget(self.gallery,1)
        bottom=QHBoxLayout();self.open=QPushButton("Abrir momento");self.open.clicked.connect(self.detail)
        self.remove=QPushButton("Borrar momento");self.remove.clicked.connect(self.delete)
        bottom.addWidget(self.open);bottom.addWidget(self.remove);layout.addLayout(bottom)
        self.status=QLabel("Activá memoria para usar la Cinemateca.");layout.addWidget(self.status)
    def refresh(self,enabled,busy=False):
        self.memory_enabled=bool(enabled);self._busy=bool(busy)
        allowed=self.memory_enabled and self.policy.state is State.ACTIVE
        if allowed:
            self.policy.grant("media.read");self.policy.grant("media.write")
        else:self.stop()
        for widget in (self.import_button,self.open,self.remove,self.previous,self.next):widget.setEnabled(allowed and not busy)
        if allowed:self.reload()
    def stop(self):
        self.policy.revoke("media.read");self.policy.revoke("media.write")
        self.items=[];self.gallery.clear();self.offset=0
    def reload(self):
        if not self.memory_enabled or self.policy.state is not State.ACTIVE:return
        try:
            self.items=self.store.list_moments(25,self.offset);self.gallery.clear()
            for record in self.items:
                item=QListWidgetItem(QIcon(record["thumb_path"]),record["title"]+"\n"+record["observed_at"][:10])
                item.setSizeHint(QSize(250,210));self.gallery.addItem(item)
            self.status.setText(f"Momentos de esta página: {len(self.items)} · Página {self.offset//25+1}")
        except (MemoryStoreError,Denied):self.notice.emit("No se pudo abrir la Cinemateca.")
    def page(self,step):self.offset=max(0,self.offset+step);self.reload()
    def import_dialog(self):
        if not self.memory_enabled or self.policy.state is not State.ACTIVE:return
        path,_=QFileDialog.getOpenFileName(self,"Elegir una foto","","Fotos (*.jpg *.jpeg *.png *.webp)")
        if not path:return
        title,ok=QInputDialog.getText(self,"Nuevo momento","Título:")
        if not ok:return
        body,ok=QInputDialog.getMultiLineText(self,"Descripción","¿Qué querés conservar sobre este momento? No se interpreta la imagen automáticamente.")
        if not ok:return
        if QMessageBox.question(self,"Guardar original","Se copiarán esta foto y una miniatura al archivo local, sin cifrado en este preview. El original puede contener metadatos. ¿Guardar?",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:
            self.store.import_photo(path,title,body);self.offset=0;self.reload()
            self.notice.emit("Foto y momento guardados en esta PC.");self.changed.emit()
        except (MemoryStoreError,ValueError,Denied):self.notice.emit("No se guardó el momento. Revisá formato, tamaño y título.")
    def _current(self):
        row=self.gallery.currentRow()
        return self.items[row] if 0<=row<len(self.items) else None
    def detail(self):
        record=self._current()
        if record is None or self.policy.state is not State.ACTIVE:return
        dialog=QDialog(self);dialog.setWindowTitle(record["title"]);dialog.resize(650,500)
        layout=QVBoxLayout(dialog);view=QTextEdit();view.setReadOnly(True)
        view.setPlainText(record["observed_at"]+"\n\n"+record["body"]+"\n\nOrigen: importación manual. La IA no analizó esta fotografía.")
        layout.addWidget(view);button=QPushButton("Abrir original en visor externo");layout.addWidget(button)
        def open_file():
            try:path=self.store.asset_path(record["id"])
            except (MemoryStoreError,Denied):return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        button.clicked.connect(open_file);dialog.exec()
    def delete(self):
        record=self._current()
        if record is None:return
        if QMessageBox.question(self,"Borrar momento","¿Borrar este momento y sus archivos si no están usados por otros momentos?",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:
            self.store.delete_moment(record["id"]);self.reload();self.changed.emit()
        except (MemoryStoreError,Denied):self.notice.emit("No se pudo completar el borrado.")
    def retrieve(self,query):
        if not self.memory_enabled or self.policy.state is not State.ACTIVE:return []
        return [dict(id="moment:"+m["id"],title=m["title"],body="Momento con foto importada; imagen no enviada al modelo. "+m["body"])
                for m in self.store.search(query,3)]
