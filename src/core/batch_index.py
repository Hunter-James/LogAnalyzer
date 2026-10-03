"""Разметка партий и общие шаблоны кодов без зависимости от Qt."""
import re


# SGTIN (серийный код единицы продукта): 01 + GTIN(14) + serial.
# В DataMatrix-стандарте поля разделяются control-символами (GS = \x1d, FS=\x1c, RS=\x1e, US=\x1f),
# которые Python считает "\s" (whitespace) - поэтому НЕ используем \s, а явно перечисляем
# пробельные char'ы которые надо исключить. Также исключаем скобки - после кода в HIKROBOT
# идут координаты типа (2398,1325).
_SCAN_CODE_CHAR = r"[^ \t\n\r\f\v()\[\]{}]"
SGTIN_CODE_RE = re.compile(r"\b01\d{14}" + _SCAN_CODE_CHAR + "{6,40}")

# Агрегационный (групповой) код упаковки: 18 цифр начинающихся с 04.
# Это "груповой код" - наклейка на короб/упаковку. По одному коду на короб.
GROUP_CODE_RE = re.compile(r'\b04\d{16}\b')

# Нормализация ERROR-сообщений для группировки одинаковых по смыслу:
# заменяем коды на <CODE>, длинные числа на <N>, GUID на <UUID>.
# Здесь регекс мягче чем для извлечения SGTIN: разрешаем () и [], потому что
# в ERROR-сообщениях иногда такие символы попадаются в самом коде. Координат
# (как в HIKROBOT.run) тут не бывает, так что лимит длины 30 chars держит безопасно.
_NORMALIZE_CODE = re.compile(r"\b01\d{14}[^ \t\n\r\f\v]{6,30}")
_NORMALIZE_GUID = re.compile(
    r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I)
_NORMALIZE_GROUP = re.compile(r'L2_[0-9a-f-]+_LEVEL_\d+_GROUP_ID_\d+', re.I)
_NORMALIZE_LONG_NUM = re.compile(r'\b\d{6,}\b')


def _normalize_error_message(msg):
    """Заменяет переменные части (коды, числа, UUID) на плейсхолдеры,
    чтобы похожие ошибки группировались."""
    s = _NORMALIZE_CODE.sub('<CODE>', msg)
    s = _NORMALIZE_GROUP.sub('<GROUP>', s)
    s = _NORMALIZE_GUID.sub('<UUID>', s)
    s = _NORMALIZE_LONG_NUM.sub('<N>', s)
    return s.strip()


# Партии: события, по которым мы режем лог.
# Открытие/переключение партии: setCurrentBatch?batchId=N (N может быть -1 -> вне партии)
# Закрытие партии: /api/close (от CustomLogFilter, чтобы не зацепить случайные совпадения)
_BATCH_OPEN_RE = re.compile(r'setCurrentBatch\?batchId=(-?\d+)')
_CURRENT_BATCH_RESPONSE_RE = re.compile(
    r'Response\s+(/api/[^\s{]+).*?"currentBatch"\s*:\s*\{.*?"id"\s*:\s*(-?\d+)'
)
_BATCH_OPEN_SOURCE_SET_CURRENT = 'set_current'
_SKIP_CLOSE_AFTER_CURRENT_BATCH_RESPONSES = frozenset((
    '/api/clearNotFatalError',
))
_BACKFILL_BEFORE_CURRENT_BATCH_RESPONSES = frozenset((
    '/api/clickStartStop',
))
_BATCH_RESPONSE_BACKFILL_MAX_LINES = 80


def _extract_batch_open_marker(line):
    """Return (batch id, source) from markers that start/switch the current batch."""
    if 'setCurrentBatch' in line:
        m = _BATCH_OPEN_RE.search(line)
        if m:
            return m.group(1), _BATCH_OPEN_SOURCE_SET_CURRENT

    if 'Response /api/' in line and '"currentBatch"' in line:
        m = _CURRENT_BATCH_RESPONSE_RE.search(line)
        if m:
            endpoint, batch_id = m.groups()
            return batch_id, endpoint

    return None, None


def _should_skip_close_after_open_source(open_source):
    return open_source in _SKIP_CLOSE_AFTER_CURRENT_BATCH_RESPONSES


def _should_backfill_before_open_source(open_source):
    return open_source in _BACKFILL_BEFORE_CURRENT_BATCH_RESPONSES


def _line_has_batch_code(line):
    return bool(SGTIN_CODE_RE.search(line) or GROUP_CODE_RE.search(line))


