"""Автоматическое определение профиля аналитики журнала."""

from dataclasses import dataclass
from enum import Enum


class LogProfile(Enum):
    """Поддерживаемые семейства журналов."""

    SMART_L2 = "SmartL2"
    SMART_L3 = "SmartL3"
    MIXED_UNKNOWN = "Смешанный/неизвестный"


@dataclass(frozen=True)
class ProfileDetection:
    """Результат определения профиля с данными для диагностики."""

    profile: LogProfile
    l2_score: int = 0
    l3_score: int = 0
    l2_signals: tuple = ()
    l3_signals: tuple = ()


# Используются только классы, которые действительно пишут журналы и существуют
# лишь в одном из двух исходных проектов. Общие инфраструктурные классы намеренно
# исключены, чтобы одна строка DataService/HttpLoggingFilter не решала профиль.
_L2_LOGGERS = frozenset({
    "AggregationBase", "CodeVerificationQueueBase", "CodeVerificationQueueV1",
    "CodeVerificationQueueV2", "FileWatcher", "HIKROBOT", "LinearAggregation",
    "PLCService", "PrintService", "SerializationService", "TemplateService",
})
_L3_LOGGERS = frozenset({
    "BatchService", "CertificateService", "CodesReportService", "ImportServiceMarkL3",
    "MdlpApiService", "ReportService", "SuzService", "TrueApiService",
    "WarehouseService",
})

_L2_TEXT_SIGNALS = (
    ("команда на деактивацию отбраковщика от l2", "L2 rejector API"),
    ("codeverificationqueueitem", "CodeVerificationQueue API"),
)
_L3_TEXT_SIGNALS = (
    ("/api/v3/true-api/", "True API v3"),
    ("/api/shipment-refund", "shipment-refund API"),
    ("/api/warehouse", "warehouse API"),
)


def _collect_signals(entries, l2_signals=(), l3_signals=()):
    """Ищет только новые признаки, сохраняя полное множество доказательств."""
    l2_signals = set(l2_signals)
    l3_signals = set(l3_signals)
    pending = [
        (marker, f"api:{label}", found)
        for definitions, found in ((_L2_TEXT_SIGNALS, l2_signals), (_L3_TEXT_SIGNALS, l3_signals))
        for marker, label in definitions
        if f"api:{label}" not in found
    ]
    # После обнаружения признаков L2 обычно остаются только API-пути L3.
    # Условие выводится из самих маркеров: новые правила не требуют правки фильтра.
    requires_slash = all('/' in marker for marker, _, _ in pending)
    loggers = set()
    for entry in entries:
        loggers.add(getattr(entry, "logger", ""))
        if not pending:
            continue
        message = getattr(entry, "message", "")
        if requires_slash and '/' not in message:
            continue
        text = message.lower()
        matched = False
        for marker, signal, found in pending:
            if marker in text:
                found.add(signal)
                matched = True
        if matched:
            pending = [(marker, signal, found) for marker, signal, found in pending if signal not in found]
            requires_slash = all('/' in marker for marker, _, _ in pending)

    l2_signals.update(f"logger:{logger}" for logger in loggers & _L2_LOGGERS)
    l3_signals.update(f"logger:{logger}" for logger in loggers & _L3_LOGGERS)
    return l2_signals, l3_signals


def _build_detection(l2_signals, l3_signals):
    """Собирает итог после полного или инкрементального прохода."""

    l2_score = len(l2_signals)
    l3_score = len(l3_signals)
    if l2_score and not l3_score:
        profile = LogProfile.SMART_L2
    elif l3_score and not l2_score:
        profile = LogProfile.SMART_L3
    else:
        profile = LogProfile.MIXED_UNKNOWN

    return ProfileDetection(
        profile=profile,
        l2_score=l2_score,
        l3_score=l3_score,
        l2_signals=tuple(sorted(l2_signals)),
        l3_signals=tuple(sorted(l3_signals)),
    )


def detect_log_profile(entries):
    """Определяет профиль по уникальным логгерам и характерным API журнала.

    Наличие уверенных признаков обоих продуктов намеренно даёт безопасный
    MIXED_UNKNOWN: профильные правила не должны применяться к смешанному файлу.
    """
    return _build_detection(*_collect_signals(entries))


def update_log_profile(current, new_entries):
    """Дополняет результат признаками новых tail-записей без повторного прохода."""
    return _build_detection(*_collect_signals(new_entries, current.l2_signals, current.l3_signals))
