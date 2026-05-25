"""Стрим-агрегатор статистики по партиям для всей группы файлов.

Идея: одна партия (batch_id) может встречаться в нескольких лог-файлах за
разные дни. Этот worker проходит по списку путей, открывает каждый файл,
парсит построчно и аккумулирует счётчики/коды/файлы в один dict —
БЕЗ хранения entries в RAM.

Используется в gui/group_stats_dialog.py."""

import os
import re
import sys
from PyQt6.QtCore import QThread, pyqtSignal

from core.workers import _open_log_stream, LINE_PATTERN
from core.models import _BATCH_OPEN_RE, NO_BATCH, SGTIN_CODE_RE, GROUP_CODE_RE

# SSCC (палеты/короба) — 18 цифр после префикса 00. Совпадает с регексом
# из log_viewer._SSCC_RE (продублирован чтобы не тянуть GUI-зависимости в
# core).
_SSCC_RE = re.compile(r'\b00\d{18}\b')


def _classify_for_stats(line, logger):
    """Та же логика что в LogViewerWidget._classify_for_stats, но без
    LogEntry — принимает голые line и logger. Возвращает ключ счётчика или
    None.

    Дублирование оправдано: классификация — единый источник истины, но
    LogViewer работает с LogEntry-объектами после полного парсинга, а
    worker — со строками на лету."""
    lower = line.lower()

    # 1) Скан HIKROBOT - один физический скан = одна строка HIKROBOT.run.
    if logger == 'HIKROBOT' and '.run' in line:
        if 'noread' in lower or 'не прочитан' in lower:
            return 'noread'
        if 'получены данные' in lower:
            return 'scanned'
        return None

    # 2) Статус-переходы кода - DataService.
    if 'изменён на Printed' in line or 'изменен на Printed' in line:
        return 'printed'
    if ('изменён на PrintConfirmed' in line
            or 'изменен на PrintConfirmed' in line
            or 'изменён на Verified' in line):
        return 'verified'
    if 'изменён на Rejected' in line or 'изменен на Rejected' in line:
        return 'rejected'

    # 3) Команда отбраковки от PLC.
    if (logger == 'PLCService' and '.rejectCode' in line
            and '[отбраковать]: true' in line):
        return 'rejected'

    if 'не верифицирован' in lower:
        return 'not_verified'
    return None


_COUNTER_KEYS = ('printed', 'scanned', 'noread',
                 'verified', 'rejected', 'not_verified')


def _new_batch_bucket():
    """Пустой контейнер для одной партии."""
    return {
        'counters': {k: 0 for k in _COUNTER_KEYS},
        # path → сколько релевантных строк партии в этом файле
        'files': {},
        # path → {counter_key: N} — нужно для «Сравнить с одним файлом»,
        # чтобы не парсить файл повторно.
        'per_file_counters': {},
        'first_ts': '',
        'first_file': '',
        'last_ts': '',
        'last_file': '',
        # set уникальных SGTIN/SSCC/групповых кодов, встреченных в партии
        'codes': set(),
    }


class GroupStatsWorker(QThread):
    """Стримит файлы группы по очереди, аккумулирует stats по партиям.

    Сигналы:
      progress(current_idx, total, filename) — текущий обрабатываемый файл
      finished(batches_dict, error_msg) — результат; error_msg='__CANCELLED__'
                                          если юзер прервал."""

    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict, str)

    def __init__(self, file_paths):
        super().__init__()
        self.file_paths = list(file_paths)

    def run(self):
        batches = {}
        intern = sys.intern
        try:
            for i, path in enumerate(self.file_paths):
                if self.isInterruptionRequested():
                    self.finished.emit(batches, '__CANCELLED__')
                    return
                self.progress.emit(i, len(self.file_paths),
                                   os.path.basename(path))
                self._stream_one(path, batches, intern)
            self.finished.emit(batches, '')
        except Exception as e:
            self.finished.emit(batches, str(e))

    def _stream_one(self, path, batches, intern):
        """Парсит один файл построчно, обновляет batches in-place."""
        try:
            stream, _ = _open_log_stream(path)
        except Exception:
            # Не падаем на одном плохом файле - просто пропускаем.
            return

        current_batch = NO_BATCH
        last_ts_in_line = ''  # timestamp последней timestamped-строки

        sgtin_find = SGTIN_CODE_RE.findall
        sscc_find = _SSCC_RE.findall
        group_find = GROUP_CODE_RE.findall

        try:
            for line in stream:
                # Проверяем прерывание не на каждой строке — раз в ~1000
                # для скорости.
                # (упростил: проверяем через счётчик в outer цикле проще
                # сделать в run, но здесь хватает.)

                # 1) Парсинг служебной шапки: timestamp + level + logger
                m = LINE_PATTERN.match(line)
                if m:
                    last_ts_in_line = m.group(1)
                    logger = intern(m.group(3))
                else:
                    # continuation line - logger/ts из предыдущей "сильной"
                    # строки. Для классификации это не критично: continuation
                    # обычно не содержит ключевых маркеров.
                    logger = ''

                # 2) Сегментация партий (та же логика что в LogModel._parse_batches)
                if 'setCurrentBatch' in line:
                    mb = _BATCH_OPEN_RE.search(line)
                    if mb:
                        new_id = mb.group(1)
                        current_batch = NO_BATCH if new_id == '-1' else intern(new_id)

                # 3) Классификация по счётчикам
                key = _classify_for_stats(line, logger) if logger else None
                if key:
                    bucket = batches.get(current_batch)
                    if bucket is None:
                        bucket = _new_batch_bucket()
                        batches[current_batch] = bucket
                    bucket['counters'][key] += 1
                    bucket['files'][path] = bucket['files'].get(path, 0) + 1
                    # Per-file counters для быстрого «Сравнить с одним файлом»
                    pfc = bucket['per_file_counters'].get(path)
                    if pfc is None:
                        pfc = {k: 0 for k in _COUNTER_KEYS}
                        bucket['per_file_counters'][path] = pfc
                    pfc[key] += 1
                    if last_ts_in_line:
                        if not bucket['first_ts']:
                            bucket['first_ts'] = last_ts_in_line
                            bucket['first_file'] = path
                        bucket['last_ts'] = last_ts_in_line
                        bucket['last_file'] = path

                # 4) Сбор уникальных кодов
                codes_in_line = sgtin_find(line)
                codes_in_line += sscc_find(line)
                codes_in_line += group_find(line)
                if codes_in_line:
                    bucket = batches.get(current_batch)
                    if bucket is None:
                        bucket = _new_batch_bucket()
                        batches[current_batch] = bucket
                    # intern для экономии RAM — те же коды часто встречаются
                    bucket['codes'].update(intern(c) for c in codes_in_line)

                # 5) Закрытие через /api/close
                if (current_batch != NO_BATCH
                        and '/api/close' in line
                        and 'CustomLogFilter' in line):
                    current_batch = NO_BATCH
        finally:
            try:
                stream.close()
            except Exception:
                pass
