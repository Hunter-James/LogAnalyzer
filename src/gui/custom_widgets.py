from PyQt6.QtWidgets import QListView, QTextEdit
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QWheelEvent

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
