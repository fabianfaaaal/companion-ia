"""Synthetic Ollama discovery and installer handoff regressions; no real launches."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from companion.policy import Policy
from companion.setup_manager import SetupManager, SetupError


class OllamaSetupStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.policy = Policy()
        self.manager = SetupManager(self.policy, home=self.root / "managed")

    def tearDown(self):
        self.manager.cancel()
        self.temp.cleanup()

    def test_redirected_appdata_keeps_standard_home_install_discoverable(self):
        home = self.root / "home"
        executable = home / "AppData/Local/Programs/Ollama/ollama.exe"
        executable.parent.mkdir(parents=True)
        executable.touch()
        with patch("companion.setup_manager.Path.home", return_value=home), patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "redirected")}), patch("companion.setup_manager.shutil.which", return_value=None):
            self.assertEqual(self.manager.ollama_executable(), executable)

    def test_installer_started_api_is_rechecked_when_cli_not_discovered(self):
        with patch("companion.setup_manager.supported_platform", return_value=True), patch.object(self.manager, "_ollama_ready", side_effect=[False, True]) as ready, patch.object(self.manager, "ollama_executable", return_value=None), patch.object(self.manager, "_space"), patch.object(self.manager, "_installer") as installer, patch.object(self.manager, "_api", return_value={"models": [{"name": "synthetic-local:1b"}]}) as api, patch("companion.setup_manager.subprocess.Popen") as spawn:
            self.manager.prepare_chat()
        installer.assert_called_once()
        self.assertGreaterEqual(ready.call_count, 2)
        api.assert_called_once_with("tags")
        spawn.assert_not_called()

    def test_install_without_cli_or_api_fails_without_inventing_success(self):
        with patch("companion.setup_manager.supported_platform", return_value=True), patch.object(self.manager, "_ollama_ready", return_value=False), patch.object(self.manager, "ollama_executable", return_value=None), patch.object(self.manager, "_space"), patch.object(self.manager, "_installer"), patch("companion.setup_manager.subprocess.Popen") as spawn, self.assertRaises(SetupError):
            self.manager.prepare_chat()
        spawn.assert_not_called()
