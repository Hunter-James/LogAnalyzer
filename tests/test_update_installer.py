"""Регрессии загрузки EXE и отдельного Windows-установщика."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_updates import APP, LocalServerCase, wait_until
from core.update_checker import Release, RELEASES_URL, newer_release
from core.update_installer import UpdateInstaller, replacement_script


class DownloadTests(LocalServerCase):
    """Использует локальный HTTP-сервер для потока бинарного файла."""

    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.target = Path(self.directory.name) / "тест ' $ папка.exe"
        self.target.write_bytes(b"original")
        self.body = b"MZ" + b"payload" * 10000
        self.installer = UpdateInstaller()
        self.ready = []
        self.install_errors = []
        self.installer.ready.connect(lambda: self.ready.append(True))
        self.installer.failed.connect(self.install_errors.append)
        self.release = Release("9.0.0", RELEASES_URL,
                               f"http://127.0.0.1:{self.server.serverPort()}/LogAnalyzer.exe",
                               len(self.body), hashlib.sha256(self.body).hexdigest())
        self.frozen = patch.object(sys, "frozen", True, create=True)
        self.executable = patch.object(sys, "executable", str(self.target))
        self.frozen.start()
        self.executable.start()

    def tearDown(self):
        self.installer.cancel()
        self.installer.deleteLater()
        self.executable.stop()
        self.frozen.stop()
        self.directory.cleanup()
        super().tearDown()

    def download(self):
        self.installer.start(self.release)
        wait_until(lambda: self.ready or self.install_errors)
        self.assertEqual(self.target.read_bytes(), b"original")

    def test_download_and_handoff(self):
        self.download()
        self.assertEqual(self.install_errors, [])
        self.assertEqual(self.installer._staged.read_bytes(), self.body)
        with patch("core.update_installer.launch_replacement") as launch:
            self.assertTrue(self.installer.install())
            launch.assert_called_once_with(self.target.resolve(), self.installer._staged, os.getpid())
        self.installer.cancel()
        self.assertTrue(self.installer._staged.exists())

    def test_digest_mismatch(self):
        self.body = b"MZ" + b"x" * (len(self.body) - 2)
        self.download()
        self.assertIn("сумма", self.install_errors[0])
        self.assertEqual(self.ready, [])

    def test_truncated_file(self):
        self.body = self.body[:100]
        self.download()
        self.assertIn("не полностью", self.install_errors[0])

    def test_oversized_file(self):
        self.body += b"extra"
        self.download()
        self.assertIn("превышает", self.install_errors[0])

    def test_non_executable(self):
        self.body = b"<html>wrong file</html>"
        self.release = Release("9.0.0", "", self.release.download_url, len(self.body))
        self.download()
        self.assertIn("Windows", self.install_errors[0])

    def test_http_error(self):
        self.status = 404
        self.download()
        self.assertEqual(self.ready, [])
        self.assertIn("скачать", self.install_errors[0])

    def test_missing_asset(self):
        self.release = Release("9.0.0", RELEASES_URL)
        self.download()
        self.assertIn("отсутствует", self.install_errors[0])

    def test_source_run_does_not_replace_python(self):
        with patch.object(sys, "frozen", False):
            self.download()
        self.assertIn("собранном", self.install_errors[0])

    def test_cancel_download_cleans_staging(self):
        self.hold_response = True
        self.installer.start(self.release)
        wait_until(lambda: self.requests)
        directory = self.installer._directory
        self.installer.cancel()
        APP.processEvents()
        self.assertFalse(directory.exists())
        self.assertEqual(self.ready, [])
        self.assertEqual(self.install_errors, [])

    def test_failed_helper_keeps_current_executable(self):
        self.download()
        with patch("core.update_installer.launch_replacement", side_effect=OSError("blocked")):
            self.assertFalse(self.installer.install())
        self.assertEqual(self.target.read_bytes(), b"original")
        self.assertIn("blocked", self.install_errors[0])


class AssetTests(unittest.TestCase):
    def test_selects_exe_and_pins_download_repository(self):
        release = newer_release({"tag_name": "v9.0.0", "assets": [
            {"name": "LogAnalyzer.exe", "state": "uploaded", "size": 50,
             "browser_download_url": "https://wrong.example/evil.exe", "digest": "sha256:" + "a" * 64}]})
        self.assertEqual(release.download_url, f"{RELEASES_URL}/download/v9.0.0/LogAnalyzer.exe")
        self.assertEqual(release.sha256, "a" * 64)
        self.assertEqual(release.size, 50)

    def test_missing_exe_has_no_download(self):
        self.assertEqual(newer_release({"tag_name": "v9.0.0", "assets": []}).download_url, "")

    def test_malformed_assets(self):
        for assets in (None, {}, [None], [{"name": "LogAnalyzer.exe", "state": "uploaded", "size": -1}]):
            with self.subTest(assets=assets), self.assertRaises(ValueError):
                newer_release({"tag_name": "v9.0.0", "assets": assets})


@unittest.skipUnless(os.name == "nt", "Установщик предназначен для Windows")
class ReplacementTests(unittest.TestCase):
    def test_real_powershell_replaces_and_rolls_back_on_start_failure(self):
        # Подмена только запуска приложения и окна ошибки: операции файлов реальные.
        with tempfile.TemporaryDirectory(prefix="кириллица ' $ ") as directory:
            root = Path(directory)
            target = root / "старый ' $ app.exe"
            for fail in (False, True):
                with self.subTest(fail=fail):
                    target.write_bytes(b"old")
                    stage = root / "stage"
                    stage.mkdir()
                    staged = stage / "LogAnalyzer.exe"
                    staged.write_bytes(b"new")
                    script = replacement_script(target, staged, 2147483647)
                    start = "function Start-Process { " + ("throw 'start failure'" if fail else "") + " }\n"
                    script = start + script
                    script = script.replace("    Add-Type -AssemblyName System.Windows.Forms", "")
                    script = script.replace(
                        '[System.Windows.Forms.MessageBox]::Show("Обновление не установлено.`n" + $problem + "`nФайлы: " + $folder, \'Log Analyzer\')',
                        "Write-Output $problem")
                    import base64
                    result = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                         base64.b64encode(script.encode("utf-16le")).decode()],
                        capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(target.read_bytes(), b"old" if fail else b"new")
                    if fail:
                        self.assertTrue((stage / "error.txt").exists())


if __name__ == "__main__":
    unittest.main()
