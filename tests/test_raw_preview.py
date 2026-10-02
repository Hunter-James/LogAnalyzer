"""Полнота компактного буфера и отрисовка больших журналов без полного обхода модели."""
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication
from core.fast_text_loader import FastTextLoader
from core.raw_log_buffer import RawLogBuffer
from gui.custom_widgets import RawLinesModel
from gui.log_viewer import LogViewerWidget
from gui.raw_log_view import RawLogView

APP = QApplication.instance() or QApplication([])


class RawPreviewTests(unittest.TestCase):
    def test_utf8_newlines_and_markers_across_block_boundaries(self):
        data = ('начало\r\n10:00:00.000 [ERROR] ошибка\r'
                '10:00:01.000 [WARN] предупреждение\n'
                'text WARN [ERROR]\n'
                'x' * 2).encode('utf-8')
        data += b'\xff\n\r\nlast\r'
        expected = list(io.TextIOWrapper(io.BytesIO(data), encoding='utf-8', errors='replace'))
        markers = []
        for index, line in enumerate(expected, 1):
            head = line[:30]
            if '[' in head:
                if 'ERROR' in head:
                    markers.append((index, 'ERROR'))
                elif 'WARN' in head:
                    markers.append((index, 'WARN'))
        for size in (1, 2, 3, 4, 7, 29, 64, 1024):
            with self.subTest(size=size), patch.object(RawLogBuffer, 'BLOCK_SIZE', size):
                result = RawLogBuffer.read(io.BytesIO(data), lambda: False, lambda _: None)
                self.assertEqual(list(result), expected)
                self.assertEqual(result[::-1], expected[::-1])
                self.assertEqual(result[-1], expected[-1])
                self.assertEqual(result.markers(lambda: False), markers)
                with self.assertRaises(IndexError):
                    result[len(result)]

    def test_empty_and_final_line_without_newline(self):
        for data in (b'', b'one', b'\n', b'one\ntwo', b'\r\n\r'):
            with self.subTest(data=data):
                result = RawLogBuffer.read(io.BytesIO(data), lambda: False, lambda _: None)
                expected = list(io.TextIOWrapper(io.BytesIO(data), encoding='utf-8'))
                self.assertEqual(list(result), expected)

    def test_preview_is_one_independent_snapshot_before_eof(self):
        data = b'row\n' * 2000
        stream = io.BytesIO(data)
        snapshots = []
        with patch.object(RawLogBuffer, 'BLOCK_SIZE', 1024):
            result = RawLogBuffer.read(stream, lambda: False, lambda _: None,
                                       lambda lines: snapshots.append((lines, stream.tell())))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(len(snapshots[0][0]), 256)
        self.assertLess(snapshots[0][1], len(data))
        self.assertEqual(len(result), 2000)
        self.assertEqual(len(snapshots[0][0]), 256)

    def test_cancel_read_and_marker_scan(self):
        with self.assertRaises(InterruptedError):
            RawLogBuffer.read(io.BytesIO(b'row\n'), lambda: True, lambda _: None)
        result = RawLogBuffer.read(io.BytesIO(b'[ERROR]\n'), lambda: False, lambda _: None)
        with self.assertRaises(InterruptedError):
            result.markers(lambda: True)

    def test_fast_loader_uses_compact_buffer_and_publishes_preview(self):
        data = b'10:00:00.000 [WARN] text\n' * 600
        stream = io.TextIOWrapper(io.BytesIO(data), encoding='utf-8')
        worker = FastTextLoader('unused.zip')
        results, previews = [], []
        worker.finished.connect(lambda *args: results.append(args))
        worker.previewReady.connect(previews.append)
        with patch('core.fast_text_loader._open_log_stream', return_value=(stream, 8 * 1024 * 1024)):
            worker.run()
        self.assertTrue(stream.closed)
        self.assertEqual(len(previews), 1)
        self.assertIsInstance(results[0][0], RawLogBuffer)
        self.assertEqual(results[0][2:], (600, ''))
        self.assertEqual(len(results[0][1]), 600)

    def test_partial_preview_does_not_finish_loading_and_cancel_ignores_late_preview(self):
        viewer = LogViewerWidget('unused.zip', 'Default', 10, lazy=True)
        worker = FastTextLoader('unused.zip')
        viewer.fast_loader = worker
        worker.previewReady.connect(viewer._on_fast_preview)
        finished, ready = [], []
        viewer.loadingFinished.connect(lambda: finished.append(True))
        viewer.previewReady.connect(lambda: ready.append(True))
        worker.previewReady.emit(('first\n', 'second\n'))
        self.assertTrue(viewer._loaded)
        self.assertEqual(viewer._raw_lines_model.rowCount(), 2)
        self.assertEqual(ready, [True])
        self.assertEqual(finished, [])
        viewer.unload()
        worker.previewReady.emit(('stale\n',))
        self.assertEqual(viewer._raw_lines_model.rowCount(), 0)
        self.assertFalse(viewer._loaded)
        viewer.deleteLater()

    def test_view_paints_and_scrolls_millions_of_rows_with_bounded_access(self):
        class Rows:
            calls = 0

            def __len__(self):
                return 3_200_000

            def __getitem__(self, index):
                self.calls += 1
                if self.calls > 10_000:
                    raise AssertionError('View traverses the entire log')
                return f'line {index}\n'

        rows = Rows()
        view = RawLogView()
        model = RawLinesModel(rows)
        view.setModel(model)
        painted = []
        view.firstRowsPainted.connect(painted.append)
        view.resize(800, 500)
        view.show()
        try:
            APP.processEvents()
            view.grab()
            self.assertTrue(painted)
            self.assertTrue(view.indexAt(QPoint(5, 5)).isValid())
            view.scrollToBottom()
            APP.processEvents()
            self.assertFalse(view.visualRect(model.index(len(rows) - 1)).isEmpty())
            self.assertLess(rows.calls, 10_000)
        finally:
            view.close()
            view.deleteLater()


if __name__ == '__main__':
    unittest.main()