def _find_batch_response_backfill_start(entries, segment_start, marker_idx):
    scan_start = max(segment_start, marker_idx - _BATCH_RESPONSE_BACKFILL_MAX_LINES)
    first_code_idx = None
    for idx in range(marker_idx - 1, scan_start - 1, -1):
        line = entries[idx].message
        if _extract_batch_open_marker(line)[0] is not None:
            break
        if '/api/close' in line and 'CustomLogFilter' in line:
            break
        if _line_has_batch_code(line):
            first_code_idx = idx
    return marker_idx if first_code_idx is None else first_code_idx


# Маркер "вне партии" - пустая строка (None плохо сериализуется в JSON и Qt-сигналах)
NO_BATCH = ""


def parse_batch_index(entries, start_from=0, segments=None, batch_for_index=None, cancelled=None):
    """Делит entries на сегменты по setCurrentBatch и /api/close.
    start_from > 0 для tail-режима: продолжаем с последнего открытого сегмента."""

    if start_from == 0:
        # Полный re-parse
        segments = []
        batch_for_index = [NO_BATCH] * len(entries)
        current_batch = NO_BATCH
        segment_start = 0
        skip_next_close_after_recovered_batch = False
    else:
        # Догоняем хвост. Восстанавливаем состояние с последнего сегмента.
        batch_for_index.extend([NO_BATCH] * (len(entries) - start_from))
        skip_next_close_after_recovered_batch = False
        if segments:
            last_seg = segments[-1]
            # Если последний сегмент был "открыт до конца предыдущего парсинга",
            # продолжаем его - удалим и пересоздадим с расширенным концом.
            current_batch = last_seg['batch_id']
            segment_start = last_seg['start']
            segments.pop()
        else:
            current_batch = NO_BATCH
            segment_start = 0

    def close_segment(end_idx):
        """Закрывает текущий сегмент [segment_start..end_idx] включительно."""
        if end_idx < segment_start:
            return
        first_ts = ""
        last_ts = ""
        for k in range(segment_start, end_idx + 1):
            if cancelled and k % 2048 == 0 and cancelled():
                raise InterruptedError
            if entries[k].timestamp:
                first_ts = entries[k].timestamp
                break
        for k in range(end_idx, segment_start - 1, -1):
            if cancelled and k % 2048 == 0 and cancelled():
                raise InterruptedError
            if entries[k].timestamp:
                last_ts = entries[k].timestamp
                break
        segments.append({
            'batch_id': current_batch,
            'start': segment_start,
            'end': end_idx,
            'first_ts': first_ts,
            'last_ts': last_ts,
        })

    for i in range(start_from, len(entries)):
        if cancelled and i % 2048 == 0 and cancelled():
            raise InterruptedError
        # Маркеры не требуют завершающего newline из full_line; не копируем
        # весь текст каждой записи во время прохода по большому журналу.
        line = entries[i].message

        # Открытие/переключение партии: эта запись начинает НОВЫЙ сегмент,
        # поэтому current_batch обновляем ДО присваивания _batch_for_index[i].
        new_id, open_source = _extract_batch_open_marker(line)
        if new_id is not None:
            new_batch = NO_BATCH if new_id == '-1' else new_id
            backfill_start = i
            if (current_batch == NO_BATCH
                    and new_batch != NO_BATCH
                    and _should_backfill_before_open_source(open_source)):
                backfill_start = _find_batch_response_backfill_start(entries, segment_start, i)
            close_segment(backfill_start - 1)
            current_batch = new_batch
            segment_start = backfill_start
            for backfill_idx in range(backfill_start, i):
                batch_for_index[backfill_idx] = current_batch
            skip_next_close_after_recovered_batch = (
                current_batch != NO_BATCH
                and _should_skip_close_after_open_source(open_source)
            )

        # Эта запись принадлежит ТЕКУЩЕЙ партии (для /api/close - закрывающейся,
        # для setCurrentBatch - уже новой)
        batch_for_index[i] = current_batch

        # Закрытие через /api/close: запись i относится к закрывшейся партии,
        # дальше переходим в "вне партии". Делаем ПОСЛЕ присваивания массива.
        if (current_batch != NO_BATCH
                and '/api/close' in line
                and 'CustomLogFilter' in line):
            if skip_next_close_after_recovered_batch:
                skip_next_close_after_recovered_batch = False
            else:
                close_segment(i)
                current_batch = NO_BATCH
                segment_start = i + 1

    # Финальный открытый сегмент - закрываем по концу файла
    if segment_start < len(entries):
        close_segment(len(entries) - 1)

    return segments, batch_for_index
