"""После открытия видна последняя запись, в том числе при порционной раскладке Qt."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PyQt6.QtWidgets import QApplication
from core.thread_lifecycle import retired_threads
from gui.log_viewer import LogViewerWidget

APP = QApplication.instance() or QApplication([])


def pump_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.001)
    if not predicate():
        raise AssertionError('Last log entry did not become visible')


class InitialScrollTests(unittest.TestCase):
    def assert_last_entry_visible(self, viewer, count):
        view = viewer.log_view

        def last_is_visible():
            if viewer.model.rowCount() != count:
                return False
            rect = view.visualRect(viewer.model.index(count - 1))
            return rect.isValid() and view.viewport().rect().contains(rect.bottomLeft())

        pump_until(last_is_visible)
        scrollbar = view.verticalScrollBar()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())

    def test_normal_open_and_reload_finish_at_last_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'normal.log'
            path.write_text(''.join(f'10:00:00.000 [INFO] [Test.run]: row {i}\n'
                                    for i in range(6500)), encoding='utf-8')
            with patch('config.load_settings', return_value={'fast_open_mode': False}):
                viewer = LogViewerWidget(str(path), 'Default', 10, lazy=True)
                viewer.resize(1000, 700)
                viewer.show()
                filtered = []
                viewer.model.filterFinished.connect(lambda: filtered.append(True))
                try:
                    for _ in range(2):
                        filtered.clear()
                        viewer.ensure_loaded()
                        pump_until(lambda: bool(filtered))
                        self.assert_last_entry_visible(viewer, 6500)
                        # Переход вниз при открытии не включает постоянное слежение.
                        viewer.log_view.scrollToTop()
                        APP.processEvents()
                        self.assertEqual(viewer.log_view.verticalScrollBar().value(), 0)
                        viewer.unload()
                        pump_until(lambda: not retired_threads().workers)
                finally:
                    viewer.stop_background_work()
                    viewer.close()
                    pump_until(lambda: not retired_threads().workers)
                    viewer.deleteLater()

    def test_fast_engines_show_file_end_before_and_after_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fast.log'
            lines = [f'10:00:00.000 [ERROR] [Test.run]: row {i}\n' for i in range(6500)]
            path.write_text(''.join(lines), encoding='utf-8')
            for engine in ('list', 'full', 'limited'):
                settings = {'fast_open_mode': True, 'fast_view_engine': engine}
                with self.subTest(engine=engine), patch('config.load_settings', return_value=settings):
                    viewer = LogViewerWidget(str(path), 'Default', 10, lazy=True)
                    if engine == 'limited':
                        viewer.LIMITED_VIEW_LINES = 200
                    viewer.resize(1000, 700)
                    viewer.show()
                    loaded, filtered = [], []
                    viewer.loadingFinished.connect(lambda: loaded.append(True))
                    viewer.model.filterFinished.connect(lambda: filtered.append(True))
                    try:
                        # Проверяем завершённый быстрый просмотр до запуска анализа.
                        with patch.object(viewer, 'load_file'):
                            viewer.ensure_loaded()
                            pump_until(lambda: bool(loaded))
                        APP.processEvents()
                        view = viewer.text_view
                        scrollbar = view.verticalScrollBar()
                        self.assertGreater(scrollbar.maximum(), 0)
                        self.assertEqual(scrollbar.value(), scrollbar.maximum())
                        if engine == 'list':
                            rect = view.visualRect(view.model().index(len(lines) - 1))
                            self.assertTrue(view.viewport().rect().contains(rect.bottomLeft()))
                        else:
                            self.assertTrue(view.toPlainText().endswith(lines[-1]))
                        if engine == 'limited':
                            self.assertTrue(view.toPlainText().startswith(lines[-200]))
                            self.assertEqual(viewer._fast_markers[0], (1, 'ERROR'))
                            self.assertEqual(viewer._fast_markers[-1], (200, 'ERROR'))
                            self.assertIn('последние', viewer.lbl_stats.text())
                        viewer.load_file()
                        pump_until(lambda: bool(filtered))
                        self.assert_last_entry_visible(viewer, len(lines))
                    finally:
                        viewer.stop_background_work()
                        viewer.close()
                        pump_until(lambda: not retired_threads().workers)
                        viewer.deleteLater()


if __name__ == '__main__':
    unittest.main()
