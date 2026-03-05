import os
import ctypes
from PyQt6.QtWidgets import (QTabWidget, QSplitter, QWidget, QVBoxLayout, QMenu, 
                             QTabBar, QApplication)
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QPoint
from PyQt6.QtGui import QDrag, QPixmap, QCursor
from gui.log_viewer import LogViewerWidget

class DraggableTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        # TabBar initiates drag, but doesn't accept drops itself (the TabWidget does)
        self.setAcceptDrops(False) 
        self.drag_start_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if not self.drag_start_pos:
            return
        
        if (event.pos() - self.drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        tab_index = self.tabAt(self.drag_start_pos)
        if tab_index < 0:
            return

        # Start Drag
        drag = QDrag(self)
        mime_data = QMimeData()
        
        # Pass the python id of the parent TabWidget and the tab index
        parent_widget = self.parent()
        source_id = id(parent_widget)
        mime_data.setText(f"{source_id}|{tab_index}")
        mime_data.setData("application/x-loganalyzer-tab", b"dummy")
        
        drag.setMimeData(mime_data)
        
        # Visual feedback
        rect = self.tabRect(tab_index)
        pixmap = self.grab(rect)
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos() - rect.topLeft())
        
        drag.exec(Qt.DropAction.MoveAction)


class EditorTabWidget(QTabWidget):
    moveTabRequested = pyqtSignal(int)
    tabActivated = pyqtSignal(QWidget)
    tabDropped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabsClosable(True)
        self.setMovable(False)  # Мы обрабатываем перемещение вручную
        self.setAcceptDrops(True)

        self.tab_bar = DraggableTabBar(self)
        self.setTabBar(self.tab_bar)

        self.tabCloseRequested.connect(self.close_tab)
        self.currentChanged.connect(self.on_current_changed)

        self.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabBar().customContextMenuRequested.connect(self.show_context_menu)

    def close_tab(self, index):
        widget = self.widget(index)
        if widget:
            self.removeTab(index)
            widget.deleteLater()

    def show_context_menu(self, point):
        index = self.tabBar().tabAt(point)
        if index < 0:
            return

        menu = QMenu(self)
        action_move = menu.addAction("Move to Other View")
        action_close = menu.addAction("Close")

        action = menu.exec(self.tabBar().mapToGlobal(point))

        if action == action_move:
            self.moveTabRequested.emit(index)
        elif action == action_close:
            self.close_tab(index)

    def on_current_changed(self, index):
        if index >= 0:
            widget = self.widget(index)
            if widget:
                self.tabActivated.emit(widget)
        else:
            self.tabActivated.emit(None)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-loganalyzer-tab"):
            event.accept()
        else:
            event.ignore()

    # 👇 ДОБАВЛЕНО: Без этого события PyQt отменяет Drop при движении мыши!
    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-loganalyzer-tab"):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-loganalyzer-tab"):
            data = event.mimeData().text().split('|')
            source_id = int(data[0])
            source_index = int(data[1])

            # Получаем исходный виджет
            source_widget = ctypes.cast(source_id, ctypes.py_object).value

            if source_widget == self:
                # Перетаскивание внутри одного и того же окна
                drop_pos = event.position().toPoint()
                tab_bar_pos = self.tabBar().mapFrom(self, drop_pos)
                target_index = self.tabBar().tabAt(tab_bar_pos)

                if target_index == -1:
                    if self.tabBar().geometry().contains(tab_bar_pos):
                        # Бросили на панель вкладок, но мимо конкретной -> в конец
                        target_index = self.count() - 1
                    else:
                        # 👇 ДОБАВЛЕНО: Бросили в тело текущего лога -> Отправляем в другое окно (Split)!
                        self.moveTabRequested.emit(source_index)
                        event.accept()
                        return

                if source_index != target_index:
                    widget = self.widget(source_index)
                    text = self.tabText(source_index)

                    self.blockSignals(True)
                    self.removeTab(source_index)
                    self.insertTab(target_index, widget, text)
                    self.setCurrentIndex(target_index)
                    self.blockSignals(False)

                    self.tabActivated.emit(widget)
            else:
                # Перетаскивание из другого окна (когда сплиттер уже разделен)
                widget = source_widget.widget(source_index)
                text = source_widget.tabText(source_index)

                source_widget.removeTab(source_index)

                self.addTab(widget, text)
                self.setCurrentWidget(widget)
                self.setFocus()

                self.tabDropped.emit()
                source_widget.tabDropped.emit()

            event.accept()

class SplitManager(QSplitter):
    activeTabChanged = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        
        self.left_tabs = EditorTabWidget()
        self.right_tabs = EditorTabWidget()
        
        self.addWidget(self.left_tabs)
        self.addWidget(self.right_tabs)
        
        self.right_tabs.hide()
        
        # Connect signals
        self.left_tabs.moveTabRequested.connect(self.move_to_right)
        self.right_tabs.moveTabRequested.connect(self.move_to_left)
        
        self.left_tabs.tabActivated.connect(self.on_tab_activated)
        self.right_tabs.tabActivated.connect(self.on_tab_activated)
        
        self.left_tabs.tabDropped.connect(self.check_visibility)
        self.right_tabs.tabDropped.connect(self.check_visibility)
        
        self.active_group = self.left_tabs

    def check_visibility(self):
        # Hide right tabs if empty
        if self.right_tabs.count() == 0:
            self.right_tabs.hide()
        
        # Ensure active group is valid
        if self.active_group.count() == 0:
            other = self.right_tabs if self.active_group == self.left_tabs else self.left_tabs
            if other.isVisible() and other.count() > 0:
                self.active_group = other
                self.activeTabChanged.emit(other.currentWidget())
            else:
                self.activeTabChanged.emit(None)

    def add_tab(self, widget, title):
        target = self.active_group
        if not self.right_tabs.isVisible():
            target = self.left_tabs
            
        index = target.addTab(widget, title)
        target.setCurrentIndex(index)
        
        if not target.isVisible():
            target.show()
            
        target.setFocus()
        self.active_group = target
        self.activeTabChanged.emit(widget)

    def move_to_right(self, index):
        self._move_tab(index, self.left_tabs, self.right_tabs)

    def move_to_left(self, index):
        self._move_tab(index, self.right_tabs, self.left_tabs)

    def _move_tab(self, index, source, target):
        widget = source.widget(index)
        text = source.tabText(index)
        source.removeTab(index)
        
        new_index = target.addTab(widget, text)
        target.setCurrentIndex(new_index)
        
        if source.count() == 0 and source == self.right_tabs:
            source.hide()
        
        if not target.isVisible():
            target.show()
            
        self.active_group = target
        target.setFocus()
        self.activeTabChanged.emit(widget)

    def on_tab_activated(self, widget):
        if widget:
            sender = self.sender()
            if isinstance(sender, EditorTabWidget):
                self.active_group = sender
            
            if isinstance(widget, LogViewerWidget):
                self.activeTabChanged.emit(widget)
        else:
            self.check_visibility()

    def get_current_viewer(self):
        if self.active_group.count() > 0:
            w = self.active_group.currentWidget()
            if isinstance(w, LogViewerWidget):
                return w
        
        other = self.right_tabs if self.active_group == self.left_tabs else self.left_tabs
        if other.isVisible() and other.count() > 0:
            w = other.currentWidget()
            if isinstance(w, LogViewerWidget):
                return w
                
        return None
