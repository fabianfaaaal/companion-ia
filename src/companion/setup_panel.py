"""Accept/cancel setup UI. No downloads or installations before explicit acceptance."""
from __future__ import annotations
import os
import sys
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget
from .policy import State, Denied
from .setup_manager import SetupManager, SetupCancelled, SetupError


class SetupJob(QThread):
    progress = Signal(str)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, policy, action, parent=None):
        super().__init__(parent)
        self.manager=SetupManager(policy,progress=self.progress.emit)
        self.action=action

    def run(self):
        try:
            result=getattr(self.manager,self.action)()
            self.manager._check()
            self.succeeded.emit(result)
        except SetupCancelled:
            self.manager.cancel()
            self.failed.emit("Preparación cancelada. Lo que ya se descargó o instaló puede permanecer; no se borraron motores existentes.")
        except SetupError as error:
            self.manager.cancel()
            self.failed.emit(str(error))
        except Exception:
            self.manager.cancel()
            self.failed.emit("No se completó la preparación. Revisá Internet y espacio libre y pulsá Aceptar para reintentar. No se cambiaron tus recuerdos ni se consumieron APIs pagas.")


INFORMATION = {
    "prepare_all": (
        "Preparar chat, voz y dictado",
        "Se revisan los motores existentes y se conservan. Si falta alguno, se descarga desde sus sitios oficiales.\n\n"
        "Ollama: instalación para tu cuenta de Windows (al menos 4 GB), modelo local Qwen2.5 1.5B (~986 MB) solo si no hay modelos locales. "
        "Licencias MIT y Apache-2.0. Ollama puede ofrecer funciones cloud propias; este recorrido usa modelos locales.\n\n"
        "Voz: Python 3.12.10 oficial PSF en carpeta propia, sin cambiar PATH; Piper GPL-3.0-or-later, Whisper MIT y voz Daniela (dataset CC-BY-SA-4.0). "
        "Paquetes y modelos desde PyPI/Hugging Face (~1–2 GB adicionales; reservá 8 GB libres en total). Son estimaciones y pueden variar. "
        "Motores de voz en ~/.companion-ia, Ollama en la instalación de su proveedor. Si falta, se instala Microsoft Visual C++ (~30 MB, términos Microsoft); Windows puede solicitar autorización administrativa Aceptar/Cancelar. No se requieren claves ni suscripción.\n\n"
        "Se descargan e instalan programas y modelos; no se envían conversaciones, no se abre micrófono y no se activan permisos. "
        "Aceptar inicia. Cancelar detiene el trabajo propio; los componentes ya instalados pueden quedar para reintentar. "
        "La preparación puede demorar varios minutos. Windows puede pedir confirmar un requisito del sistema."),
    "prepare_chat": (
        "Preparar chat local",
        "Aceptar comprueba Ollama, instala el programa oficial firmado para tu cuenta si falta y descarga Qwen2.5 1.5B (~986 MB, Apache-2.0) solo si no hay modelos locales. "
        "Ollama necesita al menos 4 GB aparte del modelo; reservá 6 GB. Se conserva una instalación existente. "
        "No requiere API paga ni envía conversaciones. Cancelar detiene la preparación; descargas o instalaciones completadas pueden permanecer."),
    "prepare_voice": (
        "Preparar voz y dictado",
        "Aceptar conserva la voz existente o prepara un motor independiente con Python 3.12.10 oficial PSF, Piper GPL-3.0-or-later, faster-whisper MIT y voz Daniela (dataset CC-BY-SA-4.0). "
        "Descarga ~1–2 GB desde Python.org, PyPI y Hugging Face, en ~/.companion-ia; reservá 3 GB. "
        "Si falta, se instala Microsoft Visual C++ (~30 MB, términos Microsoft), y Windows puede pedir Aceptar/Cancelar con permisos administrativos. No cambia PATH, no usa APIs pagas y no graba audio. No descarga Qwen TTS experimental. "
        "Cancelar detiene el proceso propio; los archivos parciales pueden quedar para reintentar."),
    "install_application": (
        "Instalar Companion IA para tu cuenta",
        "Aceptar copia esta aplicación completa a una carpeta nueva en AppData/Local/Programs/CompanionIA y crea un acceso en el menú Inicio. "
        "No reemplaza versiones anteriores ni mueve tus recuerdos. No requiere una instalación de Python para la interfaz. "
        "El paquete actual no tiene firma digital comercial. No se descarga una versión nueva de la aplicación en esta acción. "
        "Cancelar detiene la copia; la carpeta parcial puede permanecer. Los motores se preparan aparte con Preparar todo."),
}


