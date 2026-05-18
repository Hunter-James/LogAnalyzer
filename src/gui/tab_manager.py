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
    """Плашка-заголовок группы табов: цветной фон + имя + кнопки «+» и «⋮».

    Сигналы:
      renameRequested(str)       - юзер ввёл новое имя
      colorRequested(str)        - юзер выбрал новый цвет (hex)
      addGroupRequested()        - меню «Добавить группу справа» / кнопка «+»
      removeGroupRequested()     - меню «Удалить группу»
      collapseToggled()          - меню «Свернуть/Развернуть» / кнопка «▼/▲»
      archiveRequested()         - меню «Архивировать»
    """

    renameRequested = pyqtSignal(str)
    colorRequested = pyqtSignal(str)
    addGroupRequested = pyqtSignal()
    removeGroupRequested = pyqtSignal()
    collapseToggled = pyqtSignal()
    archiveRequested = pyqtSignal()
    filesDropped = pyqtSignal(list)  # drop'нуты пути файлов прямо на плашку
    activateRequested = pyqtSignal()  # клик по плашке - сделать группу активной

    def __init__(self, name, color, parent=None):
        super().__init__(parent)
        self._name = name
        self._color = color
        self._can_remove = True
        self._collapsed = False
        self._drag_hover = False
        self.setFixedHeight(28)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        # Принимаем drop файлов прямо на плашку - так юзер может бросить
        # .log в конкретную группу, не в активную по умолчанию.
        self.setAcceptDrops(True)

        h = QHBoxLayout(self)
        h.setContentsMargins(10, 0, 4, 0)
        h.setSpacing(4)

        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet("color: white; font-weight: bold;")
        h.addWidget(self.lbl_name)
        h.addStretch()

        # Кнопка свернуть/развернуть «▼/▲»
        self.btn_collapse = QToolButton()
        self.btn_collapse.setText("▼")
        self.btn_collapse.setFixedSize(22, 22)
        self.btn_collapse.setToolTip("Свернуть группу")
        self.btn_collapse.clicked.connect(self.collapseToggled.emit)
        h.addWidget(self.btn_collapse)

        # Кнопка «+» - добавить новую группу справа от этой
        self.btn_add = QToolButton()
        self.btn_add.setText("+")
        self.btn_add.setFixedSize(22, 22)
        self.btn_add.setToolTip("Добавить группу справа")
        self.btn_add.clicked.connect(self.addGroupRequested.emit)
        h.addWidget(self.btn_add)

        # Кнопка меню "⋮"
        self.btn_menu = QToolButton()
        self.btn_menu.setText("⋮")
        self.btn_menu.setFixedSize(22, 22)
        self.btn_menu.clicked.connect(self._show_menu_at_button)
        h.addWidget(self.btn_menu)

        self._apply_color()

    def _apply_color(self):
        """Применяет цвет фона. Текст автоматически белый/чёрный по яркости.
        Во время drag-hover добавляется яркая жёлтая рамка - сигнал
        пользователю «сюда можно бросать»."""
        c = QColor(self._color)
        lum = (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000
        text_color = '#000000' if lum > 160 else '#FFFFFF'
        if self._drag_hover:
            border = "border: 2px solid #FFD600;"
        else:
            border = ""
        self.setStyleSheet(
            f"GroupHeader {{ background-color: {self._color}; "
            f"border-radius: 3px; {border} }}"
        )
        self.lbl_name.setStyleSheet(
            f"color: {text_color}; font-weight: bold; background: transparent;"
        )
        hover_bg = "rgba(0,0,0,40)" if lum > 160 else "rgba(255,255,255,40)"
        btn_css = (
            f"QToolButton {{ color: {text_color}; background: transparent; "
            "border: none; font-size: 16px; font-weight: bold; }}"
            f"QToolButton:hover {{ background: {hover_bg}; border-radius: 3px; }}"
        )
        self.btn_menu.setStyleSheet(btn_css)
        self.btn_add.setStyleSheet(btn_css)
        self.btn_collapse.setStyleSheet(btn_css)

    def set_can_remove(self, can_remove):
        self._can_remove = bool(can_remove)

    def set_collapsed(self, collapsed):
        """Меняет visual-индикатор collapse-кнопки: ▼ когда развёрнута,
        ▲ когда свёрнута. Сам collapse выполняет GroupPanel."""
        self._collapsed = bool(collapsed)
        self.btn_collapse.setText("▲" if self._collapsed else "▼")
        self.btn_collapse.setToolTip(
            "Развернуть группу" if self._collapsed else "Свернуть группу"
        )

    # ----- Drop файлов на плашку -----

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            # Принимаем только если есть хоть один локальный файл
            if any(u.isLocalFile() for u in urls):
                self._drag_hover = True
                self._apply_color()  # перерисовать с подсветкой границы
                event.acceptProposedAction()
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._drag_hover = False
        self._apply_color()
        event.accept()

    def dropEvent(self, event):
        self._drag_hover = False
        self._apply_color()
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def mousePressEvent(self, event):
        # Левый клик по плашке - просим SplitManager сделать эту группу активной
        # (полезно в Stack-режиме, где это переключает видимый контент).
        if event.button() == Qt.MouseButton.LeftButton:
            self.activateRequested.emit()
        super().mousePressEvent(event)

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
        menu.addSeparator()
        act_collapse = menu.addAction(
            "Развернуть" if self._collapsed else "Свернуть")
        act_archive = menu.addAction("Архивировать (выгрузить файлы)")
        menu.addSeparator()
        act_add = menu.addAction("Добавить группу справа")
        act_remove = menu.addAction("Удалить группу")
        act_remove.setEnabled(self._can_remove)
        act_rename.triggered.connect(self._do_rename)
        act_color.triggered.connect(self._do_change_color)
        act_collapse.triggered.connect(self.collapseToggled.emit)
        act_archive.triggered.connect(self.archiveRequested.emit)
        act_add.triggered.connect(self.addGroupRequested.emit)
        act_remove.triggered.connect(self.removeGroupRequested.emit)
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
    табами под ним. Пробрасывает сигналы header'а наружу - чтобы SplitManager
    мог реагировать на «Добавить» / «Удалить» / «Свернуть» / «Архивировать»."""

    addGroupRequested = pyqtSignal()
    removeGroupRequested = pyqtSignal()
    collapseToggled = pyqtSignal()
    archiveRequested = pyqtSignal()
    filesDropped = pyqtSignal(list)
    activateRequested = pyqtSignal()

    def __init__(self, name, color, parent=None):
        super().__init__(parent)
        self._collapsed = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)

        self.header = GroupHeader(name, color)
        self.tabs = EditorTabWidget()

        v.addWidget(self.header)
        v.addWidget(self.tabs, 1)

        self.header.addGroupRequested.connect(self.addGroupRequested.emit)
        self.header.removeGroupRequested.connect(self.removeGroupRequested.emit)
        self.header.collapseToggled.connect(self.collapseToggled.emit)
        self.header.archiveRequested.connect(self.archiveRequested.emit)
        self.header.filesDropped.connect(self.filesDropped.emit)
        self.header.activateRequested.connect(self.activateRequested.emit)

    @property
    def collapsed(self):
        return self._collapsed

    def set_collapsed(self, collapsed):
        """Свернуть/развернуть группу. Файлы внутри остаются loaded -
        просто скрывается виджет табов. Заголовок остаётся видимым.
        Сама панель в QSplitter сжимается до высоты заголовка."""
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.tabs.setVisible(not collapsed)
        self.header.set_collapsed(collapsed)
        # Когда сворачиваем - фиксируем высоту панели до высоты заголовка,
        # чтобы splitter «сжал» её. При развороте снимаем ограничение.
        if collapsed:
            self.setMaximumHeight(self.header.height())
        else:
            self.setMaximumHeight(16777215)  # Qt-дефолт = неограничено


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
    """Контейнер «групп» табов. С раунда 2 - произвольное число групп
    (минимум 1, добавлять через add_group / удалять через remove_group).
    Каждая группа представлена GroupPanel'ью внутри сплиттера.

    Атрибуты left_tabs / right_tabs сохранены для backward-compat - указывают
    на первую и вторую панели соответственно (или None если их меньше).

    Сигналы:
      activeTabChanged(widget) - сменился активный таб;
      groupConfigChanged()     - имена/цвета/состав/collapsed групп изменились
                                  (MainWindow подписан → сохраняет settings);
      archiveChanged()         - группа уехала в архив или вернулась обратно.
                                  MainWindow перерисовывает меню «Архив»."""

    activeTabChanged = pyqtSignal(object)
    groupConfigChanged = pyqtSignal()
    archiveChanged = pyqtSignal()
    # filesDroppedOnGroup(panel_index, list_of_paths) - drop файлов на плашку
    # конкретной группы. MainWindow подписан и делает load_file в эту группу.
    filesDroppedOnGroup = pyqtSignal(int, list)

    def __init__(self, parent=None, group_configs=None):
        super().__init__(Qt.Orientation.Horizontal, parent)

        # group_configs - список dict-ов с ключами name, color. Если пусто -
        # стартуем с двух дефолтных групп.
        cfgs = list(group_configs or [])
        if len(cfgs) < 1:
            cfgs = [
                {'name': 'Группа 1', 'color': DEFAULT_GROUP_COLORS[0]},
                {'name': 'Группа 2', 'color': DEFAULT_GROUP_COLORS[1]},
            ]

        self.panels = []  # список GroupPanel в порядке отображения
        # Архив: список словарей с {name, color, files} - выгруженных групп.
        # Файлы НЕ хранятся в RAM, только пути. Восстанавливаются как lazy-табы.
        self._archive = []
        # Stack-режим: только одна группа видна одновременно, остальные
        # скрыты. Клик по плашке (через activateRequested) переключает
        # активную. По умолчанию Splitter - все группы рядом.
        self._stack_mode = False
        for i, cfg in enumerate(cfgs):
            name = (cfg or {}).get('name') or f'Группа {i + 1}'
            color = (cfg or {}).get('color') or DEFAULT_GROUP_COLORS[
                i % len(DEFAULT_GROUP_COLORS)]
            panel = self._add_panel(name, color)
            # Восстанавливаем collapsed-состояние если сохранено
            if (cfg or {}).get('collapsed'):
                panel.set_collapsed(True)

        # Скрываем все панели начиная со второй пока в них нет табов -
        # как раньше right_panel был hidden по умолчанию.
        for panel in self.panels[1:]:
            if panel.tabs.count() == 0:
                panel.hide()

        self.active_group = self.panels[0].tabs

    # ----- Backward-compat: left_tabs / right_tabs / left_panel / right_panel -----
    @property
    def left_tabs(self):
        return self.panels[0].tabs if self.panels else None

    @property
    def right_tabs(self):
        return self.panels[1].tabs if len(self.panels) >= 2 else None

    @property
    def left_panel(self):
        return self.panels[0] if self.panels else None

    @property
    def right_panel(self):
        return self.panels[1] if len(self.panels) >= 2 else None

    # ----- Управление группами -----

    def iter_groups(self):
        """Итератор по всем активным TabWidget'ам (по одному на группу).
        Заменяет жёсткое (left_tabs, right_tabs) в потребителях."""
        for panel in self.panels:
            yield panel.tabs

    def iter_panels(self):
        return list(self.panels)

    def _add_panel(self, name, color, position=None):
        """Создаёт GroupPanel и регистрирует его в сплиттере + подписывает
        сигналы. Возвращает созданную панель."""
        panel = GroupPanel(name, color)
        idx = position if position is not None else len(self.panels)
        self.panels.insert(idx, panel)
        self.insertWidget(idx, panel)

        panel.tabs.moveTabRequested.connect(self._on_move_tab_requested)
        panel.tabs.tabActivated.connect(self.on_tab_activated)
        panel.tabs.tabDropped.connect(self.check_visibility)
        panel.header.renameRequested.connect(self._on_group_config_changed)
        panel.header.colorRequested.connect(self._on_group_config_changed)
        panel.addGroupRequested.connect(
            lambda p=panel: self._add_group_after(p))
        panel.removeGroupRequested.connect(
            lambda p=panel: self._remove_panel(p))
        panel.collapseToggled.connect(
            lambda p=panel: self._toggle_collapse(p))
        panel.archiveRequested.connect(
            lambda p=panel: self._archive_panel(p))
        panel.filesDropped.connect(
            lambda paths, p=panel: self._on_files_dropped_on_panel(p, paths))
        panel.activateRequested.connect(
            lambda p=panel: self._on_panel_activate_requested(p))
        self._update_remove_enabled()
        return panel

    def _on_files_dropped_on_panel(self, panel, paths):
        """Транслирует drop файлов на плашку наверх с индексом панели."""
        try:
            idx = self.panels.index(panel)
        except ValueError:
            return
        self.filesDroppedOnGroup.emit(idx, list(paths))

    def _on_panel_activate_requested(self, panel):
        """Левый клик по плашке. В Splitter-режиме просто делаем эту группу
        активной (для load_file(side='active') и для текущего viewer'а).
        В Stack-режиме - переключаемся на эту панель."""
        self.active_group = panel.tabs
        # Если включён Stack-режим - показываем только эту панель
        if getattr(self, '_stack_mode', False):
            self._apply_stack_visibility()
        cur = panel.tabs.currentWidget()
        if cur is not None:
            self.activeTabChanged.emit(cur)

    def _toggle_collapse(self, panel):
        """Переключает collapsed-режим панели. Файлы остаются loaded -
        пользователь просто временно прячет табы. Сохраняется в settings
        через groupConfigChanged."""
        panel.set_collapsed(not panel.collapsed)
        self.groupConfigChanged.emit()

    def _archive_panel(self, panel):
        """Архивирует группу: все файлы группы unload'ятся (memory высвобождается),
        конфиг (имя/цвет/список файлов) кладётся в self._archive, панель
        удаляется из UI. Если в архиве остаётся только эта группа в UI - блокируем
        (минимум 1 группа должна остаться видимой; пусть переименует/добавит)."""
        if len(self.panels) <= 1:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Архивировать",
                "Это единственная группа - её нельзя архивировать.\n"
                "Создайте ещё одну группу через «+», тогда эту можно будет архивировать."
            )
            return

        # Собираем пути к файлам
        files = []
        for i in range(panel.tabs.count()):
            w = panel.tabs.widget(i)
            if isinstance(w, LogViewerWidget):
                files.append(w.file_path)

        if files:
            from PyQt6.QtWidgets import QMessageBox
            r = QMessageBox.question(
                self, "Архивировать группу",
                f"Группа «{panel.header.name}»: {len(files)} файл(ов).\n"
                f"Все файлы будут выгружены из памяти. Группа уедет в меню «Архив» -\n"
                f"оттуда её можно вернуть в любой момент.\n\n"
                f"Архивировать?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if r != QMessageBox.StandardButton.Yes:
                return

        # Сохраняем конфиг и закрываем виджеты табов (unload через deleteLater
        # вызовет нашу LogViewerWidget без задержки освободит ресурсы).
        entry = {
            'name': panel.header.name,
            'color': panel.header.color,
            'files': files,
        }
        was_active = (self.active_group is panel.tabs)
        while panel.tabs.count() > 0:
            w = panel.tabs.widget(0)
            panel.tabs.removeTab(0)
            if w is not None:
                # Перед удалением вызываем unload() чтобы корректно остановить
                # потоки и освободить кэши, если у viewer'а они есть.
                if hasattr(w, 'unload'):
                    try:
                        w.unload()
                    except Exception:
                        pass
                w.deleteLater()

        try:
            self.panels.remove(panel)
        except ValueError:
            pass
        panel.setParent(None)
        panel.deleteLater()

        self._archive.append(entry)

        if was_active and self.panels:
            self.active_group = self.panels[0].tabs
            self.activeTabChanged.emit(self.active_group.currentWidget())
        self._update_remove_enabled()
        self.archiveChanged.emit()
        self.groupConfigChanged.emit()

    def get_archive(self):
        """Возвращает копию списка архивированных групп - для отображения
        в меню «Архив» и для сохранения в settings."""
        return [dict(entry) for entry in self._archive]

    def set_archive(self, archive_list):
        """Загружает архив из settings (при запуске приложения)."""
        self._archive = [dict(e) for e in (archive_list or [])]
        self.archiveChanged.emit()

    # ----- Stack / Splitter режим -----

    def set_stack_mode(self, stack):
        """True - режим Stack (видна только active_group), False - Splitter
        (все группы рядом). В Stack-режиме все плашки остаются видимыми (они
        ведь в самих панелях), но видна только активная группа целиком."""
        stack = bool(stack)
        if stack == self._stack_mode:
            return
        self._stack_mode = stack
        self._apply_stack_visibility()

    def _apply_stack_visibility(self):
        """В Stack: скрываем все панели кроме active_group.parent (GroupPanel).
        В Splitter: показываем все, кроме архивированных и тех, что
        check_visibility прятала по причине пустоты (мы их не трогаем)."""
        if self._stack_mode:
            # Находим панель, владеющую active_group
            active_panel = None
            for p in self.panels:
                if p.tabs is self.active_group:
                    active_panel = p
                    break
            if active_panel is None and self.panels:
                active_panel = self.panels[0]
                self.active_group = active_panel.tabs
            for p in self.panels:
                p.setVisible(p is active_panel)
        else:
            # Splitter-режим - показываем все панели (кроме первой остаются как
            # были; первая всегда видна).
            for i, p in enumerate(self.panels):
                if i == 0:
                    p.show()
                else:
                    # Скрываем только пустые - check_visibility-логика
                    if p.tabs.count() > 0:
                        p.show()

    def restore_from_archive(self, index):
        """Восстанавливает группу из архива: создаёт новую панель с её именем,
        цветом и списком файлов (lazy). Возвращает (panel, files) либо None."""
        if not (0 <= index < len(self._archive)):
            return None
        entry = self._archive.pop(index)
        panel = self._add_panel(entry['name'], entry['color'])
        panel.show()
        self._update_remove_enabled()
        self.archiveChanged.emit()
        self.groupConfigChanged.emit()
        return panel, entry.get('files') or []

    def add_group(self, name=None, color=None):
        """Создаёт новую группу в конце. Возвращает GroupPanel."""
        n = len(self.panels) + 1
        if name is None:
            name = f'Группа {n}'
        if color is None:
            color = DEFAULT_GROUP_COLORS[(n - 1) % len(DEFAULT_GROUP_COLORS)]
        panel = self._add_panel(name, color)
        panel.show()
        self._update_remove_enabled()
        self.groupConfigChanged.emit()
        return panel

    def _add_group_after(self, anchor_panel):
        """Создаёт новую группу прямо после anchor_panel (вызывается из
        контекстного меню «Добавить группу справа»)."""
        try:
            idx = self.panels.index(anchor_panel)
        except ValueError:
            return self.add_group()
        n = len(self.panels) + 1
        name = f'Группа {n}'
        color = DEFAULT_GROUP_COLORS[(n - 1) % len(DEFAULT_GROUP_COLORS)]
        panel = self._add_panel(name, color, position=idx + 1)
        panel.show()
        self._update_remove_enabled()
        self.groupConfigChanged.emit()
        return panel

    def _remove_panel(self, panel):
        """Удаляет группу. Если в группе есть файлы - спрашиваем подтверждение."""
        if len(self.panels) <= 1:
            return  # Нельзя удалять последнюю
        from PyQt6.QtWidgets import QMessageBox
        n_files = panel.tabs.count()
        if n_files > 0:
            r = QMessageBox.question(
                self, "Удалить группу",
                f"В группе «{panel.header.name}» открыто {n_files} файл(ов).\n"
                f"Удалить группу и закрыть эти файлы?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        # Закрываем все табы
        while panel.tabs.count() > 0:
            w = panel.tabs.widget(0)
            panel.tabs.removeTab(0)
            if w is not None:
                w.deleteLater()
        # Если активная группа была этой - переключаемся на первую оставшуюся
        was_active = (self.active_group is panel.tabs)
        try:
            self.panels.remove(panel)
        except ValueError:
            pass
        panel.setParent(None)
        panel.deleteLater()
        if was_active and self.panels:
            self.active_group = self.panels[0].tabs
            self.activeTabChanged.emit(self.active_group.currentWidget())
        self._update_remove_enabled()
        self.groupConfigChanged.emit()

    def _update_remove_enabled(self):
        """Disable «Удалить группу» в меню если осталась 1 группа."""
        can_remove = len(self.panels) > 1
        for p in self.panels:
            p.header.set_can_remove(can_remove)

    def _on_move_tab_requested(self):
        """Старый left_tabs.moveTabRequested → move_to_right, и наоборот.
        При произвольном числе групп - перемещаем в следующую справа,
        а если это последняя группа, кидаем в первую."""
        sender = self.sender()
        # Найти панель-источник
        src_idx = None
        for i, p in enumerate(self.panels):
            if p.tabs is sender:
                src_idx = i
                break
        if src_idx is None:
            return
        # Назначение: следующая справа, иначе - предыдущая слева
        if src_idx + 1 < len(self.panels):
            dst_idx = src_idx + 1
        elif src_idx > 0:
            dst_idx = src_idx - 1
        else:
            return  # одна группа всего - двигать некуда
        self._move_current_tab(src_idx, dst_idx)

    def _move_current_tab(self, src_idx, dst_idx):
        src = self.panels[src_idx].tabs
        dst = self.panels[dst_idx].tabs
        idx = src.currentIndex()
        if idx < 0:
            return
        widget = src.widget(idx)
        text = src.tabText(idx)
        src.removeTab(idx)
        dst.addTab(widget, text)
        dst.setCurrentWidget(widget)
        # Показываем целевую панель если была скрыта
        dst_panel = self.panels[dst_idx]
        if not dst_panel.isVisible():
            dst_panel.show()
        self.check_visibility()

    def _on_group_config_changed(self, *_args):
        self.groupConfigChanged.emit()

    def get_group_configs(self):
        """Возвращает [{name, color, collapsed}, ...] - для сохранения в settings."""
        return [
            {
                'name': p.header.name,
                'color': p.header.color,
                'collapsed': p.collapsed,
            }
            for p in self.panels
        ]

    def check_visibility(self):
        # Скрываем целиком панель (header + tabs) если в ней не осталось
        # табов. Первую (panels[0]) не трогаем - она всегда видна, даже
        # если пуста, иначе пользователь потеряет точку для drop'а.
        for i, panel in enumerate(self.panels):
            if i == 0:
                continue
            if panel.tabs.count() == 0:
                panel.hide()

        if self.active_group is not None and self.active_group.count() == 0:
            # Ищем другую панель с табами
            other = None
            for p in self.panels:
                if p.tabs is not self.active_group and p.tabs.count() > 0 and p.isVisible():
                    other = p.tabs
                    break
            if other is not None:
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
            target = self.right_tabs or self.left_tabs
        elif (self.right_panel is None) or (not self.right_panel.isVisible()):
            # Если правая панель ещё не существует / скрыта - кладём в первую.
            target = self.left_tabs

        if target is None:
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
        owner_panel = next(
            (p for p in self.panels if p.tabs is target), None)
        if owner_panel is not None and not owner_panel.isVisible():
            owner_panel.show()

        target.setFocus()
        self.active_group = target
        if not silent:
            self.activeTabChanged.emit(widget)

    def get_open_files_per_group(self):
        """Возвращает список списков путей файлов - по одному списку на
        группу, в порядке self.panels."""
        per_group = []
        for panel in self.panels:
            files = []
            for i in range(panel.tabs.count()):
                w = panel.tabs.widget(i)
                if isinstance(w, LogViewerWidget):
                    files.append(w.file_path)
            per_group.append(files)
        return per_group

    def get_open_files(self):
        """Legacy: возвращает (files_left, files_right) - только первые две
        группы. Для произвольного числа групп используй get_open_files_per_group()."""
        per_group = self.get_open_files_per_group()
        files_left = per_group[0] if len(per_group) >= 1 else []
        files_right = per_group[1] if len(per_group) >= 2 else []
        return files_left, files_right

    # Legacy-обёртки (используются внешним кодом / тестами). Делегируют на
    # новый _move_current_tab, который работает с произвольным числом групп.
    def move_to_right(self, index):
        if len(self.panels) < 2:
            return
        self._move_tab(index, self.left_tabs, self.right_tabs)

    def move_to_left(self, index):
        if len(self.panels) < 2:
            return
        self._move_tab(index, self.right_tabs, self.left_tabs)

    def _move_tab(self, index, source, target):
        widget = source.widget(index)
        text = source.tabText(index)
        source.removeTab(index)

        new_index = target.addTab(widget, text)
        target.setCurrentIndex(new_index)

        # Скрываем целую панель источника если последний таб ушёл -
        # но только для НЕпервой группы (первая всегда видна).
        if source.count() == 0 and source is not self.left_tabs:
            src_panel = next(
                (p for p in self.panels if p.tabs is source), None)
            if src_panel is not None:
                src_panel.hide()

        # Показываем панель назначения если была скрыта
        dst_panel = next(
            (p for p in self.panels if p.tabs is target), None)
        if dst_panel is not None and not dst_panel.isVisible():
            dst_panel.show()
            # Перераспределяем ширину равномерно по всем видимым панелям
            sizes = []
            total = self.width()
            visible = [p for p in self.panels if p.isVisible()]
            if visible:
                per = total // len(visible)
                for p in self.panels:
                    sizes.append(per if p.isVisible() else 0)
                self.setSizes(sizes)

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
