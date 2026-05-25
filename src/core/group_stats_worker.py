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
from core.models import _BATCH_OPEN_RE, NO_BATCH

# Объединённый regex для всех трёх типов кодов: SGTIN (01 + 14 цифр GTIN +
# 6..40 любых non-whitespace/non-bracket), SSCC (00 + 18 цифр), групповой
# (04 + 16 цифр). Один проход вместо трёх — профайл 19MB файла показал что
# раздельные SGTIN/SSCC/GROUP findall давали 77 секунд на пустые SSCC/GROUP,
# которые в этом файле вообще ни разу не совпали. Общий префикс \b0(?:1|0|4)
# даёт regex-VM быстрый prefix-reject для большинства позиций.
_SCAN_CODE_CHAR = r"[^ \t\n\r\f\v()\[\]{}]"
ALL_CODES_RE = re.compile(
    r'\b0(?:'
    r'1\d{14}' + _SCAN_CODE_CHAR + r'{6,40}'  # SGTIN: 01 + 14 + 6..40
    r'|0\d{18}\b'                              # SSCC: 00 + 18 цифр
    r'|4\d{16}\b'                              # групповой: 04 + 16 цифр
    r')'
)


def _classify_for_stats(line, logger):
    """Та же логика что в LogViewerWidget._classify_for_stats, но без
    LogEntry — принимает голые line и logger. Возвращает ключ счётчика или
    None.

    Дублирование оправдано: классификация — единый источник истины, но
    LogViewer работает с LogEntry-объектами после полного парсинга, а
    worker — со строками на лету.

    ПРОИЗВОДИТЕЛЬНОСТЬ: line.lower() создаёт копию строки целиком и
    раньше делался всегда. Теперь lazy — вычисляется только если нужно
    (внутри HIKROBOT-ветки и финальной 'не верифицирован' проверки).
    На больших логах с миллионами строк экономит секунды."""
    # 1) Скан HIKROBOT - один физический скан = одна строка HIKROBOT.run.
    if logger == 'HIKROBOT' and '.run' in line:
        lower = line.lower()
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

    # 4) Финальная проверка - в самом конце, чтобы lower() вызывался только
    # когда не сработали все верхние быстрые проверки.
    if 'не верифицирован' in line.lower():
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
        total = len(self.file_paths)
        try:
            for i, path in enumerate(self.file_paths):
                if self.isInterruptionRequested():
                    self.finished.emit(batches, '__CANCELLED__')
                    return
                # Сначала эмитим «обрабатываю файл i+1» — UI видит имя
                # текущего файла + прогресс i из total.
                self.progress.emit(i, total, os.path.basename(path))
                self._stream_one(path, batches, intern)
            # После обработки последнего — эмитим финальный 100%, чтобы
            # прогресс-бар точно дошёл до конца ДО finished (иначе юзер
            # видел 99.7% и приложение «висит» пока шёл последний файл).
            self.progress.emit(total, total, '')
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

        codes_find = ALL_CODES_RE.findall

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

                # 4) Сбор уникальных кодов (один объединённый regex —
                # см. ALL_CODES_RE наверху файла).
                codes_in_line = codes_find(line)
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
