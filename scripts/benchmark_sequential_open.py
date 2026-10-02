"""Замер большого файла и малого после него в изолированном главном окне.

Запуск: python scripts/benchmark_sequential_open.py large.log --fast --second next.zip
--direct проверяет немедленное открытие, --wait ждёт полного анализа перед следующим файлом.
Малый файл — временная копия первых 2 млн символов, настройки пользователя не меняются.
"""
import gc
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PyQt6.QtWidgets import QApplication
from gui.window import MainWindow
from core.thread_lifecycle import retired_threads
from core.workers import _open_log_stream
from core.fast_text_loader import FastTextLoader


def main():
    app = QApplication([])
    settings = {'theme': 'Default', 'font_size': 10,
                'fast_open_mode': '--fast' in sys.argv, 'fast_view_engine': 'list'}
    if '--engine' in sys.argv:
        settings['fast_view_engine'] = sys.argv[sys.argv.index('--engine') + 1]
    gc_times = []

    def track_gc(phase, info):
        if phase == 'start':
            track_gc.started = time.perf_counter()
        else:
            gc_times.append(time.perf_counter() - track_gc.started)

    gc.callbacks.append(track_gc)
    original_read = FastTextLoader._read_preview

    def timed_read(worker):
        started = time.perf_counter()
        original_read(worker)
        worker.benchmark_read_s = time.perf_counter() - started

    with tempfile.TemporaryDirectory() as directory, \
            patch.object(FastTextLoader, '_read_preview', timed_read), \
            patch('gui.window.load_settings', return_value=settings), \
            patch('config.load_settings', return_value=settings), \
            patch.object(MainWindow, '_sync_file_associations'), \
            patch.object(MainWindow, 'restore_session'), \
            patch.object(MainWindow, 'save_current_settings'):
        small = Path(directory) / 'small.log'
        source, _ = _open_log_stream(sys.argv[1])
        with source:
            data = source.read(2 * 1024 * 1024)
        small.write_text(data.rsplit('\n', 1)[0] + '\n', encoding='utf-8')
        window = MainWindow()
        window.show()
        app.processEvents()
        viewers = []

        def measure(path, label):
            gc_times.clear()
            started = previous = time.perf_counter()
            viewer = window.load_file(str(path), lazy='--direct' not in sys.argv)
            viewers.append(viewer)
            done = []
            viewer.loadingFinished.connect(lambda: done.append(True))
            if '--direct' not in sys.argv:
                window.split_manager.activeTabChanged.emit(viewer)
            gap = 0
            while not done and time.perf_counter() - started < 180:
                app.processEvents()
                now = time.perf_counter()
                gap = max(gap, now - previous)
                previous = now
                time.sleep(.001)
            # Include painting and queued layout work after the completion signal.
            ready = time.perf_counter() - started
            until = time.perf_counter() + .3
            while time.perf_counter() < until:
                app.processEvents()
                now = time.perf_counter()
                gap = max(gap, now - previous)
                previous = now
                time.sleep(.001)
            print(json.dumps(dict(label=label, ready_s=ready, max_ui_gap_s=gap,
                                  gc_s=sum(gc_times), entries=len(viewer.model._entries),
                                  read_s=getattr(getattr(viewer, 'fast_loader', None), 'benchmark_read_s', None),
                                  raw_lines=viewer._fast_total_lines)), flush=True)
            if not done:
                raise TimeoutError(label)
            return viewer

        def settle(viewer):
            started = previous = time.perf_counter()
            gap = 0
            while time.perf_counter() - started < 180:
                app.processEvents()
                now = time.perf_counter()
                gap = max(gap, now - previous)
                previous = now
                loader = viewer.loader
                worker = viewer.model.filter_worker
                if not viewer._loaded:
                    raise RuntimeError('Анализируемая вкладка выгружена лимитом памяти')
                if viewer.model._entries and not (loader and loader.isRunning()) and not (worker and worker.isRunning()):
                    break
                time.sleep(.001)
            else:
                raise TimeoutError('Полный анализ не завершился за 180 секунд')
            print(json.dumps(dict(label='analysis', elapsed_s=time.perf_counter() - started,
                                  max_ui_gap_s=gap)), flush=True)

        try:
            measure(small, 'small_alone')
            large = measure(sys.argv[1], 'large')
            if '--wait' in sys.argv:
                settle(large)
            if '--second' in sys.argv:
                large = measure(sys.argv[sys.argv.index('--second') + 1], 'second_large')
            measure(small, 'small_after_large')
            if '--wait' not in sys.argv:
                settle(large)
            measure(small, 'small_after_analysis')
        finally:
            for viewer in viewers:
                viewer.stop_background_work()
            while retired_threads().workers:
                app.processEvents()
                time.sleep(.001)
            window.close()
            gc.callbacks.remove(track_gc)


if __name__ == '__main__':
    main()
