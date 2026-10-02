"""Заключение по наблюдаемым событиям SmartL2/L3, без догадок об отсутствующих логах."""

import json
import re
from collections import Counter

from core.log_profile import LogProfile


MISSING = 'не найдено в загруженных журналах'
_STATUS = re.compile(r'Статус кода (\S+) измен[её]н на (Printed|PrintConfirmed|Verified)\b')
_SYNC = re.compile(r'Синхронизировано (\d+) из (\d+) событий.*?batchId=(\d+)')
_STATE = re.compile(r'Changing state from (\w+) to (\w+)')
_REPORT = re.compile(
    r'Получен report uuid=\s*(\S+) batchId=\s*(\d+) batch status=\s*(\w+)')
_SAVED_REPORT = re.compile(r'Сохранение отчета uuid=\s*(\S+) status=\s*(\w+)')
_CONNECTION = re.compile(
    r'ConnectException|ConnectTimeoutException|ReadTimeoutException|UnknownHostException|'
    r'Connection refused|Connection reset|connection timed out|connection prematurely closed|'
    r'Нет соединения|Не удалось подключиться', re.I)


def _message(entry):
    """Краткая цитата с причиной Java exception, если она присутствует."""
    causes = re.findall(r'Caused by:\s*([^\n]+)', entry.message)
    line = causes[-1] if causes else entry.message.split('\n', 1)[0]
    return re.sub(r'^.*?\]:\s*', '', line).strip()[:400]


