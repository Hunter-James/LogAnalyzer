from PyQt6.QtWidgets import QListView, QTextEdit, QScrollBar, QStyle, QStyleOptionSlider
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QWheelEvent, QPainter, QColor

# --- Custom Widgets for Zooming ---
class ScalableListView(QListView):
    zoomRequest = pyqtSignal(int)

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            self.zoomRequest.emit(delta)
            event.accept()
        else:
            super().wheelEvent(event)


class ScalableTextEdit(QTextEdit):
    zoomRequest = pyqtSignal(int)

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            self.zoomRequest.emit(delta)
            event.accept()
        else:
            super().wheelEvent(event)


class MarkerScrollBar(QScrollBar):
    """Вертикальный скроллбар, который рисует поверх полоски-метки в заданных
    относительных позициях. Используется для подсветки ERROR/WARN в логе."""

    def __init__(self, orientation=Qt.Orientation.Vertical, parent=None):
        super().__init__(orientation, parent)
        # Список (rel_pos в [0,1], QColor)
        self._markers = []

    def set_markers(self, markers):
        self._markers = list(markers)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._markers:
            return

        # Берём реальный rect "желоба" (без стрелок), чтобы метки не
        # попадали на кнопки прокрутки и совпадали с позицией ползунка.
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_ScrollBar, opt,
            QStyle.SubControl.SC_ScrollBarGroove, self,
        )
        if groove.height() <= 0 or groove.width() <= 0:
            return

        painter = QPainter(self)
        for rel_pos, color in self._markers:
            y = groove.top() + int(rel_pos * (groove.height() - 2))
            painter.fillRect(groove.left(), y, groove.width(), 2, color)
        painter.end()
