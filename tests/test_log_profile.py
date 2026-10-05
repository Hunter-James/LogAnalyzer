import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.entities import LogEntry
from core.log_profile import (
    LogProfile, detect_log_profile, update_log_profile, _L2_TEXT_SIGNALS, _L3_TEXT_SIGNALS,
)
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

    def test_all_text_signals_preserve_case_insensitive_matching(self):
        cases = [
            ("Команда на деактивацию отбраковщика от L2", "L2 rejector API", True),
            ("CodeVerificationQueueItem", "CodeVerificationQueue API", True),
            ("/api/v3/true-api/orders", "True API v3", False),
            ("/api/shipment-refund", "shipment-refund API", False),
            ("/api/warehouse", "warehouse API", False),
        ]
        for message, label, is_l2 in cases:
            for text in (message, message.lower(), message.upper(), message.swapcase()):
                with self.subTest(text=text):
                    result = detect_log_profile([entry("DataService", text)])
                    self.assertEqual(("api:" + label,), result.l2_signals if is_l2 else result.l3_signals)
                    self.assertEqual(1, result.l2_score + result.l3_score)

    def test_text_signals_do_not_cross_record_boundaries(self):
        for marker in ("codeverificationqueueitem", "/api/warehouse",
                       "команда на деактивацию отбраковщика от l2"):
            for split in range(1, len(marker)):
                with self.subTest(marker=marker, split=split):
                    result = detect_log_profile([
                        entry("DataService", marker[:split]),
                        entry("DataService", marker[split:]),
                    ])
                    self.assertEqual(0, result.l2_score + result.l3_score)

    def test_late_signals_and_duplicates_keep_complete_evidence(self):
        entries = [entry("PLCService", "ordinary message")] * 10000
        entries.extend([
            entry("DataService", "CODEVERIFICATIONQUEUEITEM"),
            entry("DataService", "GET /API/WAREHOUSE"),
            entry("TrueApiService", "GET /API/WAREHOUSE"),
        ])
        result = detect_log_profile(iter(entries))
        self.assertEqual(LogProfile.MIXED_UNKNOWN, result.profile)
        self.assertEqual(("api:CodeVerificationQueue API", "logger:PLCService"), result.l2_signals)
        self.assertEqual(("api:warehouse API", "logger:TrueApiService"), result.l3_signals)
        self.assertEqual((2, 2), (result.l2_score, result.l3_score))

        incremental = detect_log_profile([])
        for start in range(0, len(entries), 511):
            incremental = update_log_profile(incremental, entries[start:start + 511])
        self.assertEqual(result, incremental)
        self.assertEqual(result, update_log_profile(result, []))

    def test_unicode_matching_keeps_lower_semantics(self):
        result = detect_log_profile([
            entry("DataService", "/ap\u0131/warehouse"),
            entry("DataService", "/API/WAREHOU\u017fE"),
            entry("DataService", "/AP\u0130/WAREHOUSE"),
        ])
        self.assertEqual(0, result.l2_score + result.l3_score)
        self.assertEqual(detect_log_profile([]), detect_log_profile([SimpleNamespace()]))

    def test_missing_queue_signal_does_not_lower_unrelated_messages(self):
        class UnrelatedMessage(str):
            def lower(self):
                raise AssertionError('Unrelated messages must not be converted to lowercase')

        initial = detect_log_profile([
            entry("PLCService", "Команда на деактивацию отбраковщика от L2"),
        ])
        result = update_log_profile(initial, [
            entry("DataService", UnrelatedMessage("Получены данные от контроллера")),
            entry("SuzService", UnrelatedMessage("Поздний компонент")),
        ])
        self.assertEqual(initial.l2_signals, result.l2_signals)
        self.assertEqual(("logger:SuzService",), result.l3_signals)
        self.assertEqual(LogProfile.MIXED_UNKNOWN, result.profile)

        late = update_log_profile(result, [entry("DataService", "CoDeVeRiFiCaTiOnQuEuEiTeM")])
        self.assertIn("api:CodeVerificationQueue API", late.l2_signals)
        self.assertEqual(result.l3_signals, late.l3_signals)

    def test_required_characters_cover_unicode_lower_variants(self):
        # Пропуск до lower() безопасен только при учёте всех вариантов символа,
        # включая символы, преобразующиеся в несколько кодовых точек.
        definitions = _L2_TEXT_SIGNALS + _L3_TEXT_SIGNALS
        variants = {chars[0].lower(): set() for _, _, chars in definitions}
        for codepoint in range(sys.maxunicode + 1):
            char = chr(codepoint)
            for lowered in char.lower():
                if lowered in variants:
                    variants[lowered].add(char)
        for marker, label, chars in definitions:
            with self.subTest(signal=label):
                required = chars[0].lower()
                self.assertIn(required, marker)
                self.assertTrue(variants[required].issubset(chars))

    def test_new_loggers_are_found_after_all_text_signals(self):
        initial = detect_log_profile([entry("DataService", (
            "Команда на деактивацию отбраковщика от L2 CodeVerificationQueueItem "
            "/api/v3/true-api/ /api/shipment-refund /api/warehouse"))])
        result = update_log_profile(initial, [entry("PLCService"), entry("SuzService")])
        self.assertEqual((2, 3), (initial.l2_score, initial.l3_score))
        self.assertEqual((3, 4), (result.l2_score, result.l3_score))
        self.assertIn("logger:PLCService", result.l2_signals)
        self.assertIn("logger:SuzService", result.l3_signals)

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
