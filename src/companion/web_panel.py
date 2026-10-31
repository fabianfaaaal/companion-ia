from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox,QComboBox,QHBoxLayout,QLabel,QLineEdit,QListWidget,
    QPushButton,QTextEdit,QVBoxLayout,QWidget)
from .policy import Denied,State
from .web_search import SearchClient,SearchError,public_url


class SearchJob(QThread):
    result=Signal(int,object)
    error=Signal(int,str)
    def __init__(self,job,query,provider):
        super().__init__();self.job=job;self.query=query;self.provider=provider;self.client=SearchClient()
    def run(self):
        try:self.result.emit(self.job,self.client.search(self.query,self.provider))
        except SearchError as e:self.error.emit(self.job,str(e))
        except Exception:self.error.emit(self.job,"No se pudo completar la búsqueda.")
    def cancel(self):self.client.cancel()


class WebPanel(QWidget):
    notice=Signal(str)
    changed=Signal()
    def __init__(self,policy,parent=None):
        super().__init__(parent);self.policy=policy;self._workers=set();self._job=0;self._lease=None;self.results=[]
        layout=QVBoxLayout(self)
        info=QLabel("La consulta se envía al proveedor que elijas. Solo se reciben títulos, enlaces y fragmentos; no se lee el contenido completo de las páginas.")
        info.setWordWrap(True);layout.addWidget(info)
        row=QHBoxLayout();self.query=QLineEdit();self.query.setMaxLength(500);self.query.setPlaceholderText("Consulta que vas a enviar")
        self.provider=QComboBox();self.provider.addItems(["Bing","Wikipedia"])
        self.search=QPushButton("Buscar");self.search.clicked.connect(self._start)
        row.addWidget(self.query,1);row.addWidget(self.provider);row.addWidget(self.search);layout.addLayout(row)
        self.allow=QCheckBox("Autorizar esta consulta fuera de la PC");layout.addWidget(self.allow)
        self.listing=QListWidget();self.listing.currentRowChanged.connect(self._select);layout.addWidget(self.listing)
        self.detail=QTextEdit();self.detail.setReadOnly(True);layout.addWidget(self.detail)
        self.use=QCheckBox("Usar estos fragmentos como contexto en la próxima respuesta");layout.addWidget(self.use)
        self.open=QPushButton("Abrir fuente seleccionada en el navegador");self.open.clicked.connect(self._open);layout.addWidget(self.open)
        self.status=QLabel("Sin consultas realizadas.");layout.addWidget(self.status)

    def refresh(self,active,busy=False):
        active=bool(active) and self.policy.state is State.ACTIVE
        for item in (self.query,self.provider,self.allow,self.listing,self.detail,self.use,self.open):
            item.setEnabled(active)
        self.search.setEnabled(active and not self._workers)
    def request_search(self,query=""):
        if self.policy.state is State.ACTIVE:
            if query:self.query.setText(query[:500])
            self.query.setFocus()
    def _start(self):
        if self.policy.state is not State.ACTIVE or not self.allow.isChecked() or self._workers:
            self.status.setText("Autorizá la consulta visible antes de buscar.");return
        query=self.query.text().strip()
        if not query:self.status.setText("Escribí qué querés buscar.");return
        self.policy.grant("web.search");self._lease=self.policy.begin("web.search")
        self._job+=1;job=SearchJob(self._job,query,self.provider.currentText());self._workers.add(job)
        self.results=[];self.listing.clear();self.detail.clear();self.use.setChecked(False)
        job.result.connect(self._result);job.error.connect(self._error)
        job.finished.connect(lambda:self._finished(job))
        self.policy.publish(self._lease,job.start);self.status.setText("Consultando proveedor…")
        self.allow.setChecked(False);self.refresh(True)
    def _result(self,job,results):
        if job!=self._job:return
        try:self.policy.publish(self._lease,lambda:self._accept(results))
        except Denied:return
    def _accept(self,results):
        self.results=results;self.listing.clear()
        for result in results:self.listing.addItem(result["title"])
        self.status.setText(f"Resultados recibidos: {len(results)}. Son fragmentos del buscador.")
        self.changed.emit()
    def _error(self,job,text):
        if job==self._job and self.policy.state is State.ACTIVE:self.status.setText(text)
    def _finished(self,job):
        self._workers.discard(job);job.deleteLater();self.refresh(self.policy.state is State.ACTIVE)
    def _select(self,row):
        self.detail.setPlainText(self.results[row]["url"]+"\n\n"+self.results[row]["snippet"] if 0<=row<len(self.results) else "")
    def _open(self):
        row=self.listing.currentRow()
        if self.policy.state is State.ACTIVE and 0<=row<len(self.results) and public_url(self.results[row]["url"]):
            QDesktopServices.openUrl(QUrl(self.results[row]["url"]))
    def context(self):
        if self.policy.state is not State.ACTIVE or not self.use.isChecked():return []
        self.use.setChecked(False)
        return [dict(id="web:"+str(i),title=r["title"],body=r["snippet"][:600]+"\nFuente: "+r["url"])
                for i,r in enumerate(self.results[:3])]
    def stop(self):
        self._job+=1;self._lease=None
        for job in tuple(self._workers):job.cancel()
        for job in tuple(self._workers):
            if not job.wait(5500):raise RuntimeError("La búsqueda web no confirmó su cierre.")
        self.policy.revoke("web.search")
        self.results=[];self.listing.clear();self.detail.clear();self.query.clear()
        self.allow.setChecked(False);self.use.setChecked(False);self.status.setText("Consultas y resultados temporales borrados.")
