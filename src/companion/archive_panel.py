"""Accept/cancel folder-transfer UI; long file work runs outside the GUI thread."""
from pathlib import Path
from PySide6.QtCore import QThread,QTimer,Signal,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QMessageBox
from .archive_transfer import ArchiveTransfer,ArchiveError,ArchiveCancelled
from .policy import Denied,State


class ArchiveJob(QThread):
    result = Signal(str,object)
    failure = Signal(str)
    def __init__(self,transfer,operation,value=None):
        super().__init__()
        self.transfer,self.operation,self.value=transfer,operation,value
    def cancel(self):
        self.transfer.cancel()
    def run(self):
        try:
            if self.operation == "export":
                result=self.transfer.export()
            elif self.operation == "scan":
                result=self.transfer.scan_incoming()
            else:
                result=self.transfer.prepare_restore(self.value)
            self.result.emit(self.operation,result)
        except (Denied,ArchiveCancelled):
            pass
        except (ArchiveError,OSError,ValueError):
            self.failure.emit("No se pudo completar la copia. Revisá que esté completa y sea de una versión compatible.")


class ArchivePanel(QWidget):
    notice = Signal(str)
    restart_requested = Signal()
    restart_required = Signal()
    def __init__(self,policy,data_dir,parent=None):
        super().__init__(parent)
        self.policy,self.data_dir=policy,Path(data_dir)
        self.enabled=False
        self._stopped=False
        self._permanently_stopped=False
        self._worker=None
        self._seen=set()
        self._dialogs=set()
        self._restart_pending=False
        layout=QVBoxLayout(self)
        intro=QLabel("Para llevarte tus datos, aceptá Crear copia y copiá la carpeta creada. "
            "Para volver a usarlos, pegá esa carpeta dentro de Entrada de restauración. "
            "Companion detecta la copia completa, la revisa y pide Aceptar o Cancelar. "
            "La restauración reemplaza el archivo al volver a abrir la aplicación y guarda una copia de recuperación.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row=QHBoxLayout()
        self.export_button=QPushButton("Crear copia")
        self.export_button.clicked.connect(self.export)
        self.incoming_button=QPushButton("Abrir entrada de restauración")
        self.incoming_button.clicked.connect(lambda:self.open_folder("incoming"))
        self.outgoing_button=QPushButton("Abrir copias creadas")
        self.outgoing_button.clicked.connect(lambda:self.open_folder("outgoing"))
        for button in (self.export_button,self.incoming_button,self.outgoing_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.scan_button=QPushButton("Revisar carpetas pegadas")
        self.scan_button.clicked.connect(lambda:self.scan(explicit=True))
        layout.addWidget(self.scan_button)
        self.status=QLabel("Activá memoria para copiar o restaurar el archivo.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        info=QLabel("Incluye conversaciones, notas, fichas, aprendizajes, agenda, fotos y preferencias. "
            "Claves y cuentas API, modelos, motores de voz y modo privado no viajan en la copia. "
            "Los motores se preparan desde la instalación guiada. El archivo sigue sin cifrar.")
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addStretch()
        self.timer=QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.scan)
        self.refresh(False)

    def _allowed(self):
        return not self._stopped and not self._permanently_stopped and self.enabled and self.policy.state is State.ACTIVE and not self._restart_pending

    def refresh(self,enabled,busy=False):
        if self._permanently_stopped:
            return
        self.enabled=bool(enabled)
        if self.enabled and self.policy.state is State.ACTIVE:
            self._stopped=False
        allowed=self._allowed()
        for button in (self.export_button,self.incoming_button,self.outgoing_button,self.scan_button):
            button.setEnabled(allowed and self._worker is None and not busy)
        if allowed:
            if not self.timer.isActive():
                self.timer.start()
                QTimer.singleShot(0,self.scan)
        else:
            self.timer.stop()
            if not self.enabled or self.policy.state is not State.ACTIVE:
                self.stop()

    def _confirm(self,title,text):
        dialog=QMessageBox(QMessageBox.Icon.Question,title,text,
            QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel,self)
        dialog.button(QMessageBox.StandardButton.Ok).setText("Aceptar")
        dialog.button(QMessageBox.StandardButton.Cancel).setText("Cancelar")
        self._dialogs.add(dialog)
        try:
            return dialog.exec() == QMessageBox.StandardButton.Ok and self._allowed()
        finally:
            self._dialogs.discard(dialog)
            dialog.deleteLater()

    def export(self):
        if self._allowed() and self._confirm("Crear copia", "Se copiarán tus datos y preferencias a una carpeta local. "
            "No incluye claves API ni motores. Después podés copiar y pegar esa carpeta donde quieras conservarla."):
            self._start("export")

    def open_folder(self,kind):
        if not self._allowed() or self._worker is not None:
            return
        try:
            transfer=ArchiveTransfer(self.data_dir,self.policy)
            folders=transfer.folders()
            path=folders[0 if kind == "incoming" else 1]
            lease=self.policy.begin("memory.read")
            self.policy.publish(lease,lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        except (Denied,ArchiveError,OSError):
            self.status.setText("No se pudo abrir la carpeta de transferencia.")

    def scan(self,explicit=False):
        if not self._allowed() or self._worker is not None:
            return
        if explicit:
            self._seen.clear()
        self._start("scan")

    def _start(self,operation,value=None):
        if not self._allowed() or self._worker is not None:
            return
        worker=ArchiveJob(ArchiveTransfer(self.data_dir,self.policy),operation,value)
        self._worker=worker
        lease=self.policy.begin("memory.write" if operation=="restore" else "memory.read")
        worker.result.connect(lambda operation,result,worker=worker,lease=lease:self._accept(worker,lease,operation,result))
        worker.failure.connect(self._failure)
        worker.finished.connect(lambda:self._finished(worker))
        self.status.setText("Revisando carpetas…" if operation == "scan" else "Preparando copia… Podés cancelar con modo privado.")
        self.refresh(self.enabled)
        worker.start()

    def _accept(self,worker,lease,operation,result):
        if worker is not self._worker:
            return
        try:
            self.policy.publish(lease,lambda:None)
        except Denied:
            return
        self._result(operation,result)

    def _result(self,operation,result):
        if not self._allowed():
            return
        if operation == "export":
            self.status.setText("Copia lista en Copias creadas. Copiá la carpeta completa para conservarla.")
            self.notice.emit("Copia local lista; no se subió información a Internet.")
        elif operation == "restore":
            self._restart_pending=True
            self.timer.stop()
            self.status.setText("Copia validada. Cerrá y volvé a abrir Companion para aplicar la restauración.")
            self.restart_required.emit()
            self.restart_requested.emit()
        else:
            for record in result:
                if record["name"] in self._seen:
                    continue
                self._seen.add(record["name"])
                if self._confirm("Copia detectada", "Se encontró una copia completa de Companion. "
                    "Aceptar prepara la restauración para el próximo inicio: reemplaza los datos actuales, "
                    "incluidos recuerdos eliminados que sigan en esa copia. Se guardará una copia de recuperación. "
                    "No modifica claves API ni el modo privado. ¿Continuar?"):
                    # Start after scan's finished signal releases its worker slot.
                    QTimer.singleShot(0,lambda path=record["path"]:self._start_restore(path))
                    break
            self.status.setText("Carpetas revisadas. Las copias incompletas se esperan hasta terminar de pegar.")

    def _start_restore(self,path):
        if self._worker is not None:
            QTimer.singleShot(50,lambda:self._start_restore(path))
        elif self._allowed():
            self._start("restore",path)

    def _failure(self,message):
        if self.policy.state is State.ACTIVE:
            self.status.setText(message)
            self.notice.emit(message)

    def _finished(self,worker):
        current=self._worker is worker
        if current:
            self._worker=None
        worker.deleteLater()
        if current and not self._stopped:
            self.refresh(self.enabled)

    def stop(self,permanent=False):
        self._stopped=True
        self._permanently_stopped=self._permanently_stopped or permanent
        self.enabled=False
        self.timer.stop()
        for dialog in tuple(self._dialogs):
            dialog.reject()
        worker=self._worker
        if worker is not None:
            worker.cancel()
            if not worker.wait(5000):
                raise RuntimeError("La transferencia no confirmó su cierre.")
            self._worker=None
        self._seen.clear()
