import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.entities import LogEntry
from core.log_profile import LogProfile, detect_log_profile
from core.models import LogModel, NO_BATCH


def entry(logger, message="message"):
    return LogEntry("10:00:00.000", "INFO", logger, message)


class LogProfileDetectionTests(unittest.TestCase):
    def test_detects_smart_l2_by_unique_loggers(self):
        result = detect_log_profile([
            entry("PLCService"),
            entry("SerializationService"),
            entry("DataService"),
        ])

        self.assertEqual(LogProfile.SMART_L2, result.profile)
        self.assertGreaterEqual(result.l2_score, 2)
        self.assertEqual(0, result.l3_score)

    def test_detects_smart_l3_by_unique_loggers_and_api(self):
        result = detect_log_profile([
            entry("SuzService"),
            entry("TrueApiService", "GET https://host/api/v3/true-api/orders"),
            entry("HttpLoggingFilter"),
        ])

        self.assertEqual(LogProfile.SMART_L3, result.profile)
        self.assertGreaterEqual(result.l3_score, 2)
        self.assertEqual(0, result.l2_score)

    def test_shared_loggers_are_unknown(self):
        result = detect_log_profile([
            entry("DataService"),
            entry("HttpLoggingFilter"),
            entry("WebSocketEventListener"),
        ])

        self.assertEqual(LogProfile.MIXED_UNKNOWN, result.profile)
        self.assertEqual(0, result.l2_score)
        self.assertEqual(0, result.l3_score)

    def test_signals_from_both_products_are_mixed(self):
        result = detect_log_profile([
            entry("PLCService"),
            entry("TrueApiService"),
        ])

        self.assertEqual(LogProfile.MIXED_UNKNOWN, result.profile)
        self.assertGreater(result.l2_score, 0)
        self.assertGreater(result.l3_score, 0)

    def test_model_activates_only_l2_specialized_rules(self):
        l2_model = LogModel([
            entry("PrintService", ".sendData код отправлен"),
        ])
        l2_model._batch_for_index = [NO_BATCH]
        l3_model = LogModel([
            entry("TrueApiService", ".sendData код отправлен"),
        ])
        l3_model._batch_for_index = [NO_BATCH]

        l2_analysis = l2_model.analyze_batch(NO_BATCH)
        l3_analysis = l3_model.analyze_batch(NO_BATCH)

        self.assertEqual("SmartL2", l2_analysis['profile'])
        self.assertEqual(1, l2_analysis['events']['print_data'])
        self.assertEqual("SmartL3", l3_analysis['profile'])
        self.assertEqual({}, l3_analysis['events'])


if __name__ == '__main__':
    unittest.main()