class BriefConclusion:
    """Однопроходный сбор фактов; ссылки хранят индексы логических записей."""

    def __init__(self, profile, batch_id):
        self.profile = profile
        self.batch_id = batch_id
        self.facts = {}
        self.printed = set()
        self.verified = set()
        self.scans = 0
        self.connections = 0
        self.first_connection = None
        self.last_connection = None
        self.last_error = None
        self.line_error = None
        self.line_cause = None
        self.line_candidate = None
        self.line_recovery = None
        self.sync = None
        self.sync_error = None
        self.report_problem = None
        self.reports = {}

    def observe(self, entry, index):
        """Принимает запись выбранной области в исходном порядке файла."""
        if entry.level not in ('ERROR', 'FATAL') and entry.logger not in (
                'DataService', 'CustomLogFilter', 'HttpLoggingFilter', 'HIKROBOT', 'SynchronizationService',
                'ReportService', 'ReportRepoService', 'CodesReportService'):
            return
        text = entry.message
        ref = (index, entry.timestamp, _message(entry))
        previous_error = self.last_error
        if entry.level in ('ERROR', 'FATAL'):
            self.last_error = ref
            if _CONNECTION.search(text):
                self.connections += 1
                self.first_connection = self.first_connection or ref
                self.last_connection = ref

        if self.profile == LogProfile.SMART_L2:
            if entry.logger == 'DataService':
                match = _STATUS.search(text)
                if match:
                    code, status = match.groups()
                    target = self.printed if status == 'Printed' else self.verified
                    target.add(code)
                    self.facts['print' if status == 'Printed' else 'verify'] = ref
                state = _STATE.search(text)
                if state:
                    if state[2] == 'ERROR':
                        self.line_error = ref
                        self.line_cause = None
                        self.line_recovery = None
                        self.line_candidate = (
                            previous_error if previous_error and index - previous_error[0] <= 20 else None)
                    elif state[1] == 'ERROR' and self.line_error:
                        self.line_recovery = ref
            # StateObject.errorMessage относится именно к состоянию ERROR,
            # в отличие от независимого notFatalErrorMessage.
            if entry.logger in ('CustomLogFilter', 'HttpLoggingFilter') and 'Response /api/' in text and '{' in text:
                try:
                    state, _ = json.JSONDecoder().raw_decode(text[text.index('{'):])
                except (ValueError, TypeError):
                    state = None
                if isinstance(state, dict) and state.get('state') in (-1, 'ERROR'):
                    if not self.line_error or self.line_recovery:
                        self.line_error = ref
                        self.line_cause = None
                        self.line_recovery = None
                        self.line_candidate = None
                    if state.get('errorMessage'):
                        self.line_cause = (index, entry.timestamp, str(state['errorMessage'])[:400])
            if entry.logger == 'HIKROBOT' and '.run' in text and 'Получены данные' in text:
                self.scans += 1
                self.facts['scan'] = ref
            if entry.logger == 'SynchronizationService':
                if entry.level == 'ERROR':
                    self.sync_error = ref
                match = _SYNC.search(text)
                if match and (self.batch_id is None or match[3] == self.batch_id):
                    self.sync = (int(match[1]), int(match[2]), ref)

        if entry.logger in ('ReportService', 'ReportRepoService', 'CodesReportService',
                            'SuzService', 'TrueApiService'):
            if ((entry.level == 'ERROR' and re.search(r'отч[её]т|report', text, re.I))
                    or 'Блокируют отчет агрегации' in text or 'КИ блокирует отчет агрегации' in text):
                self.report_problem = ref

        # L3 обрабатывает партии параллельно: статус связывается по UUID,
        # а не по соседству записей или последней выбранной партии.
        if entry.logger in ('ReportService', 'ReportRepoService'):
            match = _REPORT.search(text)
            if match and (self.batch_id is None or match[2] == self.batch_id):
                uuid, batch, status = match.groups()
                self.reports[uuid] = (status, ref, batch)
            elif self.batch_id is None:
                match = _SAVED_REPORT.search(text)
                if match:
                    uuid, status = match.groups()
                    batch = self.reports.get(uuid, ('', None, ''))[2]
                    self.reports[uuid] = (status, ref, batch)

    def build(self):
        """Возвращает короткие выводы и доказательства для UI и копирования."""
        result = []

        def add(text, *refs):
            result.append({'text': text, 'evidence': [r for r in refs if r is not None]})

        if self.profile == LogProfile.SMART_L2:
            if self.printed or self.scans or self.verified:
                add(f'На L2: кодов со статусом Printed — {len(self.printed)}; '
                    f'чтений сканера — {self.scans}; уникальных кодов с подтверждением '
                    f'верификации — {len(self.verified)}. '
                    f'Подтверждение завершения печати и сканирования всей партии {MISSING}.',
                    *self.facts.values())
            if self.sync:
                count, total, ref = self.sync
                add(f'Последняя запись синхронизации L2 → L3: {count} из {total} событий. '
                    'Это счётчик событий, а не подтверждение передачи всех верифицированных кодов.', ref)
            elif self.verified:
                add(f'Подтверждение синхронизации с L3 {MISSING}.')
            if self.sync_error:
                add('Последняя ошибка синхронизации: ' + self.sync_error[2], self.sync_error)
                if self.sync and self.sync[2][0] > self.sync_error[0]:
                    add('После ошибки есть запись об успешной синхронизации части или всех '
                        'событий указанного обмена; это не подтверждает пустую очередь.', self.sync[2])
                else:
                    add(f'Подтверждение успешной синхронизации после этой ошибки {MISSING}.')
            if self.line_error:
                if self.line_cause:
                    add('Зафиксирован переход линии в ERROR. Сообщение причины: '
                        + self.line_cause[2], self.line_error, self.line_cause)
                else:
                    add(f'Зафиксирован переход линии в ERROR. Явное сообщение причины {MISSING}.',
                        self.line_error)
                    if self.line_candidate:
                        add('Возможная связь: перед остановкой записана ошибка: '
                            + self.line_candidate[2] + '. Причинная связь не подтверждена.',
                            self.line_candidate)
                if self.line_recovery:
                    add('После сбоя зафиксирован выход из ERROR. Это не подтверждает запуск линии.',
                        self.line_recovery)
                else:
                    add(f'Подтверждение выхода линии из ERROR {MISSING}.')

        if self.connections:
            add(f'Ошибок соединения: {self.connections}; интервал записей '
                f'{self.first_connection[1] or "время неизвестно"} → '
                f'{self.last_connection[1] or "время неизвестно"}. '
                'Непрерывность сбоя по этим записям не установлена.',
                self.first_connection, self.last_connection)
        if self.last_error:
            add('Последняя записанная ошибка: ' + self.last_error[2], self.last_error)
        else:
            add(f'Сообщение уровня ERROR/FATAL {MISSING}.')

        if self.report_problem:
            add('Последняя проблема формирования, отправки или проверки отчёта: '
                + self.report_problem[2]
                + '. Состояние конкретного отчёта проверяется отдельно по UUID.', self.report_problem)

        if self.reports:
            counts = Counter(status for status, _, _ in self.reports.values())
            add(f'Отчётов с последним наблюдаемым статусом PROCESSED_OK: {counts["PROCESSED_OK"]} '
                f'из {len(self.reports)}. Успех одного отчёта не подтверждает завершение всей партии.')
            for uuid, (status, ref, batch) in sorted(
                    self.reports.items(), key=lambda item: item[1][1][0], reverse=True)[:3]:
                labels = {'PROCESSED_OK': 'успешно обработан', 'PROCESSED_FAIL': 'обработан с ошибками',
                          'PROCESSED_PARTIAL': 'обработан частично', 'SENT': 'обрабатывается',
                          'WAIT': 'ожидает отправки', 'REPEAT': 'повторная отправка',
                          'PROCESSED_FIX': 'исправление результата'}
                detail = f'Отчёт {uuid}' + (f', партия {batch}' if batch else '')
                detail += ': ' + labels.get(status, status) + '.'
                if status != 'PROCESSED_OK':
                    detail += f' Подтверждение итогового успешного статуса {MISSING}.'
                add(detail, ref)
        elif self.profile == LogProfile.SMART_L3:
            add(f'Подтверждение итогового успешного статуса отчётов {MISSING}.')
        if self.profile == LogProfile.MIXED_UNKNOWN:
            add('Профиль смешанный или неизвестный: вывод о состоянии линии L2 не формируется.')
        return result
