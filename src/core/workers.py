import re
import os
import time
from PyQt6.QtCore import QThread, pyqtSignal
from core.entities import LogEntry

# --- Worker Thread for Loading Files ---
class LogLoader(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(list, dict, str)

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def run(self):
        entries = []
        stats = {"INFO": 0, "DEBUG": 0, "ERROR": 0, "WARN": 0}
        log_pattern = re.compile(r'^(\d{2}:\d{2}:\d{2}\.\d{3})\s+\[\s*(INFO|DEBUG|ERROR|WARN)\s*\]')

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
                        if level_str in stats:
                            stats[level_str] += 1
                        current_entry = LogEntry(timestamp_str, level_str, line.strip(), line)
                    else:
                        if current_entry:
                            if len(current_entry.message) < 50000:
                                current_entry.message += "\n" + line.strip()
                            current_entry.full_line += line
                        else:
                            current_entry = LogEntry("", "UNKNOWN", line.strip(), line)

                if current_entry:
                    entries.append(current_entry)

            self.progress.emit(100)
            self.finished.emit(entries, stats, "")
        except Exception as e:
            self.finished.emit([], {}, str(e))


# --- Worker Thread for Filtering ---
class FilterWorker(QThread):
    finished = pyqtSignal(list)

    def __init__(self, entries, show_info, show_debug, show_error, show_warn, search_text):
        super().__init__()
        self.entries = entries
        self.show_info = show_info
        self.show_debug = show_debug
        self.show_error = show_error
        self.show_warn = show_warn
        self.search_text = search_text
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

        if not search_text:
            new_indices = [
                i for i, e in enumerate(entries)
                if e.level in active_levels or e.level == "UNKNOWN"
            ]
        else:
            search_regex = None
            try:
                search_regex = re.compile(search_text, re.IGNORECASE)
            except re.error:
                search_regex = None

            if search_regex:
                match = search_regex.search
                new_indices = [
                    i for i, e in enumerate(entries)
                    if (e.level in active_levels or e.level == "UNKNOWN") and match(e.full_line)
                ]
            else:
                search_lower = search_text.lower()
                new_indices = [
                    i for i, e in enumerate(entries)
                    if (e.level in active_levels or e.level == "UNKNOWN") and search_lower in e.full_line.lower()
                ]

        if not self._is_cancelled:
            self.finished.emit(new_indices)
