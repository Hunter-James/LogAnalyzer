"""Замер открытия файла и задержек обработки событий Qt без видимого окна.

Запуск: python scripts/benchmark_open.py path/to/file.log
max_ui_gap_s включает паузу планировщика ОС и обработку события загрузки.
"""
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

started = time.perf_counter()
from PyQt6.QtWidgets import QApplication
from config import THEMES
from gui.log_viewer import LogViewerWidget
IMPORT_SECONDS = time.perf_counter() - started


def main():
    app = QApplication([])
    viewer = LogViewerWidget(sys.argv[1], next(iter(THEMES)), 10, lazy=True)
    results = []
    viewer.loadingFinished.connect(lambda: results.append(True))
    started = time.perf_counter()
    viewer.load_file()
    gap = 0
    previous = started
    try:
        while not results and time.perf_counter() - started < 120:
            app.processEvents()
            now = time.perf_counter()
            gap = max(gap, now - previous)
            previous = now
            time.sleep(.001)
        if not results:
            raise TimeoutError('Загрузка не завершилась за 120 секунд')
        print(json.dumps(dict(import_s=IMPORT_SECONDS, open_s=time.perf_counter() - started,
                              max_ui_gap_s=gap, entries=len(viewer.model._entries))))
    finally:
        for attr in ('loader', 'fast_loader'):
            worker = getattr(viewer, attr, None)
            if worker:
                worker.requestInterruption()
                worker.wait()
        viewer.model.stop_filtering()


if __name__ == '__main__':
    main()
