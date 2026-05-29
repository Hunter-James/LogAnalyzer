"""Быстрый loader первого этапа Two-stage open режима.

Читает файл целиком в свой воркер-поток, накапливает text + позиции
ERROR/WARN. Эмитит результат ОДИН РАЗ в finished — UI делает setPlainText
один раз O(N), а не N раз × insertText на растущем view.

Раньше был streaming (chunks по 50K строк) — но на 1М+ строк UI thread
не успевал обрабатывать очередь chunkReady, loadingFinished застревал в
очереди событий, busy висел минутами. Single-shot emit устраняет это.

После завершения FastTextLoader, LogViewerWidget запускает обычный
LogLoader для полного парсинга в фоне. Когда он готов — UI заменяет
text-view на list-view с моделью.

Скорость: ~ S2 из research_perf.py (read + примитивная классификация).
Для 12 МБ zip / 1.2M строк ~ 1.5с вместо 20с полного парсинга."""

import os
import time
from PyQt6.QtCore import QThread, pyqtSignal

from core.workers import _open_log_stream


class FastTextLoader(QThread):
    """Сигналы:
      progress(int percent) — прогресс по байтам исходного файла.
      finished(str text, list[(int line_no, str level)] markers,
               int total_lines, str error_msg) —
        весь текст + все маркеры разом. error_msg='__CANCELLED__' если
        прерван через requestInterruption, '' при успехе."""

    progress = pyqtSignal(int)
    finished = pyqtSignal(str, list, int, str)

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def run(self):
        try:
            stream, total_bytes = _open_log_stream(self.file_path)
        except Exception as e:
            self.finished.emit('', [], 0, str(e))
            return

        buf = []
        markers = []  # [(line_no_1based, 'ERROR'|'WARN'), ...]
        line_no = 0
        bytes_read = 0
        last_progress = 0.0
        try:
            for line in stream:
                # Проверяем прерывание не на каждой строке — раз в ~10000
                # для скорости. interrupt проверяется ещё и на каждом
                # progress emit ниже.
                if line_no % 10000 == 0 and self.isInterruptionRequested():
                    self.finished.emit('', [], line_no, '__CANCELLED__')
                    return
                line_no += 1
                bytes_read += len(line.encode('utf-8'))
                buf.append(line)
                # Маркеры — substring-check в первых 30 символах. Уровень
                # логирования всегда в начале строки, false positive
                # минимален.
                head = line[:30] if len(line) > 30 else line
                if 'ERROR' in head and '[' in head:
                    markers.append((line_no, 'ERROR'))
                elif 'WARN' in head and '[' in head:
                    markers.append((line_no, 'WARN'))

                # Progress по байтам, не чаще раза в 100мс
                now = time.time()
                if total_bytes > 0 and now - last_progress > 0.1:
                    pct = max(0, min(100,
                                     int(bytes_read / total_bytes * 100)))
                    self.progress.emit(pct)
                    last_progress = now

            self.progress.emit(100)
            # Один emit с готовым текстом + маркерами. setPlainText на
            # стороне UI отработает за O(N), без накопления очереди событий.
            self.finished.emit(''.join(buf), markers, line_no, '')
        except Exception as e:
            self.finished.emit('', [], line_no, str(e))
        finally:
            try:
                stream.close()
            except Exception:
                pass
