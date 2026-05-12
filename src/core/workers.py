import re
import os
import io
import time
import gzip
import zipfile
import shutil
import tempfile
from PyQt6.QtCore import QThread, pyqtSignal
from core.entities import LogEntry

# Опциональные библиотеки для 7z и rar. Импорт делаем мягким - если пакет не
# установлен, поддержка соответствующего архива отключается, а юзер получает
# понятное сообщение в _open_log_stream.
try:
    import py7zr
    _HAS_PY7ZR = True
except ImportError:  # pragma: no cover
    py7zr = None
    _HAS_PY7ZR = False

try:
    import rarfile
    _HAS_RARFILE = True
except ImportError:  # pragma: no cover
    rarfile = None
    _HAS_RARFILE = False


# Шаблон строки лога: ВРЕМЯ [УРОВЕНЬ] [Логгер.метод]: ...
# Захватываем имя логгера (часть до первой точки во втором [])
# Между ]_и_[ может НЕ быть пробела (формат "[ERROR ][t.e.s.Foo]"), поэтому \s* а не \s+.
LINE_PATTERN = re.compile(
    r'^(\d{2}:\d{2}:\d{2}\.\d{3})\s+'
    r'\[\s*(INFO|DEBUG|ERROR|WARN)\s*\]\s*'
    r'\[\s*([^\s.\]]+)'
)


class IncrementalLogParser:
    """Парсер с состоянием: можно скармливать строки порциями.
    Используется и LogLoader (целый файл за раз), и tail-режимом (новые строки)."""

    def __init__(self):
        self.open_entry = None  # незакрытая запись (ждёт продолжений)
        self.stats = {"INFO": 0, "DEBUG": 0, "ERROR": 0, "WARN": 0}

    def feed_lines(self, lines):
        """Скармливает строки парсеру, возвращает список ЗАКРЫТЫХ записей.
        Открытая запись остаётся в self.open_entry до прихода следующего таймстампа."""
        new_closed = []
        for line in lines:
            m = LINE_PATTERN.match(line)
            if m:
                if self.open_entry is not None:
                    new_closed.append(self.open_entry)
                ts, lvl, lg = m.group(1), m.group(2), m.group(3)
                if lvl in self.stats:
                    self.stats[lvl] += 1
                self.open_entry = LogEntry(ts, lvl, lg, line.strip(), line)
            else:
                if self.open_entry is not None:
                    if len(self.open_entry.message) < 50000:
                        self.open_entry.message += "\n" + line.strip()
                    self.open_entry.full_line += line
                else:
                    # Файл начинается с продолжения - заворачиваем в UNKNOWN
                    self.open_entry = LogEntry("", "UNKNOWN", "", line.strip(), line)
        return new_closed

    def take_open(self):
        """Забирает текущую открытую запись (в конце файла)."""
        e = self.open_entry
        self.open_entry = None
        return e


