"""Регрессии загрузки, фоновой подготовки и закрытия активных вкладок."""
import gzip
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication
from config import THEMES
from core.batch_index import parse_batch_index
from core.models import LogModel
from core.prepared_log import prepare_log
from core.thread_lifecycle import retired_threads
from core.workers import IncrementalLogParser, LogLoader
from gui.log_viewer import LogViewerWidget
from gui.tab_manager import EditorTabWidget

APP = QApplication.instance() or QApplication([])
TEXT = ('leading text\n'
        '10:00:00.000 [INFO] [CustomLogFilter.call]: setCurrentBatch?batchId=7\n'
        '10:00:01.000 [ERROR] [HIKROBOT.run]: ошибка\n  stack trace\n'
        '10:00:02.000 [INFO] [CustomLogFilter.call]: /api/close\n'
        '10:00:03.000 [WARN] [DataService.run]: warning\n')


def pump_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.001)
    if not predicate():
        raise AssertionError('Background work did not finish')


class LoadingTests(unittest.TestCase):
    def tearDown(self):
        pump_until(lambda: not retired_threads().workers)

    def test_parser_preserves_multiline_entries_across_chunk_boundaries(self):
        lines = [' leading text\n', '\n',
                 '10:00:00.000 [ INFO ][HIKROBOT.run]: начало\n',
                 '  продолжение\n', '\n',
                 '10:00:01.000 [ERROR] [Test.run]: ошибка\n',
                 ' trace without final newline']
        expected = [('', 'UNKNOWN', '', 'leading text\n'),
                    ('10:00:00.000', 'INFO', 'HIKROBOT',
                     '10:00:00.000 [ INFO ][HIKROBOT.run]: начало\nпродолжение\n'),
                    ('10:00:01.000', 'ERROR', 'Test',
                     '10:00:01.000 [ERROR] [Test.run]: ошибка\ntrace without final newline')]
        for size in range(1, len(lines) + 1):
            with self.subTest(chunk_size=size):
                parser = IncrementalLogParser()
                entries = []
                for start in range(0, len(lines), size):
                    entries.extend(parser.feed_lines(lines[start:start + size]))
                entries.append(parser.take_open())
                self.assertEqual([(e.timestamp, e.level, e.logger, e.message) for e in entries], expected)
                self.assertEqual(parser.stats, {'INFO': 1, 'DEBUG': 0, 'ERROR': 1, 'WARN': 0})

    def test_archive_progress_uses_uncompressed_bytes_with_invalid_utf8(self):
        data = ('10:00:00.000 [INFO] [Test.run]: кириллица\r\n' * 1100).encode('utf-8') + b'\xff\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'x.zip'
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('x.log', data)
            worker = LogLoader(str(path))
            progress, results = [], []
            worker.progress.connect(progress.append)
            worker.finished.connect(lambda *args: results.append(args))
            worker.run()
            self.assertEqual(results[0][2], '')
            self.assertEqual(len(results[0][0]), 1100)
            self.assertTrue(results[0][0][-1].message.endswith('\n\ufffd'))
            self.assertEqual(progress[-1], 100)
            self.assertEqual(progress, sorted(progress))
            self.assertTrue(all(0 <= value <= 100 for value in progress))

    def test_plain_and_archives_preserve_parser_results(self):
        parser = IncrementalLogParser()
        expected = parser.feed_lines(TEXT.splitlines(keepends=True))
        expected.append(parser.take_open())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'x.log').write_text(TEXT, encoding='utf-8')
            with gzip.open(root / 'x.gz', 'wt', encoding='utf-8') as stream:
                stream.write(TEXT)
            with zipfile.ZipFile(root / 'x.zip', 'w') as archive:
                archive.writestr('x.log', TEXT)
            for path in root.iterdir():
                with self.subTest(path=path.suffix):
                    worker = LogLoader(str(path))
                    results = []
                    worker.finished.connect(lambda *args: results.append(args))
                    worker.run()
                    entries, stats, error, pos = results[0]
                    self.assertEqual(error, '')
                    self.assertEqual([e.full_line for e in entries], [e.full_line for e in expected])
                    self.assertEqual(stats, parser.stats)
                    self.assertEqual(worker.prepared.batch_for_index, ['', '7', '7', '7', ''])
                    self.assertEqual(worker.prepared.loggers, ['CustomLogFilter', 'DataService', 'HIKROBOT'])
                    if path.suffix == '.log':
                        self.assertEqual(pos, path.stat().st_size)
                    else:
                        self.assertEqual(pos, 0)

    def test_publication_does_not_copy_large_python_lists(self):
        worker = LogLoader('unused')
        received = []
        worker.finished.connect(lambda entries, *_: received.append(entries))
        entries = [object()]
        worker.finished.emit(entries, {}, '', 2**33)
        self.assertIs(received[0], entries)

    def test_prepared_model_does_not_rescan_in_ui(self):
        parser = IncrementalLogParser()
        entries = parser.feed_lines(TEXT.splitlines(keepends=True))
        entries.append(parser.take_open())
        prepared = prepare_log(entries, lambda: False)
        model = LogModel()
        with patch('core.models.detect_log_profile', side_effect=AssertionError('UI scan')):
            with patch.object(model, '_parse_batches', side_effect=AssertionError('UI scan')):
                model.set_entries(entries, prepared=prepared, apply_filters=False)
        self.assertIs(model._entries, entries)
        self.assertIs(model._filtered_indices, prepared.indices)
        self.assertEqual(model.get_batch_for_index(2), '7')

    def test_cancel_during_preparation_publishes_no_partial_data(self):
        worker = LogLoader('unused.zip')
        results = []
        worker.finished.connect(lambda *args: results.append(args))
        with patch('core.workers._open_log_stream', return_value=(io.StringIO(TEXT), len(TEXT))):
            with patch('core.prepared_log.prepare_log', side_effect=InterruptedError):
                worker.run()
        self.assertEqual(results, [([], {}, '__CANCELLED__', 0)])
        self.assertIsNone(worker.prepared)

    def test_batch_cancellation_and_tail_match_complete_parse(self):
        parser = IncrementalLogParser()
        entries = parser.feed_lines(TEXT.splitlines(keepends=True))
        entries.append(parser.take_open())
        with self.assertRaises(InterruptedError):
            parse_batch_index(entries, cancelled=lambda: True)
        segments, indices = parse_batch_index(entries[:3])
        actual = parse_batch_index(entries, 3, segments, indices)
        self.assertEqual(actual, parse_batch_index(entries))

    def test_close_tab_retains_slow_worker_without_blocking(self):
        release = threading.Event()

        class SlowWorker(QThread):
            def run(self):
                release.wait(3)
        tabs = EditorTabWidget()
        viewer = LogViewerWidget('unused.log', next(iter(THEMES)), 10, lazy=True)
        tabs.addTab(viewer, 'test')
        worker = SlowWorker()
        viewer.loader = worker
        worker.start()
        try:
            started = time.monotonic()
            tabs.close_tab(0)
            self.assertLess(time.monotonic() - started, .1)
            self.assertIn(worker, retired_threads().workers)
            self.assertTrue(worker.isInterruptionRequested())
        finally:
            release.set()
        pump_until(lambda: not retired_threads().workers)
        self.assertEqual(tabs.count(), 0)

    def test_large_fast_preview_uses_virtual_list(self):
        viewer = LogViewerWidget('unused.log', next(iter(THEMES)), 10, lazy=True)
        viewer._ensure_fast_view_engine('full')
        lines = ['10:00:00.000 [INFO] [A.run]: test\n'] * (viewer.LIMITED_VIEW_LINES + 1)
        from core.fast_text_loader import FastTextLoader
        viewer.fast_loader = FastTextLoader('unused.log')
        viewer.fast_loader.finished.connect(viewer._on_fast_finished)
        with patch.object(viewer, 'load_file'):
            viewer.fast_loader.finished.emit(lines, [], len(lines), '')
        self.assertEqual(viewer._fast_engine, 'list')
        self.assertEqual(viewer._raw_lines_model.rowCount(), len(lines))
        viewer.unload()
        self.assertEqual(viewer._raw_lines_model.rowCount(), 0)

    def test_group_statistics_import_and_shared_batch_rules(self):
        from core.group_stats_worker import _extract_batch_open_marker
        self.assertEqual(_extract_batch_open_marker('setCurrentBatch?batchId=7')[0], '7')

    def test_group_cancel_inside_file_closes_stream(self):
        from core.group_stats_worker import GroupStatsWorker
        worker = GroupStatsWorker(['unused'])
        stream = io.StringIO(TEXT * 1000)
        results = []
        worker.finished.connect(lambda *args: results.append(args))
        with patch('core.group_stats_worker._open_log_stream', return_value=(stream, 0)):
            # Первый вызов перед файлом, второй уже внутри прохода.
            with patch.object(worker, 'isInterruptionRequested', side_effect=[False, True]):
                worker.run()
        self.assertTrue(stream.closed)
        self.assertEqual(results, [({}, '__CANCELLED__')])

    def test_7z_loading_after_lazy_import(self):
        import py7zr
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'x.log'
            source.write_text(TEXT, encoding='utf-8')
            archive = Path(directory) / 'x.7z'
            with py7zr.SevenZipFile(archive, 'w') as output:
                output.write(source, 'x.log')
            worker = LogLoader(str(archive))
            results = []
            worker.finished.connect(lambda *args: results.append(args))
            worker.run()
            self.assertEqual(results[0][2], '')
            self.assertEqual(len(results[0][0]), 5)

    def test_window_close_waits_asynchronously_and_saves_once(self):
        from gui.window import MainWindow
        release = threading.Event()

        class SlowWorker(QThread):
            def run(self):
                release.wait(3)

        with patch('gui.window.load_settings', return_value={'theme': 'Default', 'font_size': 10}):
            with patch.object(MainWindow, '_sync_file_associations'):
                window = MainWindow()
        window.show()
        APP.processEvents()
        worker = SlowWorker()
        worker.start()
        retired_threads().retire(worker)
        try:
            with patch.object(window, 'save_current_settings') as save:
                started = time.monotonic()
                window.close()
                self.assertLess(time.monotonic() - started, .1)
                self.assertTrue(window.isVisible())
                release.set()
                pump_until(lambda: not window.isVisible())
                save.assert_called_once()
        finally:
            release.set()
            pump_until(lambda: not retired_threads().workers)

    def test_closed_viewer_releases_lru_and_busy_indicator(self):
        from PyQt6.QtCore import QCoreApplication, QEvent
        from gui.window import MainWindow
        with patch('gui.window.load_settings', return_value={'theme': 'Default', 'font_size': 10}):
            with patch.object(MainWindow, '_sync_file_associations'):
                window = MainWindow()
        APP.processEvents()
        viewer = window.load_file('unused.log', lazy=True)
        window._lru_loaded.append(viewer)
        window.progress_bar.add(viewer, 'test')
        window.btn_open.setEnabled(False)
        # Удаление самой вкладки не должно оставлять Python-ссылки на её данные.
        viewer.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertNotIn(viewer, window._lru_loaded)
        self.assertFalse(window.progress_bar.has_active())
        self.assertTrue(window.btn_open.isEnabled())
        with patch.object(window, 'save_current_settings'):
            window.close()

    def test_cancel_ignores_queued_result_and_allows_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'x.log'
            path.write_text(TEXT, encoding='utf-8')
            viewer = LogViewerWidget(str(path), next(iter(THEMES)), 10, lazy=True)
            viewer.load_file()
            viewer.loader.wait()
            viewer.cancel_load()
            APP.processEvents()
            self.assertFalse(viewer._loaded)
            self.assertEqual(viewer.model.rowCount(), 0)
            results = []
            viewer.loadingFinished.connect(lambda: results.append(True))
            viewer.ensure_loaded()
            pump_until(lambda: bool(results))
            self.assertGreater(viewer.model.rowCount(), 0)
            viewer.unload()
            self.assertEqual(viewer.model.rowCount(), 0)
            self.assertIsNone(viewer.model.filter_worker)


if __name__ == '__main__':
    unittest.main()
