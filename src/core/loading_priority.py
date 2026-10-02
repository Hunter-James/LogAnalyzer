"""Приоритет просмотра и последних запросов анализа открытых журналов."""
from contextlib import contextmanager
from threading import Condition


class LoadingPriority:
    """Приостанавливает фоновые проходы на время быстрого чтения без потери данных."""

    def __init__(self):
        self._condition = Condition()
        self._readers = 0
        self._analyses = []

    @contextmanager
    def analysis(self):
        """Последний начатый анализ выполняется первым; предыдущие сохраняют позицию."""
        token = object()
        with self._condition:
            self._analyses.append(token)
        try:
            yield token
        finally:
            with self._condition:
                self._analyses.remove(token)
                self._condition.notify_all()

    @contextmanager
    def preview(self):
        """Регистрирует чтение; ошибка и отмена также возобновляют анализ."""
        with self._condition:
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    def cancelled_after_preview(self, cancelled, analysis=None):
        """Уступает просмотрам и новым проходам, проверяя отмену закрытой вкладки."""
        with self._condition:
            while self._readers or (analysis is not None and self._analyses[-1] is not analysis):
                if cancelled():
                    return True
                self._condition.wait(.025)
        return cancelled()


loading_priority = LoadingPriority()