def _open_log_stream(file_path):
    """Открывает .log/.txt/.gz/.zip/.7z/.rar и возвращает (text-stream, total_bytes).
    Для архивов с несколькими файлами берём первый с расширением .log/.txt;
    если таких нет — кидаем ошибку. total_bytes для архивов — это РАСПАКОВАННЫЙ
    размер (для расчёта прогресса)."""
    low = file_path.lower()
    if low.endswith('.gz'):
        # Размер несжатого: читаем последние 4 байта (ISIZE в gzip-трейлере) — это размер mod 2^32
        try:
            with open(file_path, 'rb') as f:
                f.seek(-4, os.SEEK_END)
                uncompressed = int.from_bytes(f.read(4), 'little')
            if uncompressed <= 0:
                uncompressed = os.path.getsize(file_path) * 6  # грубая оценка
        except Exception:
            uncompressed = os.path.getsize(file_path) * 6
        stream = gzip.open(file_path, 'rt', encoding='utf-8', errors='replace')
        return stream, uncompressed

    if low.endswith('.zip'):
        zf = zipfile.ZipFile(file_path, 'r')
        candidates = [n for n in zf.namelist()
                      if n.lower().endswith(('.log', '.txt'))
                      and not n.endswith('/')]
        if not candidates:
            zf.close()
            raise ValueError("В архиве нет .log или .txt файлов")
        name = candidates[0]
        info = zf.getinfo(name)
        raw = zf.open(name, 'r')
        # ZipExtFile в режиме 'r' даёт байты; обернём в TextIOWrapper для построчного чтения
        text = io.TextIOWrapper(raw, encoding='utf-8', errors='replace')
        # Закрытие zf при закрытии text не происходит автоматически - привяжем
        original_close = text.close

        def _close():
            try:
                original_close()
            finally:
                zf.close()
        text.close = _close
        return text, info.file_size

    if low.endswith('.7z'):
        if not _HAS_PY7ZR:
            raise ValueError(
                "Для открытия .7z нужна библиотека py7zr.\n"
                "Установи её: pip install py7zr"
            )
        # py7zr 1.x не даёт потокового чтения - только extract на диск. Поэтому
        # распаковываем нужный файл во временную папку и читаем как обычный текст.
        # Удаление tmp-папки навешиваем на close() потока.
        sz = py7zr.SevenZipFile(file_path, 'r')
        infos = sz.list()  # [FileInfo, ...]
        candidates = [
            fi for fi in infos
            if not fi.is_directory and fi.filename.lower().endswith(('.log', '.txt'))
        ]
        if not candidates:
            sz.close()
            raise ValueError("В архиве .7z нет .log или .txt файлов")
        target = candidates[0]
        name = target.filename

        tmp_dir = tempfile.mkdtemp(prefix='loganalyzer_7z_')
        try:
            sz.extract(path=tmp_dir, targets=[name])
        finally:
            sz.close()

        extracted_path = os.path.join(tmp_dir, name)
        if not os.path.exists(extracted_path):
            # Имя файла в архиве могло содержать поддиректории / нестандартный
            # разделитель. Подстрахуемся - ищем рекурсивно по basename.
            base = os.path.basename(name)
            for root, _dirs, files in os.walk(tmp_dir):
                if base in files:
                    extracted_path = os.path.join(root, base)
                    break

        if not os.path.exists(extracted_path):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise ValueError(f"Не удалось извлечь {name} из .7z")

        stream = open(extracted_path, 'r', encoding='utf-8', errors='replace')
        uncompressed = os.path.getsize(extracted_path)

        # На закрытии stream чистим tmp_dir (включая распакованный файл).
        original_close = stream.close

        def _close():
            try:
                original_close()
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        stream.close = _close
        return stream, uncompressed

    if low.endswith('.rar'):
        if not _HAS_RARFILE:
            raise ValueError(
                "Для открытия .rar нужна библиотека rarfile.\n"
                "Установи её: pip install rarfile"
            )
        # rarfile требует внешний unrar.exe. Если он не в PATH - даём осмысленную ошибку
        # ещё до открытия архива.
        if not _find_unrar_tool():
            raise ValueError(
                "Для открытия .rar нужен unrar.exe в PATH.\n"
                "Скачай WinRAR (https://www.win-rar.com) или unrar consumer "
                "из https://www.rarlab.com/rar_add.htm и положи unrar.exe рядом "
                "с LogAnalyzer.exe или в PATH."
            )
        rf = rarfile.RarFile(file_path)
        candidates = [
            n for n in rf.namelist()
            if n.lower().endswith(('.log', '.txt')) and not n.endswith('/')
        ]
        if not candidates:
            rf.close()
            raise ValueError("В архиве .rar нет .log или .txt файлов")
        name = candidates[0]
        info = rf.getinfo(name)
        raw = rf.open(name, 'r')
        text = io.TextIOWrapper(raw, encoding='utf-8', errors='replace')
        # rarfile сам закрывает архив при закрытии файлового объекта в большинстве
        # случаев, но подстрахуемся - привяжем закрытие RarFile к TextIOWrapper.close.
        original_close = text.close

        def _close():
            try:
                original_close()
            finally:
                try:
                    rf.close()
                except Exception:
                    pass
        text.close = _close
        return text, info.file_size

    # Обычный .log/.txt
    return open(file_path, 'r', encoding='utf-8', errors='replace'), os.path.getsize(file_path)


