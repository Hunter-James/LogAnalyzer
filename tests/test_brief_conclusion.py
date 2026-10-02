"""Регрессии заключения: неполные журналы, причинность и независимые отчёты."""
import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication
from core.brief_conclusion import BriefConclusion, MISSING
from core.entities import LogEntry
from core.log_profile import LogProfile
from core.models import LogModel
from gui.brief_conclusion_dialog import BriefConclusionDialog, ConclusionWorker, conclusion_html
from gui.batch_analysis_dialog import BatchAnalysisDialog


def entry(message, logger='DataService', level='INFO', timestamp='13:42:00.000'):
    return LogEntry(timestamp, level, logger, message)


def summarize(entries, profile=LogProfile.SMART_L2, batch=None):
    accumulator = BriefConclusion(profile, batch)
    for index, item in enumerate(entries):
        accumulator.observe(item, index)
    return accumulator.build()


def prose(result):
    return '\n'.join(item['text'] for item in result)


class BriefConclusionTests(unittest.TestCase):
    def test_empty_and_incomplete_logs_use_qualified_absence(self):
        self.assertIn(MISSING, prose(summarize([])))
        result = prose(summarize([entry('Статус кода example изменён на PrintConfirmed.')]))
        self.assertIn('верификации — 1', result)
        self.assertIn('Подтверждение синхронизации с L3 ' + MISSING, result)
        self.assertNotIn('печать завершена', result)

    def test_unique_codes_and_no_false_print_completion(self):
        rows = [entry('Статус кода example изменён на PrintConfirmed.')] * 2
        rows += [entry('.sendData example', 'PrintService')]
        result = prose(summarize(rows))
        self.assertIn('верификации — 1', result)
        self.assertIn('Printed — 0', result)

    def test_sync_count_is_not_code_count_and_is_batch_scoped(self):
        rows = [entry('Синхронизировано 5 из 8 событий , batchId=12, t=1 ms', 'SynchronizationService')]
        self.assertNotIn('5 из 8', prose(summarize(rows, batch='13')))
        self.assertIn('5 из 8 событий', prose(summarize(rows, batch='12')))

    def test_sync_recovery_order(self):
        error = entry('Синхронизация не выполнена.', 'SynchronizationService', 'ERROR')
        success = entry('Синхронизировано 5 из 5 событий , batchId=12, t=1 ms', 'SynchronizationService')
        self.assertIn('После ошибки есть запись', prose(summarize([error, success])))
        self.assertIn('после этой ошибки ' + MISSING, prose(summarize([success, error])))

    def test_error_state_cause_and_recovery_are_distinct(self):
        rows = [entry('Changing state from BATCH_SELECTED to ERROR'),
                entry('Response /api/state {"state":"ERROR","errorMessage":"Нет кодов"}', 'CustomLogFilter'),
                entry('Changing state from ERROR to BATCH_SELECTED')]
        result = prose(summarize(rows))
        self.assertIn('Сообщение причины: Нет кодов', result)
        self.assertIn('выход из ERROR', result)
        self.assertIn('не подтверждает запуск', result)

    def test_nearby_error_is_only_a_candidate(self):
        rows = [entry('Ошибка камеры', 'ScanService', 'ERROR'),
                entry('Changing state from BATCH_SELECTED to ERROR')]
        result = prose(summarize(rows))
        self.assertIn('Возможная связь', result)
        self.assertIn('Причинная связь не подтверждена', result)
        self.assertIn('выхода линии из ERROR ' + MISSING, result)

    def test_current_l2_numeric_error_state_in_http_filter(self):
        rows = [entry('Response /api/state {"state":-1,"errorMessage":"Ошибка принтера",'
                      '"notFatalErrorMessage":"Другая ошибка"}', 'HttpLoggingFilter')]
        result = prose(summarize(rows))
        self.assertIn('Сообщение причины: Ошибка принтера', result)
        self.assertNotIn('Другая ошибка', result)

    def test_new_error_snapshot_does_not_reuse_old_cause(self):
        rows = [entry('Response /api/state {"state":-1,"errorMessage":"Старая причина"}', 'HttpLoggingFilter'),
                entry('Changing state from ERROR to BATCH_SELECTED'),
                entry('Response /api/state {"state":-1,"errorMessage":null}', 'HttpLoggingFilter')]
        result = prose(summarize(rows))
        self.assertNotIn('Старая причина', result)
        self.assertIn('Явное сообщение причины ' + MISSING, result)

    def test_remote_error_is_not_cause_and_new_failure_resets_recovery(self):
        rows = [entry('Ошибка камеры', 'ScanService', 'ERROR')]
        rows += [entry('idle')] * 25
        rows += [entry('Changing state from BATCH_SELECTED to ERROR'),
                 entry('Changing state from ERROR to BATCH_SELECTED'),
                 entry('Changing state from BATCH_SELECTED to ERROR')]
        result = prose(summarize(rows))
        self.assertNotIn('Возможная связь', result)
        self.assertIn('выхода линии из ERROR ' + MISSING, result)

    def test_connection_errors_are_counted_once_per_multiline_record(self):
        rows = [entry('Не отправлен запрос\nCaused by: java.net.ConnectException: Connection refused',
                      'ExchangeService', 'ERROR') for _ in range(36)]
        rows += [entry('connection refused mentioned in documentation')]
        result = summarize(rows)
        self.assertIn('Ошибок соединения: 36', prose(result))
        self.assertIn('java.net.ConnectException', prose(result))
        self.assertEqual(35, result[-1]['evidence'][0][0])

    def test_report_statuses_are_tracked_per_uuid_and_latest_wins(self):
        rows = [entry('Получен report uuid= a batchId= 12 batch status= SENT', 'ReportService'),
                entry('Получен report uuid= b batchId= 13 batch status= PROCESSED_FAIL', 'ReportService'),
                entry('Получен report uuid= a batchId= 12 batch status= PROCESSED_OK', 'ReportService')]
        text = prose(summarize(rows, LogProfile.SMART_L3))
        self.assertIn('PROCESSED_OK: 1 из 2', text)
        self.assertIn('Отчёт b, партия 13: обработан с ошибками', text)
        self.assertIn('итогового успешного статуса ' + MISSING, text)
        scoped = prose(summarize(rows, LogProfile.SMART_L3, '12'))
        self.assertNotIn('партия 13', scoped)

    def test_http_success_and_partial_status_do_not_prove_report_success(self):
        rows = [entry('HTTP 200 PROCESSED_OK', 'HttpLoggingFilter'),
                entry('Получен report uuid= a batchId= 12 batch status= PROCESSED_PARTIAL', 'ReportService')]
        result = prose(summarize(rows, LogProfile.SMART_L3))
        self.assertIn('PROCESSED_OK: 0 из 1', result)
        self.assertIn('обработан частично', result)

    def test_report_blocker_is_detected_even_at_info_level(self):
        rows = [entry('Блокируют отчет агрегации sgtins: 2', 'CodesReportService')]
        self.assertIn('Последняя проблема', prose(summarize(rows, LogProfile.SMART_L3)))

    def test_mixed_profile_does_not_infer_l2_state(self):
        rows = [entry('Changing state from BATCH_SELECTED to ERROR')]
        result = prose(summarize(rows, LogProfile.MIXED_UNKNOWN))
        self.assertNotIn('Зафиксирован переход', result)

    def test_html_escapes_log_content_and_links_to_record(self):
        result = summarize([entry('<script>alert(1)</script>', level='ERROR')])
        rendered = conclusion_html(result)
        self.assertNotIn('<script>', rendered)
        self.assertIn('entry:0', rendered)


class ConclusionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_batch_analysis_includes_conclusion_and_evidence(self):
        rows = [entry('PLC failure', 'PLCService', 'ERROR')]
        model = LogModel(rows)
        analysis = model.analyze_batch('')
        dialog = BatchAnalysisDialog(analysis, entries=rows)
        self.assertIn('Краткое заключение', dialog.browser.toPlainText())
        dialog.browser.anchorClicked.emit(QUrl('entry:0'))
        self.assertEqual('PLC failure', dialog.evidence.toPlainText())
        dialog.close()

    def test_file_dialog_worker_and_copy(self):
        rows = [entry('test failure', 'PLCService', 'ERROR')]
        dialog = BriefConclusionDialog(rows, LogProfile.SMART_L2)
        self.assertTrue(dialog.worker.wait(5000))
        self.app.processEvents()
        self.assertIn('test failure', dialog.browser.toPlainText())
        dialog.show_evidence(QUrl('entry:0'))
        self.assertEqual('test failure', dialog.evidence.toPlainText())
        dialog.copy_button.click()
        self.assertIn('test failure', self.app.clipboard().text())
        dialog.close()

    def test_worker_cancellation_does_not_publish(self):
        worker = ConclusionWorker([entry('idle')] * 100000, LogProfile.SMART_L2)
        results = []
        worker.ready.connect(results.append)
        worker.start()
        worker.requestInterruption()
        self.assertTrue(worker.wait(5000))
        self.app.processEvents()
        self.assertEqual([], results)


if __name__ == '__main__':
    unittest.main()
