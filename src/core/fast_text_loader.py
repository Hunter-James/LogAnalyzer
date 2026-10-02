"""Первый просмотр журнала до полного анализа.

Большие файлы хранятся в блоках UTF-8 с компактным индексом строк.
Один ранний снимок позволяет читать начало до завершения распаковки;
finished передаёт весь файл и метки. Очереди множества порций в GUI нет.
После finished LogViewerWidget запускает LogLoader для полного анализа.
"""

import os
import time
from PyQt6.QtCore import QThread, pyqtSignal

from core.workers import _open_log_stream
from core.loading_priority import loading_priority
from core.raw_log_buffer import RawLogBuffer


class FastTextLoader(QThread):
    """Сигналы:
      progress(int percent) — прогресс по байтам исходного файла.
      previewReady(tuple lines) — один снимок первых строк большого файла.
      finished(Sequence lines, list[(int line_no, str level)] markers,
               int total_lines, str error_msg) —
        список сырых строк + все маркеры разом. error_msg='__CANCELLED__'
        если прерван через requestInterruption, '' при успехе.

        Возвращаем последовательность строк, которую RawLinesModel
        использует без копирования. Для full/limited-движков
        UI сам сделает '\\n'.join(lines) при необходимости."""

    progress = pyqtSignal(int)
    previewReady = pyqtSignal(object)
    finished = pyqtSignal(object, object, int, str)

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def run(self):
        with loading_priority.preview():
            self._read_preview()

    def _read_preview(self):
        """Читает исходный текст, пока полные анализаторы уступают ему процессор."""
        try:
            stream, total_bytes = _open_log_stream(self.file_path)
        except Exception as e:
            self.finished.emit([], [], 0, str(e))
            return

        buf = []
        markers = []  # [(line_no_1based, 'ERROR'|'WARN'), ...]
        line_no = 0
        bytes_read = 0
        last_progress = 0.0
        try:
            if total_bytes >= 8 * 1024 * 1024 and hasattr(stream, 'buffer'):
                def progress(consumed):
                    nonlocal last_progress
                    now = time.monotonic()
                    if now - last_progress > .1:
                        self.progress.emit(min(99, int(consumed / total_bytes * 100)))
                        last_progress = now
                lines = RawLogBuffer.read(
                    stream.buffer, self.isInterruptionRequested, progress, self.previewReady.emit)
                markers = lines.markers(self.isInterruptionRequested)
                self.progress.emit(100)
                self.finished.emit(lines, markers, len(lines), '')
                return
            for line in stream:
                # Проверяем прерывание не на каждой строке — раз в ~10000
                # для скорости. interrupt проверяется ещё и на каждом
                # progress emit ниже.
                if line_no % 10000 == 0 and self.isInterruptionRequested():
                    self.finished.emit([], [], line_no, '__CANCELLED__')
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
            # Один emit со списком строк + маркерами. UI сам выберет как
            # показать (setModel для list-движка / setPlainText для full).
            self.finished.emit(buf, markers, line_no, '')
        except InterruptedError:
            self.finished.emit([], [], 0, '__CANCELLED__')
        except Exception as e:
            self.finished.emit([], [], line_no, str(e))
        finally:
            try:
                stream.close()
            except Exception:
                pass
