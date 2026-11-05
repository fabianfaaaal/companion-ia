"""Explicit guided downloads/installations; all effects require a live setup lease.

No background downloads. This optional installer never reads conversation data.
Windows installers are accepted only after Windows validates the expected signer.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, urljoin
from urllib.error import HTTPError
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import uuid4

from .policy import Denied, State
from .voice_paths import engine_ready, register_engine

MODEL = "qwen2.5:1.5b"
PYTHON_VERSION = "3.12.10"
PYTHON_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
OLLAMA_URL = "https://ollama.com/download/OllamaSetup.exe"
VC_RUNTIME_URL = "https://aka.ms/vc14/vc_redist.x64.exe"
ALLOWED_HOSTS = frozenset({"www.python.org", "python.org", "ollama.com", "www.ollama.com",
                          "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
                          "aka.ms", "download.visualstudio.microsoft.com", "download.microsoft.com"})
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SetupError(RuntimeError):
    pass


class SetupCancelled(SetupError):
    pass


def supported_platform():
    """The real installer remains Windows x64 only; tests mock this boundary."""
    return os.name == "nt" and platform.machine().lower() in ("amd64", "x86_64")


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.username or parts.password or parts.port not in (None, 443):
        raise SetupError("La descarga salió de los sitios oficiales permitidos. No se ejecutó el archivo.")
    return url


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        # Redirect hops are followed by the manager, each with a fresh lease check.
        return None


def resources_root():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "setup-resources"
    return Path(__file__).resolve().parents[2]


def voice_resources():
    root = resources_root()
    if getattr(sys, "frozen", False):
        return root / "requirements-voice-windows.txt", root / "speech_engine.py"
    return root / "requirements-voice-windows.txt", root / "src/companion/speech_engine.py"


VOICE_FETCH = r'''
import json, os, pathlib, shutil, sys
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1'
from huggingface_hub import HfApi, hf_hub_download, snapshot_download
root=pathlib.Path(sys.argv[1])
repo='rhasspy/piper-voices'; folder='es/es_AR/daniela/high'; name='es_AR-daniela-high'
revision=HfApi(token=False).model_info(repo).sha
voice=root/'models/piper'; voice.mkdir(parents=True,exist_ok=True)
for filename in [name+'.onnx',name+'.onnx.json','MODEL_CARD']:
    src=hf_hub_download(repo,folder+'/'+filename,revision=revision,token=False,cache_dir=str(root/'download-cache'))
    shutil.copyfile(src,voice/filename)
whisper='ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66'
snapshot_download('Systran/faster-whisper-base',revision=whisper,token=False,
    local_dir=str(root/'models/whisper-base'),allow_patterns=['model.bin','config.json','tokenizer.json','vocabulary.txt','README.md'])
(root/'models/sources.json').write_text(json.dumps({'piper_repository':repo,'piper_revision':revision,
    'voice':name,'voice_dataset_license':'CC-BY-SA-4.0; see MODEL_CARD',
    'whisper_repository':'Systran/faster-whisper-base','whisper_revision':whisper}),encoding='utf-8')
'''


class ElevatedInstaller:
    """Retain the Windows process handle so cancellation is never assumed."""
    stdout=None
    def __init__(self,args):
        import ctypes
        from ctypes import wintypes
        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_=[("cbSize",wintypes.DWORD),("fMask",wintypes.ULONG),("hwnd",wintypes.HWND),
                ("lpVerb",wintypes.LPCWSTR),("lpFile",wintypes.LPCWSTR),("lpParameters",wintypes.LPCWSTR),
                ("lpDirectory",wintypes.LPCWSTR),("nShow",ctypes.c_int),("hInstApp",wintypes.HINSTANCE),
                ("lpIDList",ctypes.c_void_p),("lpClass",wintypes.LPCWSTR),("hkeyClass",wintypes.HKEY),
                ("dwHotKey",wintypes.DWORD),("hIcon",wintypes.HANDLE),("hProcess",wintypes.HANDLE)]
        shell=ctypes.WinDLL("shell32",use_last_error=True)
        shell.ShellExecuteExW.argtypes=[ctypes.POINTER(SHELLEXECUTEINFOW)]
        shell.ShellExecuteExW.restype=wintypes.BOOL
        info=SHELLEXECUTEINFOW()
        info.cbSize=ctypes.sizeof(info);info.fMask=0x40|0x100;info.lpVerb="runas"
        info.lpFile=str(args[0]);info.lpParameters=subprocess.list2cmdline(args[1:]);info.nShow=0
        if not shell.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
            raise SetupError("Windows no autorizó el requisito de Microsoft. Podés volver a aceptar su instalación.")
        self._handle=info.hProcess
        self._kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        self._kernel.GetProcessId.argtypes=[wintypes.HANDLE];self._kernel.GetProcessId.restype=wintypes.DWORD
        self._kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        self._kernel.GetExitCodeProcess.restype=wintypes.BOOL
        self._kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT];self._kernel.TerminateProcess.restype=wintypes.BOOL
        self._kernel.CloseHandle.argtypes=[wintypes.HANDLE];self._kernel.CloseHandle.restype=wintypes.BOOL
        self.pid=self._kernel.GetProcessId(self._handle)
        self.returncode=None

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        import ctypes
        from ctypes import wintypes
        code=wintypes.DWORD()
        if not self._kernel.GetExitCodeProcess(self._handle,ctypes.byref(code)):
            raise SetupError("No se pudo comprobar si el instalador de Microsoft terminó.")
        if code.value!=259:
            self.returncode=code.value
            self._kernel.CloseHandle(self._handle)
            self._handle=None
        return self.returncode

    def kill(self):
        if self.poll() is None and not self._kernel.TerminateProcess(self._handle,1):
            raise OSError("Windows aún está completando el requisito del sistema.")

    def wait(self,timeout=None):
        start=time.monotonic()
        while self.poll() is None:
            if timeout is not None and time.monotonic()-start>timeout:
                raise subprocess.TimeoutExpired("Microsoft runtime installer",timeout)
            time.sleep(.1)
        return self.returncode


class SetupManager:
    def __init__(self, policy, *, home=None, progress=None, progress_state=None):
        self.policy = policy
        self.home = Path(home) if home is not None else Path.home() / ".companion-ia"
        self.progress = progress or (lambda text: None)
        self.progress_state = progress_state or (lambda state: None)
        self.cancelled = threading.Event()
        self._lock = threading.RLock()
        self._process = None
        self._response = None
        self._lease = None
        self._server = None

    def _begin(self):
        if not supported_platform():
            raise SetupError("Esta preparación automática requiere Windows x64. No se cambió tu equipo.")
        self.policy.grant("setup.install")
        self._lease = self.policy.begin("setup.install")
        self._check()

    def _check(self):
        if self.cancelled.is_set():
            raise SetupCancelled("Preparación cancelada. Podés reintentar cuando quieras.")
        if self._lease is None:
            raise SetupError("La preparación requiere aceptar sus condiciones.")
        self.policy.publish(self._lease, lambda: None)

    def _effect(self, callback):
        self._check()
        return self.policy.publish(self._lease, callback)

    def _status(self, text, *, percent=None):
        self._check()
        self.progress(text)
        # A percentage describes this measurable step, never a guessed ETA.
        self.progress_state({"label": text, "percent": percent})

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            response, process, server = self._response, self._process, self._server
        if response is not None:
            try:
                response.close()
            except OSError:
                pass
        for owned in (process, server):
            if owned is not None and owned.poll() is None:
                self._kill_tree(owned)

    @staticmethod
    def _kill_tree(process):
        # Restrict termination to the child tree launched by this manager.
        if os.name == "nt":
            taskkill = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/taskkill.exe"
            try:
                subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10, creationflags=CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                # An elevated system installer may refuse termination. Retain its
                # handle, and confirm_stopped will keep PRIVATE fail-closed.
                pass

    def _run(self, args, *, timeout=1800, capture=False, env=None, elevate=False):
        self._check()
        output = subprocess.PIPE if capture else subprocess.DEVNULL
        def spawn():
            with self._lock:
                self._check()
                self._process = ElevatedInstaller(args) if elevate else subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                    env=env, creationflags=CREATE_NO_WINDOW)
                return self._process
        process = self._effect(spawn)
        started = time.monotonic()
        try:
            while process.poll() is None:
                self._check()
                if time.monotonic() - started > timeout:
                    raise SetupError("La preparación demoró demasiado. Podés cancelar y volver a intentar.")
                time.sleep(.1)
            self._check()
            if process.returncode not in (0, 3010):
                raise SetupError("Un componente no pudo instalarse. Revisá conexión y espacio y volvé a intentar.")
            if capture:
                data = process.stdout.read(1024 * 1024 + 1)
                if len(data) > 1024 * 1024:
                    raise SetupError("La respuesta del instalador no es válida.")
                return data.decode("utf-8-sig", errors="replace")
            return ""
        finally:
            if process.poll() is None:
                self._kill_tree(process)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    raise SetupError("No se pudo confirmar la detención del instalador.") from None
            if process.stdout:
                process.stdout.close()
            with self._lock:
                if self._process is process:
                    self._process = None

    def _download(self, url, target, *, limit=4 * 1024**3):
        validate_url(url)
        target = Path(target)
        self._effect(lambda: target.parent.mkdir(parents=True, exist_ok=True))
        opener = build_opener(ProxyHandler({}), OfficialRedirects())
        self._check()
        # Opening has a bounded timeout. Chunked body reads run outside the gate.
        current=url
        response=None
        for hop in range(8):
            self._check()
            try:
                response=self._effect(lambda:opener.open(Request(current,headers={"User-Agent":"CompanionIA-Setup/1"}),timeout=5))
                break
            except HTTPError as error:
                try:
                    if error.code not in (301,302,303,307,308) or not error.headers.get("Location"):
                        raise SetupError("El sitio de descarga no respondió correctamente.") from None
                    current=validate_url(urljoin(current,error.headers["Location"]))
                finally:
                    error.close()
        if response is None:
            raise SetupError("La descarga tuvo demasiadas redirecciones.")
        with self._lock:
            self._response = response
        total = 0
        digest = hashlib.sha256()
        try:
            validate_url(response.geturl())
            if response.status != 200:
                raise SetupError("El sitio de descarga no respondió correctamente.")
            size = response.headers.get("Content-Length")
            if size is not None and (not str(size).isdigit() or int(size) > limit):
                raise SetupError("La descarga supera el tamaño permitido.")
            stream = self._effect(lambda: target.open("xb"))
            with stream:
                while True:
                    self._check()
                    data = response.read(256 * 1024)
                    self._check()
                    if not data:
                        break
                    total += len(data)
                    if total > limit:
                        raise SetupError("La descarga supera el tamaño permitido.")
                    self._effect(lambda: stream.write(data))
                    digest.update(data)
                    self._status(f"Descargando: {total // (1024**2)} MiB" + (f" de {int(size) // (1024**2)} MiB" if size else ""),
                                 percent=min(100, total * 100 // int(size)) if size and int(size) > 0 else None)
            if not total or size is not None and total != int(size):
                raise SetupError("La descarga quedó incompleta. Reintentá la preparación.")
            return digest.hexdigest()
        finally:
            response.close()
            with self._lock:
                self._response = None

    def _verify_signature(self, installer, allowed):
        powershell = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        script = ("$s=Get-AuthenticodeSignature -LiteralPath $env:COMPANION_INSTALLER; "
                  "$n=''; if($s.SignerCertificate){$n=$s.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName,$false)}; "
                  "@{status=[string]$s.Status;publisher=$n}|ConvertTo-Json -Compress")
        env = os.environ.copy()
        env["COMPANION_INSTALLER"] = str(installer)
        try:
            value = json.loads(self._run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script], capture=True, timeout=30, env=env))
        except (ValueError, TypeError):
            raise SetupError("No se pudo comprobar la firma del instalador. No se ejecutó.") from None
        if value.get("status") != "Valid" or value.get("publisher") not in allowed:
            raise SetupError("Firma o editor inesperados. No se ejecutó el instalador.")
        self._status("Firma de Windows comprobada: " + value["publisher"])

    def _installer(self, url, filename, publishers, args, *, limit, elevate=False):
        cache = self.home / "setup-downloads" / uuid4().hex
        target = cache / filename
        digest = self._download(url, target, limit=limit)
        self._status("Comprobando firma del instalador…")
        self._verify_signature(target, publishers)
        self._status("Instalando componente… Windows puede pedir confirmación. Este paso puede tardar varios minutos.")
        self._check()
        self._run([str(target), *args],elevate=elevate)
        self._effect(lambda: (cache / "receipt.json").write_text(json.dumps({"url":url,"sha256":digest}), encoding="utf-8"))

    def confirm_stopped(self):
        with self._lock:
            processes=(self._process,self._server)
        if any(process is not None and process.poll() is None for process in processes):
            raise RuntimeError("Windows todavía está completando un instalador; no se confirmó su detención.")

    @staticmethod
    def _vc_runtime_ready():
        if os.name!="nt":
            return False
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",0,
                                winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
                installed,_=winreg.QueryValueEx(key,"Installed")
            system=Path(os.environ.get("WINDIR","C:/Windows"))/"System32"
            return installed==1 and all((system/name).is_file() for name in ("vcruntime140.dll","vcruntime140_1.dll","msvcp140.dll"))
        except OSError:
            return False

    def _prepare_vc_runtime(self):
        self._check()
        if self._vc_runtime_ready():
            self._status("El requisito Microsoft Visual C++ ya está instalado; se conserva.")
            return
        self._status("Preparando requisito Microsoft Visual C++ (Windows puede pedir Aceptar)…")
        self._installer(VC_RUNTIME_URL,"vc_redist.x64.exe",{"Microsoft Corporation"},
                        ["/install","/quiet","/norestart"],limit=100*1024**2,elevate=True)
        if not self._vc_runtime_ready():
            raise SetupError("Windows no confirmó el requisito de Microsoft. Puede requerir reiniciar y volver a preparar la voz.")

    def _space(self,required):
        self._check()
        folder=self.home
        while not folder.exists() and folder.parent!=folder:
            folder=folder.parent
        if shutil.disk_usage(folder).free<required:
            raise SetupError("No hay espacio suficiente para preparar los motores. Liberá espacio y reintentá.")

    @staticmethod
    def ollama_executable():
        candidates = [Path(os.environ.get("LOCALAPPDATA", str(Path.home()/"AppData/Local"))) / "Programs/Ollama/ollama.exe",
                      Path.home() / "AppData/Local/Programs/Ollama/ollama.exe"]
        for local in candidates:
            if local.is_file():
                return local
        found = shutil.which("ollama")
        return Path(found) if found else None

    def _api(self, endpoint, body=None, *, stream=False):
        from .doctor import _RejectRedirects
        opener = build_opener(ProxyHandler({}), _RejectRedirects())
        data = None if body is None else json.dumps(body).encode()
        request = Request("http://127.0.0.1:11434/api/"+endpoint, data=data,
                          headers={"Content-Type":"application/json"}, method="GET" if body is None else "POST")
        self._check()
        response = self._effect(lambda: opener.open(request, timeout=5))
        with self._lock:
            self._response = response
        try:
            if stream:
                done = False
                for _ in range(200000):
                    self._check()
                    line = response.readline(65537)
                    self._check()
                    if not line:
                        break
                    if len(line)>65536:
                        raise SetupError("El motor devolvió un progreso inválido.")
                    result = json.loads(line)
                    if "error" in result:
                        raise SetupError("El motor no pudo descargar el modelo. Podés reintentar.")
                    status = str(result.get("status", "Descargando modelo"))[:120]
                    percent = None
                    if type(result.get("total")) is int and type(result.get("completed")) is int and result["total"]>0:
                        percent = max(0, min(100, result["completed"] * 100 // result["total"]))
                        status += f" · {percent}%"
                    self._status(status, percent=percent)
                    if result.get("status") == "success":
                        done = True
                        break
                if not done:
                    raise SetupError("La descarga del modelo no confirmó su finalización.")
                return {}
            raw=response.read(1024*1024+1)
            self._check()
            if len(raw)>1024*1024:
                raise SetupError("El motor devolvió una respuesta demasiado grande.")
            result=json.loads(raw)
            if not isinstance(result,dict):
                raise SetupError("La respuesta del motor no es válida.")
            return result
        finally:
            response.close()
            with self._lock:
                self._response=None

    def _ollama_ready(self):
        try:
            value=self._api("version")
            return isinstance(value.get("version"),str) and bool(value["version"].strip())
        except (OSError, ValueError, SetupError):
            self._check()
            return False

    def _ensure_chat_engine(self, *, allow_install):
        self._status("Revisando el motor local…")
        ready=self._ollama_ready()
        executable=self.ollama_executable()
        if not ready and executable is None:
            if not allow_install:
                raise SetupError("Ollama no está instalado. Abrí Preparar motores y aceptá la preparación del chat local.")
            self._space(6*1024**3)
            self._status("Descargando instalador oficial de Ollama…")
            self._installer(OLLAMA_URL,"OllamaSetup.exe",{"Ollama","Ollama Inc.","Ollama, Inc."},
                            ["/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-"],limit=4*1024**3)
            executable=self.ollama_executable()
            ready=self._ollama_ready()
        if not ready and executable is not None:
            # Existing installs are started, never replaced or reconfigured globally.
            env=os.environ.copy()
            env["OLLAMA_HOST"]="127.0.0.1:11434"
            def launch():
                with self._lock:
                    self._server=subprocess.Popen([str(executable),"serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,env=env,cwd=executable.parent,creationflags=CREATE_NO_WINDOW)
            self._effect(launch)
            for attempt in range(30):
                self._status(f"Esperando al motor local… comprobación {attempt + 1} de 30")
                if self._server.poll() is not None:
                    if self._ollama_ready():
                        ready=True
                        break
                    raise SetupError("Ollama se cerró al iniciar (código " + str(self._server.returncode) + "). No hay conexión local; revisá Preparar motores.")
                if self._ollama_ready():
                    ready=True
                    break
                self._check()
                time.sleep(.5)
        if not ready:
            raise SetupError("No se pudo iniciar Ollama automáticamente. Pulsá Aceptar para reintentar más tarde.")
        return ready

    def start_chat(self):
        self._begin()
        self._ensure_chat_engine(allow_install=False)
        with self._lock:
            self._server=None
        return "Ollama está iniciado. Detectando los modelos locales disponibles…"

    def prepare_chat(self):
        self._begin()
        self._ensure_chat_engine(allow_install=True)
        inventory=self._api("tags")
        models=inventory.get("models",[])
        if not isinstance(models,list):
            raise SetupError("El listado de modelos no es válido.")
        if any(isinstance(item,dict) and isinstance(item.get("name"),str) and item["name"].strip() and not item["name"].endswith((":cloud","-cloud")) and not item.get("remote_host") for item in models):
            self._status("Se conserva el modelo local ya instalado.")
        else:
            self._space(2*1024**3)
            self._status("Descargando modelo inicial (~986 MB)…")
            self._api("pull",{"model":MODEL,"stream":True},stream=True)
        # A ready local service belongs to ordinary chat, including private chat.
        # Cancellation only terminates a service while setup is still incomplete.
        with self._lock:
            self._server=None
        return "Chat local preparado. Elegí el modelo detectado para conversar."

    def _existing_python(self):
        candidates=[]
        if not getattr(sys,"frozen",False):
            candidates.append(Path(sys.executable))
        found=shutil.which("python")
        if found and "windowsapps" not in found.lower():
            candidates.append(Path(found))
        if os.name=="nt":
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
                for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive,r"Software\Python\PythonCore\3.12\InstallPath",0,winreg.KEY_READ|view) as key:
                            path,_=winreg.QueryValueEx(key,"")
                        candidates.append(Path(path)/"python.exe")
                    except OSError:
                        pass
        for candidate in dict.fromkeys(candidates):
            self._check()
            if not candidate.is_file():
                continue
            try:
                self._run([str(candidate),"-I","-c","import sys,venv,ensurepip; assert sys.version_info[:2]==(3,12) and sys.maxsize>2**32"],timeout=15)
                return candidate
            except SetupError:
                self._check()
        return None

    def _python(self):
        existing=self._existing_python()
        if existing is not None:
            self._status("Se conserva y reutiliza Python 3.12 existente; el motor se instala aparte.")
            return existing
        root=self.home/"managed-python"/"3.12.10"
        python=root/"python.exe"
        if python.is_file():
            self._run([str(python),"-I","-c","import sys; assert sys.version_info[:2]==(3,12)"])
            return python
        if root.exists():
            root=root.with_name("3.12.10-"+uuid4().hex[:8])
            python=root/"python.exe"
        self._status("Preparando Python para el motor de voz (sin cambiar PATH)…")
        args=["/quiet","InstallAllUsers=0",f"TargetDir={root}","Include_pip=1","Include_launcher=0",
              "InstallLauncherAllUsers=0","PrependPath=0","AppendPath=0","Shortcuts=0","AssociateFiles=0",
              "Include_test=0","Include_doc=0","Include_tcltk=0"]
        self._installer(PYTHON_URL,"python-3.12.10-amd64.exe",{"Python Software Foundation"},args,limit=40*1024**2)
        if not python.is_file():
            raise SetupError("Python no confirmó la instalación. La voz anterior se conserva.")
        return python

    def prepare_voice(self):
        self._begin()
        from .voice_paths import engine_root
        existing=engine_root()
        if engine_ready(existing):
            self._status("El motor de voz actual ya está preparado; se conserva.")
            return "Motor de voz existente reconocido."
        lock,worker=voice_resources()
        if not lock.is_file() or not worker.is_file():
            raise SetupError("Faltan recursos del instalador de voz. Usá el paquete completo de Companion IA.")
        self._space(3*1024**3)
        self._prepare_vc_runtime()
        python=self._python()
        root=self.home/"voice-engines"/("piper-whisper-"+uuid4().hex[:10])
        self._effect(lambda: root.mkdir(parents=True,exist_ok=False))
        self._status("Creando entorno independiente de voz…")
        self._run([str(python),"-I","-m","venv",str(root/"venv")])
        vpython=root/"venv/Scripts/python.exe"
        env=os.environ.copy()
        env.update(PIP_CONFIG_FILE=os.devnull,PIP_NO_INPUT="1",PIP_INDEX_URL="https://pypi.org/simple",
                   PIP_EXTRA_INDEX_URL="",PIP_TRUSTED_HOST="",PYTHONNOUSERSITE="1",
                   HF_HUB_DISABLE_TELEMETRY="1",HF_HUB_DISABLE_IMPLICIT_TOKEN="1",DO_NOT_TRACK="1")
        env.pop("HF_TOKEN",None)
        self._status("Descargando componentes de voz y dictado (~1 GB con dependencias)…")
        self._run([str(vpython),"-I","-m","pip","install","--only-binary=:all:","--index-url","https://pypi.org/simple","-r",str(lock)],env=env)
        self._status("Descargando voz Daniela y Whisper base (~260 MB)…")
        self._run([str(vpython),"-I","-c",VOICE_FETCH,str(root)],env=env)
        self._effect(lambda: shutil.copyfile(worker,root/"speech_engine.py"))
        self._effect(lambda: (root/"NOTICE.txt").write_text("Piper GPL-3.0-or-later; faster-whisper MIT. Daniela dataset CC-BY-SA-4.0; ver models/piper/MODEL_CARD. Modelos y programas opcionales independientes. Python 3.12.10 PSF.\n",encoding="utf-8"))
        if not engine_ready(root):
            raise SetupError("La preparación de voz quedó incompleta. El motor anterior se conserva.")
        self._status("Verificando bibliotecas de voz y dictado…")
        try:
            self._run([str(vpython),"-I","-c","import piper,ctranslate2,faster_whisper,onnxruntime,numpy"],env=env,timeout=60)
        except SetupError:
            self._check()
            raise SetupError("Las bibliotecas de voz no pudieron cargarse. Puede faltar un requisito compatible o reiniciar Windows. El motor anterior se conserva.") from None
        self._effect(lambda: register_engine(root))
        return "Voz y dictado preparados. El micrófono solo se abre al pulsar Dictar."

    def install_application(self):
        self._begin()
        if not getattr(sys,"frozen",False):
            raise SetupError("La instalación de la aplicación está disponible en el ejecutable Windows.")
        source=Path(sys.executable).resolve().parent
        base=Path(os.environ.get("LOCALAPPDATA",str(Path.home()/"AppData/Local")))/"Programs/CompanionIA"
        target=base/("preview-"+uuid4().hex[:10])
        # Snapshot only shipped application material, never arbitrary adjacent user folders.
        material=[]
        roots=[source/Path(sys.executable).name,source/"_internal",source/"licenses"]
        roots += [source/name for name in ("LICENSE","THIRD_PARTY_NOTICES.md","LEEME.txt","COMO_PROBAR.md","PRIMEROS_PASOS.md")]
        for entry in roots:
            if entry.exists():
                material.append(entry)
                if entry.is_dir():
                    material.extend(entry.rglob("*"))
        if not (source/"_internal").is_dir():
            raise SetupError("Falta la carpeta del runtime de la aplicación.")
        self._effect(lambda: target.mkdir(parents=True,exist_ok=False))
        total=0
        for item in material:
            self._check()
            if item.is_symlink() or getattr(item.stat(),"st_file_attributes",0)&0x400:
                raise SetupError("El paquete contiene un enlace no permitido.")
            relative=item.relative_to(source)
            destination=target/relative
            if item.is_dir():
                self._effect(lambda: destination.mkdir(parents=True,exist_ok=True))
            elif item.is_file():
                total+=item.stat().st_size
                if total>2*1024**3:
                    raise SetupError("El paquete supera el tamaño permitido para instalar.")
                self._effect(lambda: destination.parent.mkdir(parents=True,exist_ok=True))
                with item.open("rb") as input_file:
                    output_file=self._effect(lambda: destination.open("xb"))
                    with output_file:
                        while True:
                            chunk=input_file.read(256*1024)
                            if not chunk:
                                break
                            self._effect(lambda: output_file.write(chunk))
        executable=target/Path(sys.executable).name
        if not executable.is_file() or not (target/"_internal").is_dir():
            raise SetupError("El paquete no contiene la aplicación completa.")
        self._status("Creando acceso en el menú Inicio…")
        powershell=Path(os.environ.get("WINDIR","C:/Windows"))/"System32/WindowsPowerShell/v1.0/powershell.exe"
        script=("$d=Join-Path ([Environment]::GetFolderPath('Programs')) 'Companion IA'; "
                "New-Item -ItemType Directory -Path $d -Force|Out-Null; "
                "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut((Join-Path $d 'Companion IA.lnk')); "
                "$s.TargetPath=$env:COMPANION_EXE; $s.WorkingDirectory=[IO.Path]::GetDirectoryName($env:COMPANION_EXE); $s.Save()")
        env=os.environ.copy();env["COMPANION_EXE"]=str(executable)
        self._run([str(powershell),"-NoProfile","-NonInteractive","-Command",script],env=env,timeout=30)
        return "Aplicación instalada para tu cuenta. Ya podés abrirla desde Inicio; tus datos se conservan aparte."

    def prepare_all(self):
        self.prepare_chat()
        self.prepare_voice()
        return "Chat, voz y dictado preparados. No se activaron sensores ni se enviaron conversaciones."
