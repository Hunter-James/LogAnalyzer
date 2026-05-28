"""Быстрый loader первого этапа Two-stage open режима.

Читает файл chunks, эмитит сырой текст для добавления в QPlainTextEdit
+ позиции ERROR/WARN строк для маркеров на скроллбаре. Никакого
LogEntry, никакой регекс-классификации — просто substring-check.

После того как FastTextLoader закончил, MainWindow запускает обычный
LogLoader для полного парсинга в фоне. Когда тот готов — UI заменяет
text-view на list-view с моделью.

Скорость: ~ S2 из research_perf.py (read + примитивная классификация).
Для 6.5 МБ zip ~ 0.8s вместо 1.6s полного парсинга."""

import os
import time
from PyQt6.QtCore import QThread, pyqtSignal

from core.workers import _open_log_stream

# Размер chunk'а в строках. Меньше = чаще обновляется UI (плавнее), но
# больше overhead на emit. 5000 ~ компромисс: 60 emit для 300K строк.
_CHUNK_LINES = 5000


class FastTextLoader(QThread):
    """Сигналы:
      chunkReady(str text, list[(int line_no, str level)] markers) —
        очередной кусок текста + маркеры ERROR/WARN найденные в нём.
      progress(int percent) — прогресс по байтам исходного файла.
      finished(int total_lines, str error_msg) — '__CANCELLED__' если
        прерван через requestInterruption."""

    chunkReady = pyqtSignal(str, list)
    progress = pyqtSignal(int)
    finished = pyqtSignal(int, str)

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def run(self):
        try:
            stream, total_bytes = _open_log_stream(self.file_path)
        except Exception as e:
            self.finished.emit(0, str(e))
            return

        buf = []
        markers = []  # [(line_no_1based, 'ERROR'|'WARN'), ...]
        line_no = 0
        bytes_read = 0
        last_progress = 0.0
        # Substring-check без regex — быстрее. Покрывает оба варианта
        # написания уровня: «[ERROR ]» и «[ ERROR]».
        try:
            for line in stream:
                if self.isInterruptionRequested():
                    self.finished.emit(line_no, '__CANCELLED__')
                    return
                line_no += 1
                bytes_read += len(line.encode('utf-8'))
                buf.append(line)
                # Маркеры — ищем по позиции «[ERROR» в типовом начале
                # строки. False positive минимален: уровень логирования
                # всегда после timestamp в первых 25 символах.
                head = line[:30] if len(line) > 30 else line
                if 'ERROR' in head and '[' in head:
                    markers.append((line_no, 'ERROR'))
                elif 'WARN' in head and '[' in head:
                    markers.append((line_no, 'WARN'))

                if len(buf) >= _CHUNK_LINES:
                    self.chunkReady.emit(''.join(buf), markers)
                    buf = []
                    markers = []
                    now = time.time()
                    if total_bytes > 0 and now - last_progress > 0.1:
                        pct = max(0, min(100,
                                         int(bytes_read / total_bytes * 100)))
                        self.progress.emit(pct)
                        last_progress = now

            # Финальный chunk
            if buf or markers:
                self.chunkReady.emit(''.join(buf), markers)
            self.progress.emit(100)
            self.finished.emit(line_no, '')
        except Exception as e:
            self.finished.emit(line_no, str(e))
        finally:
            try:
                stream.close()
            except Exception:
                pass
