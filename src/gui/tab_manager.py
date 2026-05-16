import os
import sys
import ctypes
import subprocess
from PyQt6.QtWidgets import (QTabWidget, QSplitter, QWidget, QVBoxLayout, QMenu,
                             QTabBar, QApplication)
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QPoint, QTimer
from PyQt6.QtGui import QDrag, QPixmap, QCursor, QPainter, QColor
from gui.log_viewer import LogViewerWidget


class DraggableTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.drag_start_pos = None
        self.selected_indices = set()
        self.last_clicked_index = -1

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.pos()

            index = self.tabAt(event.pos())
            if index >= 0:
                modifiers = QApplication.keyboardModifiers()

                if modifiers == Qt.KeyboardModifier.ControlModifier:
                    if index in self.selected_indices:
                        self.selected_indices.remove(index)
                    else:
                        self.selected_indices.add(index)
                    self.last_clicked_index = index
                    self.update()
                    return

                elif modifiers == Qt.KeyboardModifier.ShiftModifier:
                    if self.last_clicked_index >= 0:
                        start = min(self.last_clicked_index, index)
                        end = max(self.last_clicked_index, index)
                        self.selected_indices.update(range(start, end + 1))
                        self.update()
                        return
                else:
                    self.selected_indices = {index}
                    self.last_clicked_index = index
                    self.update()

        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if getattr(self, 'selected_indices', None):
            painter = QPainter(self)
            for i in self.selected_indices:
                if i != self.currentIndex():
                    rect = self.tabRect(i)
                    painter.fillRect(rect, QColor(130, 180, 255, 50))
            painter.end()

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

        parent = self.parent()
        if isinstance(parent, EditorTabWidget):
            EditorTabWidget._drag_source = parent

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setText(str(tab_index))
        mime_data.setData("application/x-loganalyzer-tab", b"dummy")

        drag.setMimeData(mime_data)

        rect = self.tabRect(tab_index)
        pixmap = self.grab(rect)
        drag.setPixmap(pixmap)
        drag.setHotSpot(event.pos() - rect.topLeft())

        drag.exec(Qt.DropAction.MoveAction)

        if isinstance(parent, EditorTabWidget):
            EditorTabWidget._drag_source = None

    def dragEnterEvent(self, event):
        self.parent().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.parent().dragMoveEvent(event)

    def dropEvent(self, event):
        self.parent().dropEvent(event)


class EditorTabWidget(QTabWidget):
    moveTabRequested = pyqtSignal(int)
    tabActivated = pyqtSignal(QWidget)
    tabDropped = pyqtSignal()

    _drag_source = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabsClosable(True)
        self.setMovable(False)
        self.setAcceptDrops(True)

        self.tab_bar = DraggableTabBar(self)
        self.setTabBar(self.tab_bar)

        self.tabCloseRequested.connect(self.close_tab)
        self.currentChanged.connect(self.on_current_changed)

        self.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabBar().customContextMenuRequested.connect(self.show_context_menu)

    def close_tab(self, index):
        if index in self.tab_bar.selected_indices:
            self.tab_bar.selected_indices.remove(index)

        new_selection = set()
        for i in self.tab_bar.selected_indices:
            if i > index:
                new_selection.add(i - 1)
            else:
                new_selection.add(i)
        self.tab_bar.selected_indices = new_selection
        self.tab_bar.update()

        widget = self.widget(index)
        if widget:
            self.removeTab(index)
            widget.deleteLater()

    def _close_multiple_tabs(self, indices):
        for i in sorted(indices, reverse=True):
            self.close_tab(i)

    def show_context_menu(self, point):
        index = self.tabBar().tabAt(point)
        if index < 0:
            return

        if index not in self.tab_bar.selected_indices:
            self.tab_bar.selected_indices = {index}
            self.tab_bar.last_clicked_index = index
            self.tab_bar.update()

        menu = QMenu(self)
        action_close = menu.addAction("Закрыть вкладку")

        action_close_selected = None
        if len(self.tab_bar.selected_indices) > 1:
            action_close_selected = menu.addAction(
                f"Закрыть выделенные ({len(self.tab_bar.selected_indices)})"
            )

        menu.addSeparator()
        action_close_others = menu.addAction("Закрыть остальные")
        action_close_left = menu.addAction("Закрыть слева")
        action_close_right = menu.addAction("Закрыть справа")
        action_close_all = menu.addAction("Закрыть все")
        menu.addSeparator()
        action_move = menu.addAction("Переместить в другую панель")

        # Пункты для конкретного файла - только если у вкладки есть file_path
        action_open_in_explorer = None
        action_copy_path = None
        widget = self.widget(index)
        file_path = getattr(widget, 'file_path', None)
        if file_path:
            menu.addSeparator()
            action_open_in_explorer = menu.addAction("Открыть в проводнике")
            action_copy_path = menu.addAction("Копировать полный путь")

        action = menu.exec(self.tabBar().mapToGlobal(point))

        if not action:
            return

        if action == action_close:
            self.close_tab(index)
        elif action == action_close_selected:
            self._close_multiple_tabs(self.tab_bar.selected_indices)
        elif action == action_close_others:
            to_close = set(range(self.count())) - self.tab_bar.selected_indices
            self._close_multiple_tabs(to_close)
        elif action == action_close_left:
            self._close_multiple_tabs(range(0, index))
        elif action == action_close_right:
            self._close_multiple_tabs(range(index + 1, self.count()))
        elif action == action_close_all:
            self._close_multiple_tabs(range(self.count()))
        elif action == action_move:
            self.moveTabRequested.emit(index)
        elif action == action_open_in_explorer and file_path:
            self._open_in_explorer(file_path)
        elif action == action_copy_path and file_path:
            QApplication.clipboard().setText(file_path)

    def _open_in_explorer(self, file_path):
        """Открывает Проводник Windows с выделенным файлом (как в VS Code 'Reveal in Explorer').

        ВАЖНО: explorer.exe ожидает /select,<path> как ОДИН токен командной строки
        (после запятой - сразу значение). subprocess.Popen со списком на Windows
        конвертирует аргументы через list2cmdline, и при пробелах в пути explorer
        видит /select,"C:\\path with space\\file.log" как два аргумента и обрывает
        путь на первом пробеле - на скриншоте юзера так и получалось:
        путь доходил только до Documents\\, а дальше "работа Селена Люкс" терялись.

        Решение - передать команду одной строкой, тогда CreateProcess сохраняет
        её как есть, и explorer корректно парсит /select,"<full path>"."""
        normalized = os.path.normpath(file_path)
        if sys.platform == 'win32':
            subprocess.Popen(f'explorer /select,"{normalized}"')
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', normalized])
        else:
            subprocess.Popen(['xdg-open', os.path.dirname(normalized)])

    def on_current_changed(self, index):
        if index >= 0:
            if not QApplication.keyboardModifiers() & (
                    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                self.tab_bar.selected_indices = {index}
                self.tab_bar.last_clicked_index = index
                self.tab_bar.update()

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

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-loganalyzer-tab"):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-loganalyzer-tab"):
            try:
                source_index = int(event.mimeData().text())
                source_widget = EditorTabWidget._drag_source

                if not source_widget:
                    event.ignore()
                    return

                if source_widget == self:
                    drop_pos = event.position().toPoint()
                    tab_bar_pos = self.tabBar().mapFrom(self, drop_pos)
                    target_index = self.tabBar().tabAt(tab_bar_pos)

                    if target_index == -1:
                        if self.tabBar().geometry().contains(tab_bar_pos):
                            target_index = self.count() - 1
                        else:
                            QTimer.singleShot(0, lambda: self.moveTabRequested.emit(source_index))
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
                    widget = source_widget.widget(source_index)
                    text = source_widget.tabText(source_index)

                    source_widget.removeTab(source_index)

                    self.addTab(widget, text)
                    self.setCurrentWidget(widget)
                    self.setFocus()

                    self.tabDropped.emit()
                    source_widget.tabDropped.emit()

                event.accept()
            except Exception as e:
                print(f"Drop error: {e}")
                event.ignore()


