import re
import collections
from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from config import THEMES
from core.workers import FilterWorker


# Декларативный список метрик для анализа партии.
# Каждое правило: (key, label, predicate(entry, full_line) -> bool).
# Запись засчитывается во ВСЕ совпавшие правила (одна запись может попасть в несколько метрик).
BATCH_EVENT_RULES = [
    # --- Печать ---
    ('print_request', 'Запросов на печать кода (getAndPrintAggregationCode)',
        lambda e, line: e.logger == 'AggregationBase' and '.getAndPrintAggregationCode' in line),
    ('print_sent', 'Отправлено в принтер (printAggregationCode)',
        lambda e, line: e.logger == 'AggregationBase' and '.printAggregationCode' in line),
    ('print_sato', 'Через SATO.sendCode',
        lambda e, line: e.logger == 'SATO' and '.sendCode' in line),
    ('print_data', 'PrintService.sendData',
        lambda e, line: e.logger == 'PrintService' and '.sendData' in line),
    # --- Агрегация ---
    ('agg_attempted', 'Попыток агрегации (manageAggregationCode)',
        lambda e, line: e.logger == 'LinearAggregation' and '.manageAggregationCode' in line),
    ('agg_finished', 'Завершено агрегаций (finishAggregation)',
        lambda e, line: e.logger == 'AggregationBase' and '.finishAggregation' in line),
    ('agg_finish_response', 'Ответов на завершение (manageFinishAggregationResponse)',
        lambda e, line: e.logger == 'AggregationBase' and '.manageFinishAggregationResponse' in line),
    ('agg_cleared', 'Очищено групп (clearAggGroup)',
        lambda e, line: e.logger == 'AggregationBase' and '.clearAggGroup' in line),
    # --- Сканирование / верификация ---
    ('scan_hikrobot', 'Сканирований HIKROBOT',
        lambda e, line: e.logger == 'HIKROBOT' and '.run' in line),
    ('scan_image', 'Изображений обработано (FileWatcher.processImageFile)',
        lambda e, line: e.logger == 'FileWatcher' and '.processImageFile' in line),
    # --- Проблемы / ошибки ---
    ('err_not_found', 'Кодов «не найден в базе»',
        lambda e, line: 'не найден в базе' in line),
    ('err_dup_in_groups', 'Дублей: код «уже находится в одной из агрегационных групп»',
        lambda e, line: 'уже находится в одной из агрегационных групп' in line),
    ('err_dup_in_current', 'Дублей: «уже добавлен в агрегационную группу»',
        lambda e, line: 'уже добавлен в агрегационную группу' in line),
    ('err_processor_busy', 'Агрегационный процессор занят / не найден',
        lambda e, line: 'процессор для заданного уровня не найден или занят' in line),
    ('err_jwt_expired', 'JWT-токен истёк (нужна повторная авторизация)',
        lambda e, line: 'JWT expired' in line),
    ('err_empty_image', 'Пустое изображение со сканера',
        lambda e, line: 'пустое изображение' in line.lower()),
    ('err_scanner_read', 'Ошибки чтения со сканера HIKROBOT',
        lambda e, line: 'ошибка во время чтения кодов со сканера' in line.lower()),
    # --- Сериализация / обмен ---
    ('exchange_sgtin', 'Синхронизаций с Л2 (exchangeSgtinEvents)',
        lambda e, line: e.logger == 'ExchangeService' and '.exchangeSgtinEvents' in line),
    ('serialization', 'Подтверждений печати (manageNextConfirmedPrint)',
        lambda e, line: e.logger == 'SerializationService' and '.manageNextConfirmedPrint' in line),
    # --- HTTP API ---
    ('http_request', 'HTTP-запросов (CustomLogFilter.beforeRequest)',
        lambda e, line: e.logger == 'CustomLogFilter' and '.beforeRequest' in line),
]


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

# Маркер "вне партии" - пустая строка (None плохо сериализуется в JSON и Qt-сигналах)
NO_BATCH = ""

