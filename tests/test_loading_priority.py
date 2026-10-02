"""Быстрый просмотр не конкурирует с полным анализом предыдущих вкладок."""
import io
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PyQt6.QtWidgets import QApplication
from core.fast_text_loader import FastTextLoader
from core.loading_priority import LoadingPriority, loading_priority
from core.workers import FilterWorker, LogLoader
from core.entities import LogEntry
from gui.log_viewer import LogViewerWidget

APP = QApplication.instance() or QApplication([])


class LoadingPriorityTests(unittest.TestCase):
    def test_new_analysis_pauses_old_and_then_resumes_it(self):
        priority = LoadingPriority()
        waiting = threading.Event()
        resumed = threading.Event()

        def cancelled():
            waiting.set()
            return False

        with priority.analysis() as old:
            with priority.analysis() as new:
                thread = threading.Thread(target=lambda: (
                    priority.cancelled_after_preview(cancelled, old), resumed.set()), daemon=True)
                thread.start()
                self.assertTrue(waiting.wait(1))
                self.assertFalse(resumed.is_set())
                self.assertFalse(priority.cancelled_after_preview(lambda: False, new))
                self.assertTrue(priority.cancelled_after_preview(lambda: True, old))
            self.assertTrue(resumed.wait(1))
            thread.join(1)

    def test_analysis_resumes_only_after_all_previews_finish(self):
        priority = LoadingPriority()
        waiting = threading.Event()
        resumed = threading.Event()

        def cancelled():
            waiting.set()
            return False

        def analyze():
            priority.cancelled_after_preview(cancelled)
            resumed.set()

        with priority.preview():
            with priority.preview():
                thread = threading.Thread(target=analyze)
                thread.start()
                self.assertTrue(waiting.wait(1))
                self.assertFalse(resumed.is_set())
            self.assertFalse(resumed.is_set())
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(resumed.is_set())

    def test_preview_error_releases_analysis(self):
        priority = LoadingPriority()
        with self.assertRaises(ValueError):
            with priority.preview():
                raise ValueError('read failed')
        resumed = threading.Event()
        thread = threading.Thread(target=lambda: (
            priority.cancelled_after_preview(lambda: False), resumed.set()), daemon=True)
        thread.start()
        self.assertTrue(resumed.wait(1))
        thread.join(1)

    def test_cancel_loader_while_preview_is_reading(self):
        worker = LogLoader('unused.log')
        stream = io.StringIO('10:00:00.000 [INFO] [Test.run]: text\n')
        waiting = threading.Event()
        results = []
        worker.finished.connect(lambda *args: results.append(args))
        original = worker._cancelled_after_preview

        def checkpoint():
            waiting.set()
            return original()

        with patch('core.workers._open_log_stream', return_value=(stream, 50)), \
                patch.object(worker, '_cancelled_after_preview', side_effect=checkpoint):
            with loading_priority.preview():
                worker.start()
                try:
                    self.assertTrue(waiting.wait(1))
                finally:
                    worker.requestInterruption()
                    stopped = worker.wait(1000)
            worker.wait()
        APP.processEvents()
        self.assertTrue(stopped)
        self.assertTrue(stream.closed)
        self.assertEqual(results[0][2], '__CANCELLED__')

    def test_loader_resumes_with_complete_result(self):
        worker = LogLoader('unused.zip')
        stream = io.StringIO('10:00:00.000 [INFO] [Test.run]: text\n' * 1025)
        waiting = threading.Event()
        results = []
        worker.finished.connect(lambda *args: results.append(args))
        original = worker._cancelled_after_preview

        def checkpoint():
            waiting.set()
            return original()

        with patch('core.workers._open_log_stream', return_value=(stream, 0)), \
                patch.object(worker, '_cancelled_after_preview', side_effect=checkpoint):
            with loading_priority.preview():
                worker.start()
                self.assertTrue(waiting.wait(1))
                self.assertEqual(stream.tell(), 0)
            try:
                self.assertTrue(worker.wait(3000))
            finally:
                worker.requestInterruption()
                worker.wait()
        APP.processEvents()
        self.assertEqual(results[0][2], '')
        self.assertEqual(len(results[0][0]), 1025)
        self.assertEqual(results[0][1]['INFO'], 1025)
        self.assertEqual(len(worker.prepared.indices), 1025)

    def test_cancel_filter_while_preview_is_reading(self):
        worker = FilterWorker([LogEntry('', 'INFO', '', 'text')], True, True, True, True, '')
        waiting = threading.Event()
        original = worker._cancelled_after_preview

        def checkpoint():
            waiting.set()
            return original()

        with patch.object(worker, '_cancelled_after_preview', side_effect=checkpoint):
            with loading_priority.preview():
                worker.start()
                try:
                    self.assertTrue(waiting.wait(1))
                finally:
                    worker.cancel()
                    stopped = worker.wait(1000)
            worker.wait()
        self.assertTrue(stopped)
        self.assertEqual(worker.visible_indices, [])

    def test_fast_loader_holds_priority_during_read_and_releases_on_error(self):
        priority = LoadingPriority()
        observations = []

        def open_stream(_path):
            # Cancellation callback is called in the waiting branch, then returns True.
            observations.append(priority.cancelled_after_preview(lambda: True))
            self.assertEqual(priority._readers, 1)
            raise OSError('read failed')

        with patch('core.fast_text_loader.loading_priority', priority), \
                patch('core.fast_text_loader._open_log_stream', side_effect=open_stream):
            worker = FastTextLoader('unused')
            worker.run()
        self.assertEqual(observations, [True])
        self.assertEqual(priority._readers, 0)

    def test_immediate_open_honors_fast_mode(self):
        for fast in (True, False):
            with self.subTest(fast=fast), \
                    patch('config.load_settings', return_value={'fast_open_mode': fast}), \
                    patch.object(LogViewerWidget, 'start_fast_load') as preview, \
                    patch.object(LogViewerWidget, 'load_file') as full:
                viewer = LogViewerWidget('unused.log', 'Default', 10)
                self.assertEqual(preview.call_count, int(fast))
                self.assertEqual(full.call_count, int(not fast))
                viewer.stop_background_work()
                viewer.deleteLater()

    def test_full_preview_uses_list_for_large_text_and_long_lines(self):
        samples = [
            ['x' * 1000 + '\n'] * 2000,
            ['x' * (LogViewerWidget.FULL_VIEW_MAX_LINE_CHARS + 1)],
        ]
        for lines in samples:
            with self.subTest(lines=len(lines)):
                viewer = LogViewerWidget('unused.log', 'Default', 10, lazy=True)
                viewer._ensure_fast_view_engine('full')
                viewer.fast_loader = FastTextLoader('unused.log')
                viewer.fast_loader.finished.connect(viewer._on_fast_finished)
                with patch.object(viewer, 'load_file'):
                    viewer.fast_loader.finished.emit(lines, [], len(lines), '')
                self.assertEqual(viewer._fast_engine, 'list')
                self.assertIs(viewer._raw_lines_model._lines, lines)
                self.assertEqual(viewer._raw_lines_model.rowCount(), len(lines))
                viewer.unload()
                viewer.deleteLater()
        self.assertFalse(LogViewerWidget._needs_virtual_fast_view(['short\n'] * 10, 10))


if __name__ == '__main__':
    unittest.main()