def confirm_setup(parent,title,text):
    dialog=QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setText(text)
    dialog.setIcon(QMessageBox.Icon.Question)
    dialog.setStandardButtons(QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel)
    dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
    dialog.button(QMessageBox.StandardButton.Ok).setText("Aceptar")
    dialog.button(QMessageBox.StandardButton.Cancel).setText("Cancelar")
    return dialog.exec()


class SetupPanel(QWidget):
    notice=Signal(str)
    prepared=Signal()

    def __init__(self,policy,data_dir,parent=None):
        super().__init__(parent)
        self.policy=policy
        self._job=None
        self._jobs=set()
        self._managers=[]
        layout=QVBoxLayout(self)
        title=QLabel("Preparar Companion IA")
        layout.addWidget(title)
        description=QLabel("La aplicación puede preparar sus motores desde Internet. Leé el resumen y elegí Aceptar o Cancelar. "
                           "Los componentes existentes se conservan. No se necesitan comandos, claves ni editar carpetas.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.buttons={}
        for action,label in (("prepare_all","Preparar todo (recomendado)"),
                             ("prepare_chat","Preparar o revisar chat local"),
                             ("prepare_voice","Preparar o revisar voz y dictado"),
                             ("install_application","Instalar aplicación y acceso en Inicio")):
            button=QPushButton(label)
            button.clicked.connect(lambda checked=False,action=action:self.start(action))
            self.buttons[action]=button
            layout.addWidget(button)
        self.cancel_button=QPushButton("Cancelar preparación")
        self.cancel_button.clicked.connect(self.cancel)
        layout.addWidget(self.cancel_button)
        self.status=QLabel("Todavía no se inició ninguna descarga.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.note=QLabel("Windows x64. No prepara drivers de GPU ni cambia políticas de Windows. "
                        "Piper es la opción inicial ligera; Qwen TTS experimental existente se conserva. "
                        "Esta preparación no habilita por sí sola memoria, micrófono o proveedores externos.")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        layout.addStretch()
        self.refresh(policy.state is State.ACTIVE)

    def refresh(self,active,busy=False):
        allowed=bool(active) and self.policy.state is State.ACTIVE and os.name=="nt" and self._job is None
        for action,button in self.buttons.items():
            button.setEnabled(allowed and not busy and (action!="install_application" or bool(getattr(sys,"frozen",False))))
        self.cancel_button.setEnabled(self._job is not None)

    def start(self,action):
        if action not in INFORMATION or self.policy.state is not State.ACTIVE or self._job is not None:
            return
        title,text=INFORMATION[action]
        answer=confirm_setup(self,title,text)
        if answer != QMessageBox.StandardButton.Ok or self.policy.state is not State.ACTIVE:
            return
        job=SetupJob(self.policy,action,self)
        self._job=job
        self._jobs.add(job)
        self._managers.append(job.manager)
        job.progress.connect(lambda text:self._progress(job,text))
        job.succeeded.connect(lambda text:self._success(job,text))
        job.failed.connect(lambda text:self._failure(job,text))
        job.finished.connect(lambda:self._finished(job))
        self.policy.grant("setup.start")
        lease=self.policy.begin("setup.start")
        self.policy.publish(lease,job.start)
        self.refresh(True)

    def _current(self,job,*,allow_cancelled=False):
        if self._job is not job or self.policy.state is not State.ACTIVE or job.manager._lease is None:
            return False
        if job.manager.cancelled.is_set() and not allow_cancelled:
            return False
        try:
            self.policy.publish(job.manager._lease,lambda:None)
            return True
        except Denied:
            return False

    def _progress(self,job,text):
        if self._current(job):
            self.status.setText(text)

    def _success(self,job,text):
        if self._current(job):
            self.status.setText(text)
            self.notice.emit(text)
            self.prepared.emit()

    def _failure(self,job,text):
        if self._current(job,allow_cancelled=True):
            self.status.setText(text)
            self.notice.emit(text)

    def _finished(self,job):
        if self._job is job:
            self._job=None
        self._jobs.discard(job)
        try:
            job.manager.confirm_stopped()
        except RuntimeError:
            pass
        else:
            if job.manager in self._managers:
                self._managers.remove(job.manager)
        job.deleteLater()
        self.refresh(self.policy.state is State.ACTIVE)

    def cancel(self):
        for manager in self._managers:
            manager.cancel()
        self.status.setText("Cancelando preparación…")

    def stop(self):
        self.stop_and_wait()

    def stop_and_wait(self):
        self.cancel()
        for job in tuple(self._jobs):
            if not job.wait(15000):
                raise RuntimeError("No se pudo confirmar la detención de la preparación.")
        for manager in self._managers:
            manager.confirm_stopped()
        self.refresh(False)
