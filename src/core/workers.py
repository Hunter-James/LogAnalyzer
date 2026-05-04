import re
import os
import time
from PyQt6.QtCore import QThread, pyqtSignal
from core.entities import LogEntry

# --- Worker Thread for Loading Files ---
class LogLoader(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(list, dict, str)

    # Шаблон строки лога: ВРЕМЯ [УРОВЕНЬ] [Логгер.метод]: ...
    # Захватываем имя логгера (часть до первой точки во втором [])
    LINE_PATTERN = re.compile(
        r'^(\d{2}:\d{2}:\d{2}\.\d{3})\s+'
        r'\[\s*(INFO|DEBUG|ERROR|WARN)\s*\]\s+'
        r'\[\s*([^\s.\]]+)'
    )

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def run(self):
        entries = []
        stats = {"INFO": 0, "DEBUG": 0, "ERROR": 0, "WARN": 0}
        log_pattern = self.LINE_PATTERN

        try:
            file_size = os.path.getsize(self.file_path)
            bytes_read = 0
            last_emit_time = 0

            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                current_entry = None
                for line in f:
                    line_len = len(line.encode('utf-8'))
                    bytes_read += line_len

                    current_time = time.time()
                    if current_time - last_emit_time > 0.1:
                        progress_pct = int((bytes_read / file_size) * 100)
                        self.progress.emit(progress_pct)
                        last_emit_time = current_time

                    match = log_pattern.match(line)
                    if match:
                        if current_entry:
                            entries.append(current_entry)
                        timestamp_str = match.group(1)
                        level_str = match.group(2)
                        logger_str = match.group(3)
                        if level_str in stats:
                            stats[level_str] += 1
                        current_entry = LogEntry(timestamp_str, level_str, logger_str, line.strip(), line)
                    else:
                        if current_entry:
                            if len(current_entry.message) < 50000:
                                current_entry.message += "\n" + line.strip()
                            current_entry.full_line += line
                        else:
                            current_entry = LogEntry("", "UNKNOWN", "", line.strip(), line)

                if current_entry:
                    entries.append(current_entry)

            self.progress.emit(100)
            self.finished.emit(entries, stats, "")
        except Exception as e:
            self.finished.emit([], {}, str(e))


# --- Worker Thread for Filtering ---
class FilterWorker(QThread):
    finished = pyqtSignal(list)

    def __init__(self, entries, show_info, show_debug, show_error, show_warn,
                 search_text, loggers=None, time_from=None, time_to=None,
                 case_sensitive=False):
        super().__init__()
        self.entries = entries
        self.show_info = show_info
        self.show_debug = show_debug
        self.show_error = show_error
        self.show_warn = show_warn
        self.search_text = search_text
        # loggers=None означает "все" (без ограничения), set означает "только из этого набора"
        self.loggers = loggers
        # Границы времени в формате HH:MM:SS.mmm (лексикографически = хронологически)
        self.time_from = time_from
        self.time_to = time_to
        # Match case как в Notepad++: по умолчанию off (re.IGNORECASE), при on - регистрозависимо
        self.case_sensitive = case_sensitive
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        active_levels = set()
        if self.show_info: active_levels.add("INFO")
        if self.show_debug: active_levels.add("DEBUG")
        if self.show_error: active_levels.add("ERROR")
        if self.show_warn: active_levels.add("WARN")

        search_text = self.search_text
        entries = self.entries
        loggers = self.loggers
        time_from = self.time_from
        time_to = self.time_to

        # Базовый фильтр: уровень, логгер, диапазон времени.
        # UNKNOWN (продолжения многострочных сообщений без таймстампа) всегда пропускаем,
        # иначе теряем стек-трейсы.
        def base_pass(e):
            if e.level != "UNKNOWN" and e.level not in active_levels:
                return False
            if loggers is not None and e.logger and e.logger not in loggers:
                return False
            if e.timestamp:
                if time_from and e.timestamp < time_from:
                    return False
                if time_to and e.timestamp > time_to:
                    return False
            return True

        if not search_text:
            new_indices = [i for i, e in enumerate(entries) if base_pass(e)]
        else:
            regex_flags = 0 if self.case_sensitive else re.IGNORECASE
            search_regex = None
            try:
                search_regex = re.compile(search_text, regex_flags)
            except re.error:
                search_regex = None

            if search_regex:
                match = search_regex.search
                new_indices = [
                    i for i, e in enumerate(entries)
                    if base_pass(e) and match(e.full_line)
                ]
            elif self.case_sensitive:
                # literal fallback, регистр учитываем
                new_indices = [
                    i for i, e in enumerate(entries)
                    if base_pass(e) and search_text in e.full_line
                ]
            else:
                search_lower = search_text.lower()
                new_indices = [
                    i for i, e in enumerate(entries)
                    if base_pass(e) and search_lower in e.full_line.lower()
                ]

        if not self._is_cancelled:
            self.finished.emit(new_indices)
