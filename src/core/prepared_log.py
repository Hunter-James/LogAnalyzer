"""Подготовка индексов журнала в потоке загрузки до публикации в интерфейс."""
from dataclasses import dataclass

from core.batch_index import parse_batch_index
from core.log_profile import detect_log_profile, update_log_profile


@dataclass
class PreparedLog:
    """Готовые данные модели; после передачи загрузчик их не изменяет."""

    profile: object
    indices: list
    segments: list
    batch_for_index: list
    loggers: list


def prepare_log(entries, cancelled):
    """Собирает метаданные с ограниченными интервалами проверки отмены."""
    profile = detect_log_profile([])
    loggers = set()
    for start in range(0, len(entries), 2048):
        if cancelled():
            raise InterruptedError
        chunk = entries[start:start + 2048]
        profile = update_log_profile(profile, chunk)
        loggers.update(entry.logger for entry in chunk if entry.logger)
    segments, batch_for_index = parse_batch_index(entries, cancelled=cancelled)
    if cancelled():
        raise InterruptedError
    return PreparedLog(profile, list(range(len(entries))), segments, batch_for_index, sorted(loggers))
