"""Проверки релизов GitHub, асинхронной сети и пользовательских настроек."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QEvent, QTimer
from PyQt6.QtNetwork import QHostAddress, QTcpServer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QLabel
from config import DEFAULT_UI_FEATURES, load_settings, save_settings
from core.update_checker import Release, RELEASES_URL, UpdateChecker, newer_release
from gui.settings import SettingsDialog
from gui.update_dialog import UpdateAvailableDialog, UpdateController
from gui.window import MainWindow

APP = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    if not predicate():
        raise AssertionError("Qt не завершил ожидаемую операцию")


class ReleaseTests(unittest.TestCase):
    def test_numeric_comparison_and_two_component_versions(self):
        for tag, current, expected in (
            ("v1.10.0", "1.9.0", True),
            ("v1.6.1", "1.6.0", True),
            ("v2.0.0", "1.6.0", True),
            ("v1.6.0", "1.6.0", False),
            ("1.6", "1.6.0", False),
            ("v1.5.9", "1.6.0", False),
        ):
            with self.subTest(tag=tag, current=current):
                release = newer_release({"tag_name": tag}, current)
                self.assertEqual(release is not None, expected)
                if release:
                    self.assertEqual(release.url, f"{RELEASES_URL}/tag/{tag}")

    def test_drafts_and_prereleases_are_ignored(self):
        for flag in ("draft", "prerelease"):
            self.assertIsNone(newer_release({"tag_name": "v9.0.0-rc1", flag: True}))

    def test_invalid_payload_is_not_reported_as_up_to_date(self):
        for payload in ([], None, {}, {"tag_name": 17}, {"tag_name": "release"},
                        {"tag_name": "v1.7.0-rc1"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                newer_release(payload)

    def test_page_url_comes_from_known_repository(self):
        release = newer_release({"tag_name": "v9.0.0", "html_url": "file:///wrong.exe"})
        self.assertEqual(release.url, f"{RELEASES_URL}/tag/v9.0.0")


class LocalServerCase(unittest.TestCase):
    """Локальный HTTP-сервер проверяет настоящий QNetworkAccessManager без GitHub."""

    def setUp(self):
        self.server = QTcpServer()
        self.assertTrue(self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0))
        self.server.newConnection.connect(self._accept_connection)
        self.sockets = []
        self.requests = []
        self.status = 200
        self.body = json.dumps({"tag_name": "v9.0.0"}).encode()
        self.hold_response = False
        url = f"http://127.0.0.1:{self.server.serverPort()}/releases/latest"
        self.url_patch = patch("core.update_checker.LATEST_RELEASE_API", url)
        self.url_patch.start()
        self.checker = UpdateChecker()
        self.completed = []
        self.errors = []
        self.busy = []
        self.checker.completed.connect(self.completed.append)
        self.checker.failed.connect(self.errors.append)
        self.checker.busyChanged.connect(self.busy.append)

    def _accept_connection(self):
        socket = self.server.nextPendingConnection()
        self.sockets.append(socket)
        received = bytearray()

        def respond():
            received.extend(bytes(socket.readAll()))
            if b"\r\n\r\n" not in received:
                return
            self.requests.append(bytes(received))
            if not self.hold_response:
                headers = (f"HTTP/1.1 {self.status} Response\r\n"
                           f"Content-Length: {len(self.body)}\r\n"
                           "Content-Type: application/json\r\nConnection: close\r\n\r\n")
                socket.write(headers.encode() + self.body)
                socket.disconnectFromHost()

        socket.readyRead.connect(respond)

    def tearDown(self):
        self.checker.cancel()
        self.checker.deleteLater()
        for socket in self.sockets:
            socket.abort()
        self.server.close()
        self.server.deleteLater()
        self.url_patch.stop()
        APP.processEvents()


class NetworkTests(LocalServerCase):
    def test_async_success_and_duplicate_click(self):
        self.checker.check()
        self.checker.check()
        self.assertTrue(self.checker.busy)
        self.assertEqual(self.completed, [])
        wait_until(lambda: self.completed or self.errors)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.completed[0].version, "9.0.0")
        self.assertEqual(self.errors, [])
        self.assertEqual(self.busy, [True, False])
        self.assertEqual(len(self.requests), 1)
        self.assertIn(b"User-Agent: LogAnalyzer/", self.requests[0])
        self.assertIn(b"application/vnd.github+json", self.requests[0])

    def test_no_update(self):
        self.body = b'{"tag_name": "v1.0.0"}'
        self.checker.check()
        wait_until(lambda: self.completed)
        self.assertEqual(self.completed, [None])

    def test_http_failures_and_retry(self):
        for status in (403, 404, 429, 500):
            with self.subTest(status=status):
                self.status = status
                self.errors.clear()
                self.checker.check()
                wait_until(lambda: self.errors)
                self.assertFalse(self.checker.busy)
                self.assertEqual(self.completed, [])
        self.status = 200
        self.checker.check()
        wait_until(lambda: self.completed)

    def test_invalid_json_and_missing_version(self):
        for body in (b"not json", b"[]", b"{}", b"\xff"):
            with self.subTest(body=body):
                self.body = body
                self.errors.clear()
                self.checker.check()
                wait_until(lambda: self.errors)
                self.assertEqual(self.completed, [])

    def test_timeout_keeps_event_loop_responsive(self):
        self.hold_response = True
        self.checker._timeout.setInterval(50)
        heartbeat = []
        self.checker.check()
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        wait_until(lambda: self.errors)
        self.assertEqual(heartbeat, [True])
        self.assertEqual(len(self.errors), 1)
        self.assertFalse(self.checker.busy)

    def test_cancel_suppresses_late_result(self):
        self.hold_response = True
        self.checker.check()
        wait_until(lambda: self.requests)
        self.checker.cancel()
        QTest.qWait(30)
        self.assertEqual(self.completed, [])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.busy, [True, False])

    def test_connection_failure(self):
        self.server.close()
        self.checker.check()
        wait_until(lambda: self.errors, timeout=8)
        self.assertEqual(self.completed, [])


class UpdateUiTests(unittest.TestCase):
    def setUp(self):
        self.window = SettingsDialog("Default", 10, DEFAULT_UI_FEATURES)
        self.controller = UpdateController(self.window)
        self.start_patch = patch.object(self.controller.checker, "check")
        self.start = self.start_patch.start()
        self.release = Release("9.0.0", f"{RELEASES_URL}/tag/v9.0.0")

    def tearDown(self):
        self.controller.close()
        self.window.close()
        self.window.deleteLater()
        self.start_patch.stop()
        APP.processEvents()

    def test_automatic_is_quiet_without_update_or_on_error(self):
        self.controller.check_automatically()
        self.start.assert_called_once()
        self.controller.checker.completed.emit(None)
        self.controller.checker.failed.emit("Нет сети")
        self.assertIsNone(self.controller._dialog)

    def test_automatic_new_release_shows_question(self):
        self.controller.check_automatically()
        self.controller.checker.completed.emit(self.release)
        self.assertIsInstance(self.controller._dialog, UpdateAvailableDialog)

    def test_manual_reports_no_update_and_error(self):
        self.controller.check_manually()
        self.controller.checker.completed.emit(None)
        self.assertIn("Новых версий нет", self.controller._dialog.text())
        self.controller._dialog.accept()
        self.controller.check_manually()
        self.controller.checker.failed.emit("Нет сети")
        self.assertEqual(self.controller._dialog.text(), "Нет сети")

    def test_manual_during_automatic_enables_feedback(self):
        self.controller.check_automatically()
        self.controller.check_manually()
        self.controller.checker.completed.emit(None)
        self.assertIsNotNone(self.controller._dialog)

    def test_only_changelog_opens_browser(self):
        with patch("gui.update_dialog.QDesktopServices.openUrl", return_value=True) as open_url:
            for action in ("no", "link", "yes"):
                dialog = UpdateAvailableDialog(self.release, self.window)
                buttons = dialog.findChild(QDialogButtonBox)
                if action == "link":
                    link = next(label for label in dialog.findChildren(QLabel) if "href=" in label.text())
                    link.linkActivated.emit("release")
                else:
                    standard = (QDialogButtonBox.StandardButton.Yes if action == "yes"
                                else QDialogButtonBox.StandardButton.No)
                    buttons.button(standard).click()
                self.assertEqual(open_url.call_count, {"no": 0, "link": 1, "yes": 1}[action])
                if action == "yes":
                    self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
                dialog.deleteLater()
            self.assertEqual(open_url.call_args.args[0].toString(), self.release.url)

    def test_confirmation_starts_download_and_ready_closes_window(self):
        with patch.object(self.controller.installer, "start") as start, \
                patch.object(self.controller.installer, "install", return_value=True) as install, \
                patch.object(self.window, "close") as close:
            self.controller._on_completed(self.release)
            self.controller._dialog.accept()
            start.assert_called_once_with(self.release)
            self.controller.installer.progress.emit(50, 100)
            self.assertEqual(self.controller._dialog.value(), 50)
            self.controller.installer.ready.emit()
            install.assert_called_once()
            close.assert_called_once()

    def test_download_error_keeps_application_open(self):
        with patch.object(self.controller.installer, "start"), patch.object(self.window, "close") as close:
            self.controller._start_update(self.release)
            self.controller.installer.failed.emit("Обрыв соединения")
            self.assertEqual(self.controller._dialog.text(), "Обрыв соединения")
            close.assert_not_called()

    def test_closing_progress_cancels_download(self):
        with patch.object(self.controller.installer, "start"), \
                patch.object(self.controller.installer, "cancel") as cancel:
            self.controller._start_update(self.release)
            self.controller._dialog.close()
            cancel.assert_called_once()
            self.assertIsNone(self.controller._dialog)

    def test_settings_button_and_busy_state(self):
        requests = []
        self.window.checkUpdatesRequested.connect(lambda: requests.append(True))
        self.window.btn_check_updates.click()
        self.assertEqual(requests, [True])
        self.window.set_update_checking(True)
        self.window.btn_check_updates.click()
        self.assertEqual(requests, [True])
        self.assertEqual(self.window.btn_check_updates.text(), "Проверка…")
        self.window.set_update_checking(False)
        self.assertTrue(self.window.btn_check_updates.isEnabled())

    def test_destroyed_dialog_does_not_block_future_checks(self):
        self.controller.check_manually()
        self.controller.checker.completed.emit(self.release)
        self.controller._dialog.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertIsNone(self.controller._dialog)
        self.controller.check_manually()
        self.assertEqual(self.start.call_count, 2)

    def test_result_after_settings_closed_uses_main_window(self):
        with patch.object(self.controller.checker, "cancel"):
            self.window.show()
            transient = SettingsDialog("Default", 10, DEFAULT_UI_FEATURES, parent=self.window)
            transient.open()
            self.controller.check_manually()
            transient.reject()
            transient.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.controller.checker.completed.emit(self.release)
            self.assertIs(self.controller._dialog.parentWidget(), self.window)


class UpdateSettingsTests(unittest.TestCase):
    def test_default_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("config.get_settings_path", return_value=str(Path(directory) / "settings.json")):
                self.assertFalse(load_settings()["auto_check_updates"])
                save_settings({"auto_check_updates": True})
                self.assertTrue(load_settings()["auto_check_updates"])
                save_settings({"auto_check_updates": False})
                self.assertFalse(load_settings()["auto_check_updates"])

    def test_accept_cancel_startup_and_session_save(self):
        with patch("gui.window.load_settings", return_value={}), \
                patch("gui.window.save_settings") as save, \
                patch.object(MainWindow, "_sync_file_associations"), \
                patch.object(MainWindow, "restore_session"):
            window = MainWindow()
            try:
                with patch.object(window.update_controller, "check_automatically") as check:
                    window._update_timer.timeout.emit()
                    check.assert_not_called()

                    def choose(value, accept):
                        dialog = APP.activeModalWidget()
                        self.assertIsInstance(dialog, SettingsDialog)
                        dialog.cb_auto_check_updates.setChecked(value)
                        dialog.accept() if accept else dialog.reject()

                    QTimer.singleShot(0, lambda: choose(True, True))
                    window.open_settings()
                    self.assertTrue(save.call_args.args[0]["auto_check_updates"])
                    window._update_timer.timeout.emit()
                    check.assert_called_once()
                    QTimer.singleShot(0, lambda: choose(False, False))
                    window.open_settings()
                    self.assertTrue(window.settings["auto_check_updates"])
                    QTimer.singleShot(0, lambda: choose(False, True))
                    window.open_settings()
                    self.assertFalse(save.call_args.args[0]["auto_check_updates"])
            finally:
                window.close()
                self.assertFalse(window._update_timer.isActive())
                window.deleteLater()


if __name__ == "__main__":
    unittest.main()
