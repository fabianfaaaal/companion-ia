"""Preserve the build runtime's certificate policy; never bypass TLS checks."""
import importlib.util
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
spec=importlib.util.spec_from_file_location("companion_build_windows",Path(__file__).resolve().parents[1]/"scripts/build_windows.py")
build=importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
from companion.policy import Policy
from companion.setup_manager import SetupManager,SetupError


class RuntimeSSLTests(unittest.TestCase):
    def test_source_context_retains_hostname_and_certificate_validation(self):
        context=ssl.create_default_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)

    def test_mandatory_policy_certificate_is_bundled_next_to_ssl(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=root/"ssl.py";source.write_text("codex-rejected-cas.pem",encoding="utf-8")
            resource=root/"codex-rejected-cas.pem";resource.write_text("synthetic public certificate policy",encoding="utf-8")
            with patch.object(build.ssl,"__file__",str(source)):
                self.assertEqual(build.runtime_ssl_data(),["--add-data",str(resource)+";."])

    def test_missing_required_policy_file_fails_build(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/"ssl.py";source.write_text("codex-rejected-cas.pem",encoding="utf-8")
            with patch.object(build.ssl,"__file__",str(source)),self.assertRaises(RuntimeError):
                build.runtime_ssl_data()

    def test_standard_python_needs_no_custom_certificate_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/"ssl.py";source.write_text("standard ssl module",encoding="utf-8")
            with patch.object(build.ssl,"__file__",str(source)):
                self.assertEqual(build.runtime_ssl_data(),[])

    def test_start_installed_engine_never_installs_or_downloads(self):
        manager=SetupManager(Policy())
        with patch("companion.setup_manager.supported_platform",return_value=True),patch.object(manager,"_ollama_ready",return_value=True),patch.object(manager,"ollama_executable",return_value=None),patch.object(manager,"_installer") as install,patch.object(manager,"_api") as api:
            self.assertIn("iniciado",manager.start_chat())
        install.assert_not_called();api.assert_not_called()

    def test_start_missing_engine_reports_preparation_without_install(self):
        manager=SetupManager(Policy())
        with patch("companion.setup_manager.supported_platform",return_value=True),patch.object(manager,"_ollama_ready",return_value=False),patch.object(manager,"ollama_executable",return_value=None),patch.object(manager,"_installer") as install,self.assertRaises(SetupError):
            manager.start_chat()
        install.assert_not_called()