class SplitManager(QSplitter):
    activeTabChanged = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)

        self.left_tabs = EditorTabWidget()
        self.right_tabs = EditorTabWidget()

        self.addWidget(self.left_tabs)
        self.addWidget(self.right_tabs)

        self.right_tabs.hide()

        self.left_tabs.moveTabRequested.connect(self.move_to_right)
        self.right_tabs.moveTabRequested.connect(self.move_to_left)

        self.left_tabs.tabActivated.connect(self.on_tab_activated)
        self.right_tabs.tabActivated.connect(self.on_tab_activated)

        self.left_tabs.tabDropped.connect(self.check_visibility)
        self.right_tabs.tabDropped.connect(self.check_visibility)

        self.active_group = self.left_tabs

    def check_visibility(self):
        if self.right_tabs.count() == 0:
            self.right_tabs.hide()

        if self.active_group.count() == 0:
            other = self.right_tabs if self.active_group == self.left_tabs else self.left_tabs
            if other.isVisible() and other.count() > 0:
                self.active_group = other
                self.activeTabChanged.emit(other.currentWidget())
            else:
                self.activeTabChanged.emit(None)

    def add_tab(self, widget, title, side="active", silent=False):
        """Добавляет вкладку и - если silent=False - испускает activeTabChanged.

        silent=True используется при restore_session: иначе для каждого
        добавляемого lazy-таба отработает сигнал → ensure_loaded() → все
        логи загрузятся один за другим.

        ВАЖНО: помимо нашего ручного emit (в конце метода) есть ещё Qt-сигнал
        currentChanged, который испускается setCurrentIndex и каскадно дёргает
        on_current_changed → tabActivated → SplitManager.on_tab_activated →
        activeTabChanged. Чтобы silent действительно был silent, временно
        блокируем сигналы у target через blockSignals."""
        target = self.active_group

        if side == "left":
            target = self.left_tabs
        elif side == "right":
            target = self.right_tabs
        elif not self.right_tabs.isVisible():
            target = self.left_tabs

        if silent:
            target.blockSignals(True)
        try:
            index = target.addTab(widget, title)
            target.setCurrentIndex(index)
        finally:
            if silent:
                target.blockSignals(False)

        if not target.isVisible():
            target.show()

        target.setFocus()
        self.active_group = target
        if not silent:
            self.activeTabChanged.emit(widget)

    def get_open_files(self):
        files_left = []
        files_right = []

        for i in range(self.left_tabs.count()):
            widget = self.left_tabs.widget(i)
            if isinstance(widget, LogViewerWidget):
                files_left.append(widget.file_path)

        for i in range(self.right_tabs.count()):
            widget = self.right_tabs.widget(i)
            if isinstance(widget, LogViewerWidget):
                files_right.append(widget.file_path)

        return files_left, files_right

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

        was_hidden = not target.isVisible()
        if was_hidden:
            target.show()
            half_width = self.width() // 2
            self.setSizes([half_width, half_width])

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