# --- Model ---


class LogModel(QAbstractListModel):
    filterFinished = pyqtSignal()

    def __init__(self, entries=None):
        super().__init__()
        self._entries = entries or []
        # _raw_filtered_indices - все совпадения после фильтра (без группировки)
        self._raw_filtered_indices = list(range(len(self._entries)))
        # _filtered_indices - то, что реально отображается; при группировке это лидеры групп
        self._filtered_indices = list(self._raw_filtered_indices)
        # _filtered_counts - количество схлопнутых записей в каждой группе; пусто,
        # если группировка выключена
        self._filtered_counts = []
        # _member_to_row - real_index -> row, заполняется только при группировке
        # для быстрого find_row_by_real_index
        self._member_to_row = {}

        self._show_info = True
        self._show_debug = True
        self._show_error = True
        self._show_warn = True
        self._search_text = ""
        self._group_dupes = False
        self._loggers = None  # None = все, set = только из этого набора
        self._time_from = None
        self._time_to = None
        self._case_sensitive = False
        self._bookmarks = set()  # real_indices помеченных строк

        # Партии: сегменты файла + параллельный массив batch_id для каждой записи.
        # _batch_segments: list of dicts {batch_id, start, end, first_ts, last_ts}.
        # _batch_for_index: parallel list - для real_index какой batch_id (str или NO_BATCH).
        # _batch_filter: set of batch_id или None (None = все партии).
        self._batch_segments = []
        self._batch_for_index = []
        self._batch_filter = None

        self.current_theme = THEMES["Default"]
        self.font_size = 10
        self.update_colors()

        self.filter_worker = None

    def update_colors(self):
        self.color_info = QColor(self.current_theme["info"])
        self.color_debug = QColor(self.current_theme["debug"])
        self.color_error = QColor(self.current_theme["error"])
        self.color_warn = QColor(self.current_theme["warn"])
        self.color_default = QColor(self.current_theme["text_main"])
        self.font_mono = QFont(self.current_theme["mono_font"], self.font_size)

    def set_theme(self, theme_name, font_size):
        self.current_theme = THEMES[theme_name]
        self.font_size = font_size
        self.update_colors()
        self.layoutChanged.emit()

    def rowCount(self, parent=QModelIndex()):
        return len(self._filtered_indices)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._filtered_indices):
            return None

        real_index = self._filtered_indices[index.row()]
        entry = self._entries[real_index]
        is_bookmarked = real_index in self._bookmarks

        if role == Qt.ItemDataRole.DisplayRole:
            bm_prefix = "🔖 " if is_bookmarked else ""
            if self._filtered_counts:
                count = self._filtered_counts[index.row()]
                if count > 1:
                    return f"{bm_prefix}[×{count}] {entry.preview}"
            return f"{bm_prefix}{entry.preview}"
        if role == Qt.ItemDataRole.UserRole:
            if self._filtered_counts:
                count = self._filtered_counts[index.row()]
                if count > 1:
                    return f"[×{count} свёрнуто] {entry.message}"
            return entry.message
        if role == Qt.ItemDataRole.ForegroundRole:
            if entry.level == "INFO":
                return self.color_info
            if entry.level == "DEBUG":
                return self.color_debug
            if entry.level == "ERROR":
                return self.color_error
            if entry.level == "WARN":
                return self.color_warn
            return self.color_default
        if role == Qt.ItemDataRole.FontRole:
            return self.font_mono
        return None

    def set_entries(self, entries):
        self.beginResetModel()
        self._entries = entries
        self._raw_filtered_indices = list(range(len(entries)))
        self._filtered_indices = list(self._raw_filtered_indices)
        self._filtered_counts = []
        self._member_to_row = {}
        self._parse_batches(start_from=0)
        self.endResetModel()
        self.apply_filters_async()

    def append_entries(self, new_entries):
        """Добавляет записи в конец без полного reset модели. Используется в tail-режиме."""
        if not new_entries:
            return
        prev_len = len(self._entries)
        self._entries.extend(new_entries)
        # Парсим только новые - открытый сегмент продолжается с прошлого раза
        self._parse_batches(start_from=prev_len)
        # Полный фильтр - проще и корректнее, чем инкрементально вычислять что показывать.
        # FilterWorker крутится в отдельном потоке, так что UI не повиснет.
        self.apply_filters_async()

    # ----- Парсинг партий -----

    def _parse_batches(self, start_from=0):
        """Делит entries на сегменты по setCurrentBatch и /api/close.
        start_from > 0 для tail-режима: продолжаем с последнего открытого сегмента."""
        entries = self._entries

        if start_from == 0:
            # Полный re-parse
            self._batch_segments = []
            self._batch_for_index = [NO_BATCH] * len(entries)
            current_batch = NO_BATCH
            segment_start = 0
        else:
            # Догоняем хвост. Восстанавливаем состояние с последнего сегмента.
            self._batch_for_index.extend([NO_BATCH] * (len(entries) - start_from))
            if self._batch_segments:
                last_seg = self._batch_segments[-1]
                # Если последний сегмент был "открыт до конца предыдущего парсинга",
                # продолжаем его - удалим и пересоздадим с расширенным концом.
                current_batch = last_seg['batch_id']
                segment_start = last_seg['start']
                self._batch_segments.pop()
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
                if entries[k].timestamp:
                    if not first_ts:
                        first_ts = entries[k].timestamp
                    last_ts = entries[k].timestamp
            self._batch_segments.append({
                'batch_id': current_batch,
                'start': segment_start,
                'end': end_idx,
                'first_ts': first_ts,
                'last_ts': last_ts,
            })

        for i in range(start_from, len(entries)):
            line = entries[i].full_line

            # Открытие/переключение партии: эта запись начинает НОВЫЙ сегмент,
            # поэтому current_batch обновляем ДО присваивания _batch_for_index[i].
            if 'setCurrentBatch' in line:
                m = _BATCH_OPEN_RE.search(line)
                if m:
                    close_segment(i - 1)
                    new_id = m.group(1)
                    current_batch = NO_BATCH if new_id == '-1' else new_id
                    segment_start = i

            # Эта запись принадлежит ТЕКУЩЕЙ партии (для /api/close - закрывающейся,
            # для setCurrentBatch - уже новой)
            self._batch_for_index[i] = current_batch

            # Закрытие через /api/close: запись i относится к закрывшейся партии,
            # дальше переходим в "вне партии". Делаем ПОСЛЕ присваивания массива.
            if (current_batch != NO_BATCH
                    and '/api/close' in line
                    and 'CustomLogFilter' in line):
                close_segment(i)
                current_batch = NO_BATCH
                segment_start = i + 1

        # Финальный открытый сегмент - закрываем по концу файла
        if segment_start < len(entries):
            close_segment(len(entries) - 1)

    def get_batch_summary(self):
        """Возвращает [(batch_id, count, first_ts, last_ts), ...] отсортированный по first_ts.
        Несколько сегментов с одним batch_id (партию открывали-закрывали несколько раз)
        агрегируются в один пункт."""
        agg = {}
        for seg in self._batch_segments:
            bid = seg['batch_id']
            if bid not in agg:
                agg[bid] = {
                    'count': 0,
                    'first_ts': seg['first_ts'],
                    'last_ts': seg['last_ts'],
                }
            agg[bid]['count'] += seg['end'] - seg['start'] + 1
            # first_ts - минимальный по времени, last_ts - максимальный
            if seg['first_ts'] and (
                    not agg[bid]['first_ts'] or seg['first_ts'] < agg[bid]['first_ts']):
                agg[bid]['first_ts'] = seg['first_ts']
            if seg['last_ts'] and seg['last_ts'] > agg[bid]['last_ts']:
                agg[bid]['last_ts'] = seg['last_ts']
        # Сортируем по first_ts (партии в хронологическом порядке)
        return sorted(
            [(bid, info['count'], info['first_ts'], info['last_ts']) for bid, info in agg.items()],
            key=lambda x: (x[2] or "")
        )

    def get_batch_for_index(self, real_index):
        if 0 <= real_index < len(self._batch_for_index):
            return self._batch_for_index[real_index]
        return NO_BATCH

    def analyze_batch(self, batch_id):
        """Подробная аналитика по конкретной партии."""
        entries = self._entries
        bfi = self._batch_for_index
        total = 0
        levels = {'INFO': 0, 'DEBUG': 0, 'ERROR': 0, 'WARN': 0, 'UNKNOWN': 0}
        loggers = collections.Counter()
        error_categories = collections.Counter()  # нормализованные ERROR (группировка похожих)
        events = {key: 0 for key, _, _ in BATCH_EVENT_RULES}
        per_minute = collections.Counter()
        errors_per_hour = collections.Counter()  # 'HH' -> count ERROR
        scan_timestamps_ms = []
        all_timestamps_ms = []
        first_ts = ''
        last_ts = ''

        # Серийные коды (SGTIN): на единицах продукта. Сканируются HIKROBOT-ом.
        sgtin_scanned = set()           # уникальные SGTIN отсканированные
        sgtin_dup_in_groups = set()     # SGTIN с ошибкой "уже находится в одной из групп"
        sgtin_dup_in_current = set()    # SGTIN с ошибкой "уже добавлен в группу"
        sgtin_not_found = set()         # SGTIN "не найден в базе"

        # Групповые коды (агрегационные): на коробах. Печатаются на принтере.
        group_codes_received = set()    # коды агрегата которые сгенерировались для групп
        group_codes_printed = set()     # коды отправленные через PrintService.sendData

        # Подсчёт всех "событий печати" с кодами (даже повторных) - для объяснения расхождений
        sgtin_scan_events = 0           # Кол-во ВСЕХ срабатываний HIKROBOT с кодом
        sgtin_unique_per_scan_event = collections.Counter()  # сколько раз каждый код встретился

        def ts_to_ms(ts):
            try:
                h, m, s_ms = ts.split(':')
                s, ms = s_ms.split('.')
                return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms))
            except (ValueError, IndexError):
                return None

        for i in range(len(bfi)):
            if bfi[i] != batch_id:
                continue
            e = entries[i]
            total += 1
            if e.timestamp:
                if not first_ts:
                    first_ts = e.timestamp
                last_ts = e.timestamp
                ms = ts_to_ms(e.timestamp)
                if ms is not None:
                    all_timestamps_ms.append(ms)
                per_minute[e.timestamp[:5]] += 1
            levels[e.level] = levels.get(e.level, 0) + 1
            if e.logger:
                loggers[e.logger] += 1

            line = e.full_line

            # ERROR: извлекаем чистое сообщение и НОРМАЛИЗУЕМ для группировки
            if e.level == 'ERROR':
                first_line = e.message.split('\n', 1)[0]
                m = re.search(r'\]:\s*(.*)', first_line)
                clean = m.group(1).strip() if m else first_line
                normalized = _normalize_error_message(clean)
                error_categories[normalized[:160]] += 1
                if e.timestamp:
                    errors_per_hour[e.timestamp[:2]] += 1

            # SGTIN-коды (серийные на единицах продукта): извлекаем из всех строк
            sgtin_in_line = SGTIN_CODE_RE.findall(line)
            if sgtin_in_line:
                # HIKROBOT - реальное сканирование сканером
                if e.logger == 'HIKROBOT' and '.run' in line and 'Получены данные' in line:
                    sgtin_scan_events += 1
                    for code in sgtin_in_line:
                        sgtin_scanned.add(code)
                        sgtin_unique_per_scan_event[code] += 1
                # Дубли в других группах
                if 'уже находится в одной из агрегационных групп' in line:
                    sgtin_dup_in_groups.update(sgtin_in_line)
                # Дубли в текущей группе
                if 'уже добавлен в агрегационную группу' in line:
                    sgtin_dup_in_current.update(sgtin_in_line)
                # Не найден в базе
                if 'не найден в базе' in line:
                    sgtin_not_found.update(sgtin_in_line)

            # Групповые (агрегационные) коды упаковок
            group_in_line = GROUP_CODE_RE.findall(line)
            if group_in_line:
                if e.logger == 'AggregationBase' and (
                        '.getAndPrintAggregationCode' in line
                        and 'Получен код агрегата' in line):
                    group_codes_received.update(group_in_line)
                if e.logger == 'PrintService' and '.sendData' in line:
                    group_codes_printed.update(group_in_line)

            for key, _label, predicate in BATCH_EVENT_RULES:
                try:
                    if predicate(e, line):
                        events[key] += 1
                        if key == 'scan_hikrobot' and e.timestamp:
                            ms = ts_to_ms(e.timestamp)
                            if ms is not None:
                                scan_timestamps_ms.append(ms)
                except Exception:
                    pass

        # Длительность
        duration_ms = 0
        if first_ts and last_ts:
            first_ms = ts_to_ms(first_ts)
            last_ms = ts_to_ms(last_ts)
            if first_ms is not None and last_ms is not None:
                duration_ms = max(0, last_ms - first_ms)

        per_hour = 0
        if duration_ms > 0:
            per_hour = round(total / (duration_ms / 3_600_000), 1)

        avg_scan_delta_ms = None
        if len(scan_timestamps_ms) >= 2:
            scan_timestamps_ms.sort()
            deltas = [scan_timestamps_ms[i] - scan_timestamps_ms[i - 1]
                      for i in range(1, len(scan_timestamps_ms))]
            avg_scan_delta_ms = sum(deltas) / len(deltas)

        PAUSE_THRESHOLD_MS = 5 * 60 * 1000
        pauses = []
        if len(all_timestamps_ms) >= 2:
            all_timestamps_ms_sorted = sorted(all_timestamps_ms)
            for j in range(1, len(all_timestamps_ms_sorted)):
                diff = all_timestamps_ms_sorted[j] - all_timestamps_ms_sorted[j - 1]
                if diff > PAUSE_THRESHOLD_MS:
                    pauses.append(
                        (all_timestamps_ms_sorted[j - 1], all_timestamps_ms_sorted[j], diff)
                    )
        # Топ-5 самых длинных пауз
        pauses.sort(key=lambda x: x[2], reverse=True)
        pauses = pauses[:5]

        # Эффективность агрегации: % от попыток что завершились успешно
        agg_efficiency = None
        if events['agg_attempted'] > 0:
            agg_efficiency = round(events['agg_finished'] / events['agg_attempted'] * 100, 1)

        # Сколько SGTIN-кодов сканировались более 1 раза (потенциальные дубли)
        repeated_scans = sum(1 for c, n in sgtin_unique_per_scan_event.items() if n > 1)
        # Топ-3 кода которые сканировались чаще всего (часто-сканируемые)
        most_repeated = sorted(sgtin_unique_per_scan_event.items(),
                               key=lambda x: x[1], reverse=True)[:3]
        most_repeated = [(c, n) for c, n in most_repeated if n > 1]

        return {
            'batch_id': batch_id,
            'total': total,
            'first_ts': first_ts,
            'last_ts': last_ts,
            'duration_ms': duration_ms,
            'per_hour': per_hour,
            'levels': levels,
            'events': events,
            'event_labels': {key: label for key, label, _ in BATCH_EVENT_RULES},
            'top_error_categories': error_categories.most_common(10),
            'top_loggers': loggers.most_common(5),
            'top_minutes': per_minute.most_common(5),
            'avg_scan_delta_ms': avg_scan_delta_ms,
            'pauses': pauses,
            'agg_efficiency_pct': agg_efficiency,
            'errors_per_hour': sorted(errors_per_hour.items()),
            # Серийные коды (продукт)
            'sgtin_scanned_unique': len(sgtin_scanned),
            'sgtin_scan_events': sgtin_scan_events,
            'sgtin_repeated_scans': repeated_scans,  # сколько SGTIN сканировалось >1 раза
            'sgtin_most_repeated': most_repeated,    # топ-3 чаще всего сканированные
            'sgtin_dup_in_groups': len(sgtin_dup_in_groups),
            'sgtin_dup_in_current': len(sgtin_dup_in_current),
            'sgtin_not_found': len(sgtin_not_found),
            # Групповые коды (упаковки)
            'group_codes_received': len(group_codes_received),  # сгенерированных
            'group_codes_printed': len(group_codes_printed),    # отправленных в принтер
            'group_codes_lost': len(group_codes_received - group_codes_printed),
        }

    def update_filters(self, show_info, show_debug, show_error, show_warn,
                       search_text, group_dupes=False, loggers=None,
                       time_from=None, time_to=None, case_sensitive=False,
                       batch_filter=None):
        self._show_info = show_info
        self._show_debug = show_debug
        self._show_error = show_error
        self._show_warn = show_warn
        self._search_text = search_text
        self._group_dupes = group_dupes
        self._loggers = loggers
        self._time_from = time_from
        self._time_to = time_to
        self._case_sensitive = case_sensitive
        self._batch_filter = batch_filter
        self.apply_filters_async()

    def apply_filters_async(self):
        if self.filter_worker and self.filter_worker.isRunning():
            self.filter_worker.cancel()
            self.filter_worker.wait()

        self.filter_worker = FilterWorker(
            self._entries,
            self._show_info, self._show_debug, self._show_error, self._show_warn,
            self._search_text,
            self._loggers,
            self._time_from,
            self._time_to,
            self._case_sensitive,
            self._batch_filter,
            self._batch_for_index,
        )
        self.filter_worker.finished.connect(self.on_filter_finished)
        self.filter_worker.start()

    def on_filter_finished(self, new_indices):
        self.beginResetModel()
        self._raw_filtered_indices = new_indices
        if self._group_dupes:
            (self._filtered_indices,
             self._filtered_counts,
             self._member_to_row) = self._build_groups(new_indices)
        else:
            self._filtered_indices = new_indices
            self._filtered_counts = []
            self._member_to_row = {}
        self.endResetModel()
        self.filterFinished.emit()

    def _build_groups(self, indices):
        """Схлопывает подряд идущие записи с одинаковым (level, message) в группы."""
        grouped = []
        counts = []
        member_to_row = {}
        prev_key = None
        entries = self._entries
        for idx in indices:
            e = entries[idx]
            key = (e.level, e.message)
            if grouped and key == prev_key:
                counts[-1] += 1
            else:
                grouped.append(idx)
                counts.append(1)
                prev_key = key
            member_to_row[idx] = len(grouped) - 1
        return grouped, counts, member_to_row

    def get_real_index(self, row):
        if 0 <= row < len(self._filtered_indices):
            return self._filtered_indices[row]
        return None

    def find_row_by_real_index(self, real_index):
        if self._member_to_row:
            return self._member_to_row.get(real_index, -1)
        try:
            return self._filtered_indices.index(real_index)
        except ValueError:
            return -1

    # ----- Закладки -----

    def toggle_bookmark(self, real_index):
        """Переключает закладку для записи. Возвращает True если закладка ВКЛЮЧЕНА после операции."""
        if real_index in self._bookmarks:
            self._bookmarks.discard(real_index)
            now_on = False
        else:
            self._bookmarks.add(real_index)
            now_on = True
        # Перерисовываем строку если она видна
        row = self.find_row_by_real_index(real_index)
        if row != -1:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)
        return now_on

    def set_bookmarks(self, real_indices):
        """Устанавливает набор закладок целиком (используется при восстановлении сессии)."""
        self._bookmarks = set(real_indices)
        # Полная перерисовка
        if self.rowCount() > 0:
            self.dataChanged.emit(self.index(0), self.index(self.rowCount() - 1))

    def get_bookmarks_sorted(self):
        return sorted(self._bookmarks)
