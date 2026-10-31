"""Ephemeral foreground-window observations and user-reported state."""
import ctypes
import sys
import time
from datetime import datetime,timezone
from PySide6.QtCore import QTimer,Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel,QCheckBox,QComboBox,QMessageBox
from .policy import State,Denied


def foreground_title():
    if sys.platform!="win32":return ""
    user=ctypes.WinDLL("user32",use_last_error=True)
    user.GetForegroundWindow.restype=ctypes.c_void_p
    user.GetWindowTextW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_int]
    handle=user.GetForegroundWindow();buffer=ctypes.create_unicode_buffer(1025)
    user.GetWindowTextW(handle,buffer,len(buffer))
    return buffer.value[:1000]


class EnvironmentPanel(QWidget):
    notice=Signal(str)
    changed=Signal()
    def __init__(self,policy,parent=None,read_title=None):
        super().__init__(parent);self.policy=policy;self.read_title=read_title or foreground_title
        self.observed="";self.observed_at="";self.stamp=0.
        layout=QVBoxLayout(self)
        info=QLabel("Contexto ambiental inicial: estado que vos indicás y título de ventana activa. No toma imágenes, audio ambiental ni identifica personas.")
        info.setWordWrap(True);layout.addWidget(info)
        self.state=QComboBox();self.state.addItems(["Sin indicar","Trabajando","Descansando","Me voy a dormir","Estoy despierto"])
        layout.addWidget(QLabel("Estado informado por vos, no inferido:"));layout.addWidget(self.state)
        self.allow=QCheckBox("Observar títulos de ventanas (solo RAM)");self.allow.toggled.connect(self._toggle);layout.addWidget(self.allow)
        self.share=QCheckBox("Incluir este contexto autorizado al conversar");layout.addWidget(self.share)
        self.share.toggled.connect(lambda enabled: self.changed.emit() if not enabled else None)
        self.label=QLabel("Sin observaciones.");self.label.setWordWrap(True);layout.addWidget(self.label);layout.addStretch()
        self.timer=QTimer(self);self.timer.setInterval(2000);self.timer.timeout.connect(self._tick)
        if sys.platform!="win32":
            self.allow.setEnabled(False);self.label.setText("La observación de ventanas de esta versión requiere Windows.")
    def set_observation_enabled(self, value):
        value = bool(value) and self.policy.state is State.ACTIVE and sys.platform == "win32"
        self.allow.blockSignals(True);self.allow.setChecked(value);self.allow.blockSignals(False)
        if value:
            self.policy.grant("sensor.foreground");self.timer.start()
        else:
            self.timer.stop();self.policy.revoke("sensor.foreground")
            self.observed="";self.observed_at="";self.label.setText("Observaciones borradas.");self.changed.emit()
    def _toggle(self,value):
        self.set_observation_enabled(value)
    def _tick(self):
        if self.policy.state is not State.ACTIVE or not self.allow.isChecked():return
        try:
            lease=self.policy.begin("sensor.foreground")
            title=self.policy.publish(lease,self.read_title)
            if title and not title.startswith("Companion IA"):
                self.observed=title;self.stamp=time.monotonic()
                self.observed_at=datetime.now(timezone.utc).isoformat()
                self.label.setText("Última ventana externa observada:\n"+title)
        except (Denied,OSError):
            self.label.setText("No se pudo observar la ventana.")
    def context(self):
        if self.policy.state is not State.ACTIVE or not self.share.isChecked():return []
        text="Estado informado por el usuario: "+self.state.currentText()+"."
        if self.allow.isChecked() and self.observed and time.monotonic()-self.stamp<=30:
            text+=" Última ventana externa observada a "+self.observed_at+": "+self.observed+". Es solo el título; no se leyó su contenido."
        return [dict(id="environment:session",title="Contexto ambiental autorizado",body=text)]
    def refresh(self,active):
        allowed=bool(active) and self.policy.state is State.ACTIVE
        self.state.setEnabled(allowed);self.share.setEnabled(allowed)
        self.allow.setEnabled(allowed and sys.platform=="win32")
    def stop(self):
        self.timer.stop();self.allow.blockSignals(True);self.allow.setChecked(False);self.allow.blockSignals(False)
        self.share.blockSignals(True);self.share.setChecked(False);self.share.blockSignals(False);self.state.setCurrentIndex(0)
        self.policy.revoke("sensor.foreground");self.observed="";self.observed_at="";self.stamp=0.
        self.label.setText("Contexto ambiental borrado.")
