"""Просмотр исходных строк без предварительной раскладки всего журнала."""
from PyQt6.QtCore import QEvent, QPoint, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QTableView


class RawLogView(QTableView):
    """Один столбец с фиксированной высотой строк; Qt рассчитывает только видимую область."""

    firstRowsPainted = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paint_reported = False
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().hide()
        self.horizontalHeader().hide()
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setResizeContentsPrecision(100)
        self._update_row_height()

    def _update_row_height(self):
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 10)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._update_row_height()

    def setModel(self, model):
        super().setModel(model)
        if model is not None:
            model.modelReset.connect(self._reset_paint_report)

    def _reset_paint_report(self):
        self._paint_reported = False

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._paint_reported and self.indexAt(QPoint(5, 5)).isValid():
            self._paint_reported = True
            self.firstRowsPainted.emit(self.model().rowCount())
