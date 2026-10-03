"""Временная настройка GC не теряется при ошибках и пересекающихся загрузках."""
import gc
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from core.loading_memory import LoadingMemory


class LoadingMemoryTests(unittest.TestCase):
    def setUp(self):
        self.saved = gc.get_threshold()
        self.enabled = gc.isenabled()

    def tearDown(self):
        gc.set_threshold(*self.saved)
        (gc.enable if self.enabled else gc.disable)()

    def test_overlapping_loads_restore_only_after_last_exit(self):
        gc.enable()
        memory = LoadingMemory()
        with patch('core.loading_memory.gc.collect') as collect:
            first = memory.batch()
            second = memory.batch()
            first.__enter__()
            second.__enter__()
            temporary = gc.get_threshold()
            self.assertTrue(gc.isenabled())
            self.assertEqual(temporary[:2], self.saved[:2])
            self.assertGreaterEqual(temporary[2], 1_000_000)
            first.__exit__(None, None, None)
            self.assertEqual(gc.get_threshold(), temporary)
            collect.assert_not_called()
            second.__exit__(None, None, None)
            self.assertEqual(gc.get_threshold(), self.saved)
            collect.assert_not_called()

    def test_error_and_cancellation_restore_thresholds(self):
        gc.enable()
        for error in (OSError, InterruptedError):
            with self.subTest(error=error), patch('core.loading_memory.gc.collect'):
                with self.assertRaises(error), LoadingMemory().batch():
                    raise error()
                self.assertEqual(gc.get_threshold(), self.saved)

    def test_disabled_gc_stays_disabled(self):
        gc.disable()
        with patch('core.loading_memory.gc.collect') as collect:
            with LoadingMemory().batch():
                self.assertFalse(gc.isenabled())
                self.assertEqual(gc.get_threshold(), self.saved)
            collect.assert_not_called()
        self.assertFalse(gc.isenabled())

    def test_external_threshold_change_is_preserved(self):
        gc.enable()
        changed = (1234, 20, 30)
        with patch('core.loading_memory.gc.collect'):
            with LoadingMemory().batch():
                gc.set_threshold(*changed)
        self.assertEqual(gc.get_threshold(), changed)


if __name__ == '__main__':
    unittest.main()
