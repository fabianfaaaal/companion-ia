"""Explicit native startup diagnostic: version only, no inference or downloads."""
import json
import os
import subprocess
import time
from urllib.request import build_opener, ProxyHandler
from .setup_manager import SetupManager
from .policy import Policy
from .ollama_client import OllamaClient


def run_start_check(directory):
    import ssl
    try:
        context = ssl.create_default_context()
        opener = build_opener(ProxyHandler({}))
    except (OSError, ValueError) as error:
        (directory / "ollama-start-check.json").write_text(json.dumps({"ssl_context": False, "error_type": type(error).__name__}), encoding="utf-8")
        return 3
    def ready():
        try:
            with opener.open("http://127.0.0.1:11434/api/version", timeout=.3) as response:
                return isinstance(json.loads(response.read(4096)).get("version"), str)
        except (OSError, ValueError):
            return False
    if ready():
        result = {"already_running": True}
    else:
        executable = SetupManager.ollama_executable()
        if executable is None:
            result = {"executable_found": False}
        else:
            result = {"executable_found": True}
            for clean in (False,):
                env = os.environ.copy()
                env["OLLAMA_HOST"] = "127.0.0.1:11434"
                launch = subprocess.Popen
                process = launch([str(executable), "serve"], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                    cwd=executable.parent, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    reached = False
                    for _ in range(40):
                        if ready():
                            reached = True
                            break
                        if process.poll() is not None:
                            break
                        time.sleep(.2)
                    result["clean" if clean else "inherited"] = {"ready": reached, "exit": process.poll()}
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=10)
                    time.sleep(.3)
    manager = SetupManager(Policy(directory / "privacy.json"))
    try:
        manager._begin()
        manager._ensure_chat_engine(allow_install=False)
        result["guided_start"] = True
        result["local_model_count"] = len(OllamaClient().list_models())
    finally:
        manager.cancel()
        manager.confirm_stopped()
    result["ssl_context"] = True
    result["certificate_verification"] = context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    try:
        with opener.open("https://www.python.org/", timeout=10) as response:
            result["https_status"] = response.status
    except (OSError, ValueError) as error:
        result["https_error_type"] = type(error).__name__
    (directory / "ollama-start-check.json").write_text(json.dumps(result), encoding="utf-8")
    return 0 if result.get("already_running") or result.get("inherited", {}).get("ready") else 3
