import os
import sys
import ctypes
import subprocess
from PyQt6.QtWidgets import (QTabWidget, QSplitter, QWidget, QVBoxLayout, QHBoxLayout, QMenu,
                             QTabBar, QApplication, QLabel, QToolButton, QFrame,
                             QInputDialog, QColorDialog)
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData, QPoint, QTimer
from PyQt6.QtGui import QDrag, QPixmap, QCursor, QPainter, QColor
from gui.log_viewer import LogViewerWidget


# Палитра цветов по умолчанию для новых групп (Material-подобные тёплые).
DEFAULT_GROUP_COLORS = [
    '#E53935',  # красный
    '#43A047',  # зелёный
    '#1E88E5',  # синий
    '#FB8C00',  # оранжевый
    '#8E24AA',  # фиолетовый
    '#00897B',  # бирюзовый
    '#5E35B1',  # индиго
    '#FDD835',  # жёлтый
]


class GroupHeader(QFrame):
    """Плашка-заголовок группы табов: цветной фон + имя + кнопка меню.

    Сигналы:
      renameRequested(str) - юзер ввёл новое имя
      colorRequested(str)  - юзер выбрал новый цвет (hex)
    """

    renameRequested = pyqtSignal(str)
    colorRequested = pyqtSignal(str)

    def __init__(self, name, color, parent=None):
        super().__init__(parent)
        self._name = name
        self._color = color
        self.setFixedHeight(28)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # Контекстное меню по правой кнопке
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

        h = QHBoxLayout(self)
        h.setContentsMargins(10, 0, 4, 0)
        h.setSpacing(6)

        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet("color: white; font-weight: bold;")
        h.addWidget(self.lbl_name)
        h.addStretch()

        # Кнопка меню "⋮"
        self.btn_menu = QToolButton()
        self.btn_menu.setText("⋮")
        self.btn_menu.setFixedSize(22, 22)
        self.btn_menu.setStyleSheet(
            "QToolButton { color: white; background: transparent; border: none; "
            "font-size: 16px; font-weight: bold; }"
            "QToolButton:hover { background: rgba(255,255,255,40); border-radius: 3px; }"
        )
        self.btn_menu.clicked.connect(self._show_menu_at_button)
        h.addWidget(self.btn_menu)

        self._apply_color()

    def _apply_color(self):
        """Применяет цвет фона. Текст автоматически белый/чёрный по яркости."""
        c = QColor(self._color)
        # Простая luminance для выбора цвета текста
        lum = (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000
        text_color = '#000000' if lum > 160 else '#FFFFFF'
        self.setStyleSheet(
            f"GroupHeader {{ background-color: {self._color}; border-radius: 3px; }}"
        )
        self.lbl_name.setStyleSheet(
            f"color: {text_color}; font-weight: bold; background: transparent;"
        )
        # Кнопке меню тоже подкрашиваем
        hover_bg = "rgba(0,0,0,40)" if lum > 160 else "rgba(255,255,255,40)"
        self.btn_menu.setStyleSheet(
            f"QToolButton {{ color: {text_color}; background: transparent; "
            "border: none; font-size: 16px; font-weight: bold; }}"
            f"QToolButton:hover {{ background: {hover_bg}; border-radius: 3px; }}"
        )

    @property
    def name(self):
        return self._name

    @property
    def color(self):
        return self._color

    def set_name(self, name):
        self._name = name
        self.lbl_name.setText(name)

    def set_color(self, color):
        self._color = color
        self._apply_color()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._do_rename()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def _show_menu_at_button(self):
        pos = self.btn_menu.mapToGlobal(QPoint(0, self.btn_menu.height()))
        self._build_menu().exec(pos)

    def _show_menu(self, pos):
        self._build_menu().exec(self.mapToGlobal(pos))

    def _build_menu(self):
        menu = QMenu(self)
        act_rename = menu.addAction("Переименовать …")
        act_color = menu.addAction("Сменить цвет …")
        act_rename.triggered.connect(self._do_rename)
        act_color.triggered.connect(self._do_change_color)
        return menu

    def _do_rename(self):
        new, ok = QInputDialog.getText(
            self, "Переименовать группу", "Новое название группы:",
            text=self._name)
        if ok and new.strip():
            self.set_name(new.strip())
            self.renameRequested.emit(new.strip())

    def _do_change_color(self):
        col = QColorDialog.getColor(QColor(self._color), self, "Цвет группы")
        if col.isValid():
            hex_color = col.name()
            self.set_color(hex_color)
            self.colorRequested.emit(hex_color)


class GroupPanel(QWidget):
    """Один «слот группы» в SplitManager: заголовок-плашка сверху + TabWidget с
    табами под ним. Заменяет голый EditorTabWidget в сплиттере, чтобы было
    видно к какой группе относятся файлы."""

    def __init__(self, name, color, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        self.header = GroupHeader(name, color)
        self.tabs = EditorTabWidget()

        v.addWidget(self.header)
        v.addWidget(self.tabs, 1)


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
    """Контейнер двух «групп» табов. В этой итерации - всё ещё фиксированно
    две группы (раньше left_tabs / right_tabs), но каждая получила цветную
    плашку-заголовок (GroupPanel) с именем и цветом. Атрибуты left_tabs /
    right_tabs сохранены для совместимости с остальным кодом - указывают на
    .tabs внутри соответствующей панели.

    Сигнал groupConfigChanged эмитится когда юзер переименовал группу или
    сменил её цвет - MainWindow подписан на него, чтобы сохранить в settings."""

    activeTabChanged = pyqtSignal(object)
    groupConfigChanged = pyqtSignal()  # имя/цвет одной из групп изменились

    def __init__(self, parent=None, group_configs=None):
        super().__init__(Qt.Orientation.Horizontal, parent)

        # group_configs - список из 2 dict-ов с ключами name, color.
        # Если None или мало элементов - подставляем дефолты.
        cfgs = list(group_configs or [])
        while len(cfgs) < 2:
            cfgs.append(None)
        cfg_left = cfgs[0] or {}
        cfg_right = cfgs[1] or {}
        name_left = cfg_left.get('name') or 'Группа 1'
        color_left = cfg_left.get('color') or DEFAULT_GROUP_COLORS[0]
        name_right = cfg_right.get('name') or 'Группа 2'
        color_right = cfg_right.get('color') or DEFAULT_GROUP_COLORS[1]

        self.left_panel = GroupPanel(name_left, color_left)
        self.right_panel = GroupPanel(name_right, color_right)

        # Обратная совместимость API: код снаружи использует left_tabs/right_tabs.
        self.left_tabs = self.left_panel.tabs
        self.right_tabs = self.right_panel.tabs

        self.addWidget(self.left_panel)
        self.addWidget(self.right_panel)

        self.right_panel.hide()

        self.left_tabs.moveTabRequested.connect(self.move_to_right)
        self.right_tabs.moveTabRequested.connect(self.move_to_left)

        self.left_tabs.tabActivated.connect(self.on_tab_activated)
        self.right_tabs.tabActivated.connect(self.on_tab_activated)

        self.left_tabs.tabDropped.connect(self.check_visibility)
        self.right_tabs.tabDropped.connect(self.check_visibility)

        # Подписываемся на изменения header'ов - эмитим groupConfigChanged
        # чтобы MainWindow сохранил настройки.
        for header in (self.left_panel.header, self.right_panel.header):
            header.renameRequested.connect(self._on_group_config_changed)
            header.colorRequested.connect(self._on_group_config_changed)

        self.active_group = self.left_tabs

    def _on_group_config_changed(self, *_args):
        self.groupConfigChanged.emit()

    def get_group_configs(self):
        """Возвращает [{name, color}, {name, color}] - для сохранения в settings."""
        return [
            {'name': self.left_panel.header.name,
             'color': self.left_panel.header.color},
            {'name': self.right_panel.header.name,
             'color': self.right_panel.header.color},
        ]

    def check_visibility(self):
        # Скрываем/показываем целиком ПАНЕЛЬ (header + tabs), а не только tabs:
        # иначе заголовок-плашка остаётся торчать над пустым местом.
        if self.right_tabs.count() == 0:
            self.right_panel.hide()

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
        elif not self.right_panel.isVisible():
            target = self.left_tabs

        if silent:
            target.blockSignals(True)
        try:
            index = target.addTab(widget, title)
            target.setCurrentIndex(index)
        finally:
            if silent:
                target.blockSignals(False)

        # Показываем целиком панель (header + tabs), а не только сами tabs.
        owner_panel = (self.left_panel if target is self.left_tabs
                       else self.right_panel)
        if not owner_panel.isVisible():
            owner_panel.show()

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
