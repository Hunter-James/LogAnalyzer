"""Частота сборки старых объектов при массовом создании записей журнала."""
from contextlib import contextmanager
import gc
from threading import RLock


class LoadingMemory:
    """Объединяет пересекающиеся загрузки в один период настройки общего GC."""

    def __init__(self):
        self._lock = RLock()
        self._users = 0
        self._saved = None
        self._temporary = None

    @contextmanager
    def batch(self):
        """Откладывает автоматические проходы по старым объектам до конца загрузок.

        Миллионы LogEntry переживают все сборки до закрытия вкладки.
        Повторный обход этих объектов при заполнении списка расходует время
        и останавливает GUI из-за GIL. Сам GC не отключается.
        """
        with self._lock:
            if not self._users and gc.isenabled():
                self._saved = gc.get_threshold()
                self._temporary = (*self._saved[:2], max(self._saved[2], 1_000_000))
                gc.set_threshold(*self._temporary)
            self._users += 1
        try:
            yield
        finally:
            with self._lock:
                self._users -= 1
                if not self._users and self._saved is not None:
                    # Не запускаем сборку вручную в рабочем потоке: среди
                    # циклических объектов могут быть виджеты Qt.
                    if gc.get_threshold() == self._temporary:
                        gc.set_threshold(*self._saved)
                    self._saved = self._temporary = None


loading_memory = LoadingMemory()
