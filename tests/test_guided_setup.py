"""Synthetic installation tests: no network, real installers, microphones or models."""
from pathlib import Path
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from companion.policy import Policy,Denied
from companion.setup_manager import SetupManager,SetupError,SetupCancelled,validate_url,voice_resources,MODEL,supported_platform


class Response(io.BytesIO):
    status=200
    def __init__(self,raw,url="https://www.python.org/runtime.exe"):
        super().__init__(raw)
        self.headers={"Content-Length":str(len(raw))}
        self.url=url
    def geturl(self):
        return self.url


class SetupTests(unittest.TestCase):
    def setUp(self):
        platform_boundary=patch("companion.setup_manager.supported_platform",return_value=True)
        platform_boundary.start()
        self.addCleanup(platform_boundary.stop)
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.policy=Policy(self.root/"privacy.json")
        self.manager=SetupManager(self.policy,home=self.root/"managed")
        self.manager._begin()

    def tearDown(self):
        self.manager.cancel()
        self.temp.cleanup()

    def test_unsupported_platform_still_refuses_real_installation(self):
        manager=SetupManager(self.policy,home=self.root/"unsupported")
        with patch("companion.setup_manager.supported_platform",return_value=False),self.assertRaises(SetupError):
            manager._begin()
        self.assertIsNone(manager._lease)
        self.assertFalse(manager.home.exists())

    def test_constructing_does_not_write_or_download(self):
        self.assertFalse(self.manager.home.exists())
        self.assertFalse((self.root/"privacy.json").exists())

    def test_official_origins_only_and_no_embedded_credentials(self):
        for url in ("http://www.python.org/a","https://python.org.evil.test/a","https://user@python.org/a",
                    "https://www.python.org:444/a","file:///tmp/install.exe"):
            with self.assertRaises(SetupError):
                validate_url(url)
        self.assertEqual(validate_url("https://www.python.org/ftp/python/a"),"https://www.python.org/ftp/python/a")

    def test_cancel_and_private_invalidate_side_effects(self):
        self.manager.cancel()
        with self.assertRaises(SetupCancelled):
            self.manager._effect(lambda:(self.root/"written").touch())
        self.assertFalse((self.root/"written").exists())
        self.manager.cancelled.clear()
        self.policy.enter_private()
        with self.assertRaises(Denied):
            self.manager._check()

    def test_bounded_download_receipt_and_truncation(self):
        response=Response(b"synthetic installer")
        opener=Mock();opener.open.return_value=response
        target=self.root/"download.exe"
        with patch("companion.setup_manager.build_opener",return_value=opener):
            digest=self.manager._download("https://www.python.org/runtime.exe",target,limit=100)
        self.assertEqual(len(digest),64)
        self.assertEqual(target.read_bytes(),b"synthetic installer")
        response=Response(b"incomplete");response.headers["Content-Length"]="20"
        opener.open.return_value=response
        with patch("companion.setup_manager.build_opener",return_value=opener),self.assertRaises(SetupError):
            self.manager._download("https://www.python.org/runtime.exe",self.root/"bad.exe",limit=100)

    def test_download_rejects_oversize_before_writing(self):
        response=Response(b"x"*100)
        opener=Mock();opener.open.return_value=response
        with patch("companion.setup_manager.build_opener",return_value=opener),self.assertRaises(SetupError):
            self.manager._download("https://www.python.org/runtime.exe",self.root/"large.exe",limit=10)
        self.assertFalse((self.root/"large.exe").exists())

    def test_invalid_signature_never_launches_installer(self):
        with patch.object(self.manager,"_download",return_value="a"*64),patch.object(self.manager,"_run",return_value=json.dumps({"status":"Valid","publisher":"Unknown vendor"})) as run:
            with self.assertRaises(SetupError):
                self.manager._installer("https://www.python.org/runtime.exe","test.exe",{"Python Software Foundation"},["/quiet"],limit=100)
        self.assertEqual(run.call_count,1)
        self.assertIn("powershell",str(run.call_args.args[0][0]).lower())

    def test_signature_requires_valid_trust_and_exact_publisher(self):
        for status,publisher in (("HashMismatch","Python Software Foundation"),("Valid","Python Software Foundation Fake")):
            with patch.object(self.manager,"_run",return_value=json.dumps({"status":status,"publisher":publisher})),self.assertRaises(SetupError):
                self.manager._verify_signature(self.root/"x.exe",{"Python Software Foundation"})
        with patch.object(self.manager,"_run",return_value=json.dumps({"status":"Valid","publisher":"Python Software Foundation"})):
            self.manager._verify_signature(self.root/"x.exe",{"Python Software Foundation"})

    def test_existing_local_model_not_replaced_or_downloaded(self):
        with patch.object(self.manager,"_ollama_ready",return_value=True),patch.object(self.manager,"ollama_executable",return_value=None),patch.object(self.manager,"_api",return_value={"models":[{"name":"qwen2.5:7b"}]} ) as api,patch.object(self.manager,"_installer") as install:
            self.manager.prepare_chat()
        install.assert_not_called()
        api.assert_called_once_with("tags")

    def test_missing_local_models_pull_small_default(self):
        with patch.object(self.manager,"_ollama_ready",return_value=True),patch.object(self.manager,"ollama_executable",return_value=None),patch.object(self.manager,"_api",side_effect=[{"models":[]},{}]) as api:
            self.manager.prepare_chat()
        self.assertEqual(api.call_args.args,("pull",{"model":MODEL,"stream":True}))
        self.assertTrue(api.call_args.kwargs["stream"])

    def test_existing_voice_is_preserved(self):
        with patch("companion.setup_manager.engine_ready",return_value=True),patch.object(self.manager,"_python") as python:
            self.manager.prepare_voice()
        python.assert_not_called()

    def test_frozen_voice_resources_are_packaged_not_python_executable(self):
        with patch.object(sys,"frozen",True,create=True),patch.object(sys,"_MEIPASS",str(self.root),create=True):
            lock,worker=voice_resources()
        self.assertEqual(lock,self.root/"setup-resources/requirements-voice-windows.txt")
        self.assertEqual(worker,self.root/"setup-resources/speech_engine.py")

    def test_missing_frozen_resources_does_not_install_python(self):
        with patch("companion.setup_manager.engine_ready",return_value=False),patch("companion.setup_manager.voice_resources",return_value=(self.root/"none",self.root/"worker")),patch.object(self.manager,"_python") as python,self.assertRaises(SetupError):
            self.manager.prepare_voice()
        python.assert_not_called()

    def test_all_prepares_chat_then_voice(self):
        order=[]
        with patch.object(self.manager,"prepare_chat",side_effect=lambda:order.append("chat")),patch.object(self.manager,"prepare_voice",side_effect=lambda:order.append("voice")):
            self.manager.prepare_all()
        self.assertEqual(order,["chat","voice"])

    def test_application_install_source_mode_never_copies(self):
        with patch.object(sys,"frozen",False,create=True),self.assertRaises(SetupError):
            self.manager.install_application()


    def test_application_copy_uses_shipped_material_and_creates_shortcut(self):
        source=self.root/"portable"
        source.mkdir()
        (source/"CompanionIA.exe").write_bytes(b"synthetic executable")
        (source/"_internal").mkdir()
        (source/"_internal/runtime.dll").write_bytes(b"synthetic runtime")
        (source/"private-user-notes.txt").write_text("synthetic excluded material")
        local=self.root/"localappdata"
        with patch.object(sys,"frozen",True,create=True),patch.object(sys,"executable",str(source/"CompanionIA.exe")),patch.dict(os.environ,{"LOCALAPPDATA":str(local)}),patch.object(self.manager,"_run") as run:
            self.manager.install_application()
        target=next((local/"Programs/CompanionIA").iterdir())
        self.assertTrue((target/"CompanionIA.exe").is_file())
        self.assertEqual((target/"_internal/runtime.dll").read_bytes(),b"synthetic runtime")
        self.assertFalse((target/"private-user-notes.txt").exists())
        self.assertEqual(run.call_args.kwargs["env"]["COMPANION_EXE"],str(target/"CompanionIA.exe"))
        self.assertTrue((source/"CompanionIA.exe").is_file())

    def test_no_space_never_runs_installation(self):
        usage=Mock(free=0)
        with patch("companion.setup_manager.shutil.disk_usage",return_value=usage),self.assertRaises(SetupError):
            self.manager._space(1024)

    def test_existing_python_is_used_without_installer(self):
        with patch.object(self.manager,"_existing_python",return_value=self.root/"python.exe"),patch.object(self.manager,"_installer") as installer:
            self.assertEqual(self.manager._python(),self.root/"python.exe")
        installer.assert_not_called()


    def test_missing_microsoft_runtime_installs_signed_prerequisite(self):
        with patch.object(self.manager,"_vc_runtime_ready",side_effect=[False,True]),patch.object(self.manager,"_installer") as installer:
            self.manager._prepare_vc_runtime()
        self.assertIn("aka.ms/vc14/vc_redist.x64.exe",installer.call_args.args[0])
        self.assertEqual(installer.call_args.args[2],{"Microsoft Corporation"})
        self.assertTrue(installer.call_args.kwargs["elevate"])

    def test_existing_microsoft_runtime_is_not_modified(self):
        with patch.object(self.manager,"_vc_runtime_ready",return_value=True),patch.object(self.manager,"_installer") as installer:
            self.manager._prepare_vc_runtime()
        installer.assert_not_called()

    def test_unconfirmed_runtime_never_reports_success(self):
        with patch.object(self.manager,"_vc_runtime_ready",return_value=False),patch.object(self.manager,"_installer"),self.assertRaises(SetupError):
            self.manager._prepare_vc_runtime()

    def test_live_elevated_process_prevents_stop_confirmation(self):
        process=Mock();process.poll.return_value=None
        self.manager._process=process
        with self.assertRaises(RuntimeError):
            self.manager.confirm_stopped()
        process.poll.return_value=0
        self.manager.confirm_stopped()
        self.manager._process=None

    def test_dependency_import_must_succeed_before_voice_registration(self):
        lock=self.root/"requirements.txt";lock.write_text("synthetic==1")
        worker=self.root/"speech_engine.py";worker.write_text("# synthetic")
        order=[]
        def run(args,**kwargs):
            if "import piper,ctranslate2,faster_whisper,onnxruntime,numpy" in args:
                order.append("imports")
            return ""
        with patch("companion.setup_manager.engine_ready",side_effect=[False,True]),patch("companion.setup_manager.voice_resources",return_value=(lock,worker)),patch.object(self.manager,"_space"),patch.object(self.manager,"_prepare_vc_runtime",side_effect=lambda:order.append("vc")),patch.object(self.manager,"_python",return_value=self.root/"python.exe"),patch.object(self.manager,"_run",side_effect=run),patch("companion.setup_manager.register_engine",side_effect=lambda root:order.append("register")):
            self.manager.prepare_voice()
        self.assertEqual(order,["vc","imports","register"])

    def test_broken_dependency_does_not_register_partial_engine(self):
        lock=self.root/"requirements.txt";lock.write_text("synthetic==1")
        worker=self.root/"speech_engine.py";worker.write_text("# synthetic")
        def run(args,**kwargs):
            if "import piper,ctranslate2,faster_whisper,onnxruntime,numpy" in args:
                raise SetupError("synthetic dll load failure")
            return ""
        with patch("companion.setup_manager.engine_ready",side_effect=[False,True]),patch("companion.setup_manager.voice_resources",return_value=(lock,worker)),patch.object(self.manager,"_space"),patch.object(self.manager,"_prepare_vc_runtime"),patch.object(self.manager,"_python",return_value=self.root/"python.exe"),patch.object(self.manager,"_run",side_effect=run),patch("companion.setup_manager.register_engine") as register,self.assertRaises(SetupError):
            self.manager.prepare_voice()
        register.assert_not_called()


