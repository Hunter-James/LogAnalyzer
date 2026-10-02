"""Хранение отменённых потоков до фактического завершения без блокировки UI."""
from PyQt6.QtCore import QObject, QThread, pyqtSlot


class RetiredThreads(QObject):
    """Владеет потоками, чьи вкладки уже закрыты или выгружены."""

    def __init__(self):
        super().__init__()
        self.workers = set()

    def retire(self, worker):
        if worker in self.workers:
            return
        worker.requestInterruption()
        if hasattr(worker, 'cancel'):
            worker.cancel()
        # Отсоединяем и пользовательский результат, и системный finished:
        # ни один callback закрытой вкладки не должен запускать новую работу.
        for signal in (worker.finished, QThread.finished.__get__(worker),
                       getattr(worker, 'progress', None)):
            if signal is not None:
                try:
                    signal.disconnect()
                except (TypeError, RuntimeError):
                    pass
        worker.setParent(None)
        self.workers.add(worker)
        QThread.finished.__get__(worker).connect(self._finished)
        if not worker.isRunning():
            self._release(worker)

    @pyqtSlot()
    def _finished(self):
        self._release(self.sender())

    def _release(self, worker):
        if worker in self.workers:
            self.workers.remove(worker)
            worker.deleteLater()


_registry = None


def retired_threads():
    """Возвращает реестр; вызывать только из GUI-потока."""
    global _registry
    if _registry is None:
        _registry = RetiredThreads()
    return _registry
