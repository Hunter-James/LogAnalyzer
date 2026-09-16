"""Регрессии фильтрации: python -m unittest discover -s tests."""
import os
import sys
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PyQt6.QtWidgets import QApplication
from core.entities import LogEntry
from core.models import LogModel
from core.workers import FilterWorker

APP = QApplication.instance() or QApplication([])


def entry(message, level='INFO', logger='A', timestamp='10:00:00.000'):
    return LogEntry(timestamp, level, logger, message)


def run_filter(entries, query='', **options):
    worker = FilterWorker(entries, True, True, True, True, query, **options)
    result = []
    worker.finished.connect(result.append)
    worker.run()
    return result[0], worker


def pump_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(0.001)
    if not predicate():
        raise AssertionError('Filter did not finish')


class SearchTests(unittest.TestCase):
    def test_literal_case_unicode_and_multiline(self):
        entries = [entry('ПрИвЕт.*\nstack'), entry('привет'), entry('HELLO')]
        self.assertEqual(run_filter(entries, 'ПРИВЕТ')[0], [0, 1])
        self.assertEqual(run_filter(entries, 'привет', case_sensitive=True)[0], [1])
        self.assertEqual(run_filter(entries, '.*')[0], [0])
        self.assertEqual(run_filter(entries, 'stack\n')[0], [0])
        self.assertEqual(run_filter(entries, 'нет')[0], [])
        self.assertEqual(run_filter(entries)[0], [0, 1, 2])

    def test_regex_and_invalid_regex_fallback(self):
        entries = [entry('HELLO'), entry('a['), entry('end\nnext')]
        self.assertEqual(run_filter(entries, '^hello$', use_regex=True)[0], [0])
        self.assertEqual(run_filter(entries, '^hello$', use_regex=True, case_sensitive=True)[0], [])
        self.assertEqual(run_filter(entries, '[', use_regex=True)[0], [1])
        self.assertEqual(run_filter(entries, 'next\\n$', use_regex=True)[0], [2])

    def test_combined_metadata_filters(self):
        entries = [entry('one'), entry('two', logger='B'), entry('three', timestamp='11:00:00.000'),
                   entry('stack', level='UNKNOWN', logger='', timestamp='')]
        indices, _ = run_filter(entries, loggers={'A'}, time_from='10:00:00.000',
                                time_to='10:00:00.000', batch_filter={'b'},
                                batch_for_index=['b', 'b', 'x', 'b'])
        self.assertEqual(indices, [0, 3])
        worker = FilterWorker(entries, False, False, False, False, '')
        result = []
        worker.finished.connect(result.append)
        worker.run()
        self.assertEqual(result, [[3]])

    def test_grouping_navigation_and_marker_severity(self):
        entries = [entry('10 [A]: same', 'WARN'), entry('11 [A]: same', 'WARN'),
                   entry('12 [A]: other', 'ERROR'), entry('13 [A]: same', 'WARN')]
        indices, worker = run_filter(entries, group_dupes=True)
        self.assertEqual(indices, [0, 1, 2, 3])
        self.assertEqual(worker.visible_indices, [0, 2, 3])
        self.assertEqual(worker.counts, [2, 1, 1])
        self.assertEqual(worker.member_to_row, {0: 0, 1: 0, 2: 1, 3: 2})
        self.assertEqual(worker.marker_levels, [(0, 'WARN'), (66 / 200, 'ERROR'), (133 / 200, 'WARN')])
        entries = [entry('x', 'WARN'), entry('y', 'ERROR')] * 200
        _, worker = run_filter(entries)
        self.assertEqual(worker.marker_levels, [(i / 200, 'ERROR') for i in range(200)])

    def test_cancel_stops_reading_and_emits_nothing(self):
        class CancellingEntries:
            reads = 0

            def __len__(self):
                return 100000

            def __getitem__(self, index):
                self.reads += 1
                worker.cancel()
                return entry('x')
        entries = CancellingEntries()
        worker = FilterWorker(entries, True, True, True, True, 'x')
        results = []
        worker.finished.connect(results.append)
        worker.run()
        self.assertEqual(entries.reads, 1)
        self.assertEqual(results, [])

    def test_new_queries_do_not_wait_and_only_latest_is_applied(self):
        entered, release = threading.Event(), threading.Event()

        class SlowEntry:
            timestamp, level, logger = '', 'INFO', ''

            @property
            def message(self):
                entered.set()
                release.wait(3)
                return 'latest'
        model = LogModel([SlowEntry()])
        results = []
        model.filterFinished.connect(lambda: results.append(list(model._filtered_indices)))
        model.update_filters(True, True, True, True, 'first')
        self.assertTrue(entered.wait(2))
        try:
            started = time.monotonic()
            model.update_filters(True, True, True, True, 'middle')
            model.update_filters(True, True, True, True, 'latest')
            self.assertLess(time.monotonic() - started, 0.1)
        finally:
            release.set()
        pump_until(lambda: bool(results))
        model.stop_filtering()
        APP.processEvents()
        self.assertEqual(results, [[0]])
        self.assertEqual(model.filter_worker.search_text, 'latest')

    def test_queued_old_result_cannot_replace_new_data(self):
        model = LogModel([entry('old')])
        results = []
        model.filterFinished.connect(lambda: results.append(list(model._filtered_indices)))
        model.update_filters(True, True, True, True, 'old')
        model.filter_worker.wait()
        model.set_entries([])
        pump_until(lambda: bool(results))
        model.stop_filtering()
        self.assertEqual(results, [[]])
        self.assertEqual(model.rowCount(), 0)

    def test_stop_cancels_pending_restart(self):
        model = LogModel([entry('x')] * 10000)
        model.update_filters(True, True, True, True, 'x')
        model.update_filters(True, True, True, True, 'y')
        worker = model.filter_worker
        model.stop_filtering()
        APP.processEvents()
        self.assertIs(model.filter_worker, worker)
        self.assertFalse(worker.isRunning())
        self.assertFalse(model._filter_pending)


if __name__ == '__main__':
    unittest.main()
