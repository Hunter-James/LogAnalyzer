"""Файлы из командной строки и IPC открываются после восстановления сессии."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PyQt6.QtNetwork import QLocalSocket
from PyQt6.QtWidgets import QApplication
from core.single_instance import SingleInstanceChannel
from core.thread_lifecycle import retired_threads
from gui.startup import show_main_window
from gui.window import MainWindow

APP = QApplication.instance() or QApplication([])


def pump_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.001)
    if not predicate():
        raise AssertionError('Открытие файла не завершилось')


class StartupFilesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.old = self.make_log('Старая вкладка.log')
        self.target = self.make_log('Нужный журнал.log')
        self.second = self.make_log('Второй журнал.log')
        self.settings = dict(theme='Default', font_size=10,
                             files_per_group=[[], []], active_group_index=0)
        self.patches = [
            patch('gui.window.load_settings', return_value=self.settings),
            patch('config.load_settings', return_value=self.settings),
            patch.object(MainWindow, '_sync_file_associations'),
            patch.object(MainWindow, 'save_current_settings'),
        ]
        for context in self.patches:
            context.start()
        self.channel = SingleInstanceChannel('LogAnalyzer-test-' + uuid4().hex)
        self.window = None
        self.sockets = []

    def tearDown(self):
        if self.window is not None:
            self.window.close()
            pump_until(lambda: not self.window.isVisible() and not retired_threads().workers)
            self.window.deleteLater()
            APP.processEvents()
        for socket in self.sockets:
            socket.abort()
        self.channel.close()
        for context in reversed(self.patches):
            context.stop()
        self.directory.cleanup()

    def make_log(self, name):
        path = Path(self.directory.name) / name
        path.write_text('10:00:00.000 [INFO] [Test.run]: Проверка открытия\n', encoding='utf-8')
        return str(path)

    def viewers(self):
        return [pane.widget(i)
                for panel in self.window.split_manager.iter_panels()
                for pane in panel.panes()
                for i in range(pane.count())]

    def assert_open_once_and_active(self, path):
        try:
            pump_until(lambda: path in self.window.windowTitle()
                       and any(v.file_path == path and v.model.rowCount() == 1 for v in self.viewers()))
        except AssertionError:
            self.fail(f'Ожидался {path}; заголовок: {self.window.windowTitle()}; '
                      f'вкладки: {[(v.file_path, v.model.rowCount(), v._loaded) for v in self.viewers()]}')
        self.assertEqual(sum(v.file_path == path for v in self.viewers()), 1)

    def send_request(self, paths):
        socket = QLocalSocket()
        self.sockets.append(socket)
        socket.connectToServer(self.channel.server_name)
        self.assertTrue(socket.waitForConnected(1000), socket.errorString())
        socket.write(json.dumps({'files': paths}, ensure_ascii=False).encode('utf-8') + b'\n')
        socket.flush()
        return socket

    def test_startup_file_wins_over_restored_session_without_duplicate(self):
        self.settings['files_per_group'] = [[self.old], [self.target]]
        self.window = show_main_window(self.channel, [self.target])
        self.assert_open_once_and_active(self.target)
        self.assertEqual(len(self.viewers()), 2)

    def test_new_startup_file_is_active_after_restoring_old_tab(self):
        self.settings['files_per_group'] = [[self.old], []]
        self.window = show_main_window(self.channel, [self.target])
        self.assert_open_once_and_active(self.target)
        self.assertEqual(len(self.viewers()), 2)

    def test_request_during_window_construction_is_not_lost(self):
        self.assertTrue(self.channel.start_or_forward([]))
        socket = self.send_request([self.target])
        original_apply_theme = MainWindow.apply_theme

        def apply_theme(window, theme):
            original_apply_theme(window, theme)
            # ACK подтверждает обработку настоящего IPC ещё в конструкторе.
            pump_until(lambda: socket.bytesAvailable() > 0)

        with patch.object(MainWindow, 'apply_theme', apply_theme):
            self.window = show_main_window(self.channel, [])
        self.assertEqual(bytes(socket.readAll()), b'OK\n')
        self.assert_open_once_and_active(self.target)

    def test_request_during_restore_follows_initial_file(self):
        self.settings['files_per_group'] = [[self.old], []]
        self.assertTrue(self.channel.start_or_forward([]))
        original_show_busy = MainWindow.show_busy
        sockets = []

        def show_busy(window, text, cancel_callback=None):
            if text.startswith('Восстановление сессии:'):
                socket = self.send_request([self.second])
                sockets.append(socket)
                pump_until(lambda: socket.bytesAvailable() > 0)
            original_show_busy(window, text, cancel_callback)

        with patch.object(MainWindow, 'show_busy', show_busy):
            self.window = show_main_window(self.channel, [self.target])
            self.assert_open_once_and_active(self.second)
        self.assertEqual(len(sockets), 1)
        self.assertEqual([v.file_path for v in self.viewers()], [self.old, self.target, self.second])

    def test_repeated_request_activates_existing_tab(self):
        self.window = show_main_window(self.channel, [self.target])
        self.assert_open_once_and_active(self.target)
        self.channel.requestReceived.emit([self.second])
        self.assert_open_once_and_active(self.second)
        self.channel.requestReceived.emit([self.target, self.target])
        self.assert_open_once_and_active(self.target)
        self.assertEqual(len(self.viewers()), 2)

    def test_no_arguments_preserve_session_and_empty_request_restores_window(self):
        self.settings['files_per_group'] = [[self.old], []]
        self.window = show_main_window(self.channel, [])
        self.assert_open_once_and_active(self.old)
        self.window.showMinimized()
        APP.processEvents()
        self.channel.requestReceived.emit([])
        self.assertFalse(self.window.isMinimized())
        self.assertEqual(len(self.viewers()), 1)


if __name__ == '__main__':
    unittest.main()