from PySide6.QtWidgets import QApplication,QMessageBox
from companion.setup_panel import SetupPanel


class SetupUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.policy=Policy(Path(self.temp.name)/"privacy.json")
        self.panel=SetupPanel(self.policy,Path(self.temp.name))

    def tearDown(self):
        self.panel.stop_and_wait()
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_cancel_disclaimer_has_no_job_no_network(self):
        with patch("companion.setup_panel.confirm_setup",return_value=QMessageBox.StandardButton.Cancel),patch("companion.setup_panel.SetupJob") as worker:
            self.panel.start("prepare_all")
        worker.assert_not_called()
        self.assertIsNone(self.panel._job)

    def test_private_blocks_form_and_download_start(self):
        self.policy.enter_private()
        with patch("companion.setup_panel.confirm_setup") as question:
            self.panel.start("prepare_all")
        question.assert_not_called()
        self.panel.refresh(False)
        self.assertTrue(all(not button.isEnabled() for button in self.panel.buttons.values()))

    def test_late_success_after_private_resume_is_discarded(self):
        from types import SimpleNamespace
        manager=SetupManager(self.policy,home=Path(self.temp.name)/"managed")
        with patch("companion.setup_manager.supported_platform",return_value=True):
            manager._begin()
        job=SimpleNamespace(manager=manager)
        self.panel._job=job
        self.policy.enter_private()
        self.policy.resume(local_action=True)
        with patch.object(self.panel,"prepared") as prepared:
            self.panel._success(job,"stale completion")
        prepared.emit.assert_not_called()
        self.assertNotEqual(self.panel.status.text(),"stale completion")
        self.panel._job=None


class SetupPlatformTests(unittest.TestCase):
    def test_real_platform_boundary_accepts_only_windows_x64(self):
        for system,machine,expected in (("nt","AMD64",True),("nt","x86_64",True),
                                        ("nt","ARM64",False),("posix","x86_64",False)):
            with self.subTest(system=system,machine=machine),patch("companion.setup_manager.os.name",system),patch("companion.setup_manager.platform.machine",return_value=machine):
                self.assertEqual(supported_platform(),expected)