def _find_unrar_tool():
    """Возвращает True если на машине есть unrar/UnRAR.exe (в PATH или рядом с exe).
    rarfile сам ищет unrar в PATH, но даёт невнятную ошибку - предпочитаем
    проверить заранее и показать инструкцию по установке."""
    if shutil.which("unrar") or shutil.which("UnRAR"):
        return True
    # Рядом с приложением - удобно для bundle-сценария
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in ("unrar.exe", "UnRAR.exe"):
        if os.path.exists(os.path.join(here, candidate)):
            return True
    return False


# --- Worker Thread for Loading Files ---
class LogLoader(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(list, dict, str, int)  # entries, stats, error_msg, last_byte_position

    # Сохраняем ссылку для обратной совместимости
    LINE_PATTERN = LINE_PATTERN

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def run(self):
        parser = IncrementalLogParser()
        entries = []
        last_pos = 0  # для tail: позиция в исходном (не распакованном) файле

        try:
            stream, total_bytes = _open_log_stream(self.file_path)
            # Для tail удобнее знать позицию в исходном файле, а не в распакованном.
            # Для plain .log это совпадает. Для .gz/.zip/.7z/.rar — tail отключим в UI.
            is_plain = not self.file_path.lower().endswith(('.gz', '.zip', '.7z', '.rar'))

            bytes_read = 0
            last_emit_time = 0.0
            try:
                for line in stream:
                    bytes_read += len(line.encode('utf-8'))
                    now = time.time()
                    if total_bytes > 0 and now - last_emit_time > 0.1:
                        pct = max(0, min(100, int(bytes_read / total_bytes * 100)))
                        self.progress.emit(pct)
                        last_emit_time = now

                    closed = parser.feed_lines([line])
                    if closed:
                        entries.extend(closed)
            finally:
                stream.close()

            tail = parser.take_open()
            if tail is not None:
                entries.append(tail)

            if is_plain:
                last_pos = os.path.getsize(self.file_path)

            self.progress.emit(100)
            self.finished.emit(entries, parser.stats, "", last_pos)
        except Exception as e:
            self.finished.emit([], {}, str(e), 0)


# --- Worker Thread for Filtering ---
class FilterWorker(QThread):
    finished = pyqtSignal(list)

    def __init__(self, entries, show_info, show_debug, show_error, show_warn,
                 search_text, loggers=None, time_from=None, time_to=None,
                 case_sensitive=False, batch_filter=None, batch_for_index=None):
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
        # Партии: batch_filter=None означает "все", set означает "только batch_id из этого набора".
        # batch_for_index - параллельный entries массив str (id партии или "" для "вне партии").
        self.batch_filter = batch_filter
        self.batch_for_index = batch_for_index or []
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        active_levels = set()
        if self.show_info:
            active_levels.add("INFO")
        if self.show_debug:
            active_levels.add("DEBUG")
        if self.show_error:
            active_levels.add("ERROR")
        if self.show_warn:
            active_levels.add("WARN")

        search_text = self.search_text
        entries = self.entries
        loggers = self.loggers
        time_from = self.time_from
        time_to = self.time_to
        batch_filter = self.batch_filter
        batch_for_index = self.batch_for_index

        # Базовый фильтр: уровень, логгер, диапазон времени, партия.
        # UNKNOWN (продолжения многострочных сообщений без таймстампа) всегда пропускаем,
        # иначе теряем стек-трейсы.
        def base_pass(i, e):
            if e.level != "UNKNOWN" and e.level not in active_levels:
                return False
            if loggers is not None and e.logger and e.logger not in loggers:
                return False
            if e.timestamp:
                if time_from and e.timestamp < time_from:
                    return False
                if time_to and e.timestamp > time_to:
                    return False
            if batch_filter is not None:
                bid = batch_for_index[i] if i < len(batch_for_index) else ""
                if bid not in batch_filter:
                    return False
            return True

        if not search_text:
            new_indices = [i for i, e in enumerate(entries) if base_pass(i, e)]
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
                    if base_pass(i, e) and match(e.full_line)
                ]
            elif self.case_sensitive:
                # literal fallback, регистр учитываем
                new_indices = [
                    i for i, e in enumerate(entries)
                    if base_pass(i, e) and search_text in e.full_line
                ]
            else:
                search_lower = search_text.lower()
                new_indices = [
                    i for i, e in enumerate(entries)
                    if base_pass(i, e) and search_lower in e.full_line.lower()
                ]

        if not self._is_cancelled:
            self.finished.emit(new_indices)
