import os
import sys
import ctypes
import subprocess
from PyQt6.QtWidgets import (QTabWidget, QSplitter, QStackedWidget, QWidget,
                             QVBoxLayout, QHBoxLayout, QMenu,
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
    archiveRequested = pyqtSignal()
    filesDropped = pyqtSignal(list)  # drop'нуты пути файлов прямо на плашку
    activateRequested = pyqtSignal()  # клик по плашке - сделать группу активной

    def __init__(self, name, color, parent=None):
        super().__init__(parent)
        self._name = name
        self._color = color
        self._can_remove = True
        self._active = False  # подсветка активной плашки в bar'е
        self._drag_hover = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Принимаем drop файлов прямо на плашку - так юзер может бросить
        # .log в конкретную группу, не в активную по умолчанию.
        self.setAcceptDrops(True)

        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 4, 4)
        h.setSpacing(4)

        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet("color: white; font-weight: bold;")
        h.addWidget(self.lbl_name)
        h.addStretch()

        # Кнопка меню "⋮" - содержит все операции с группой
        self.btn_menu = QToolButton()
        self.btn_menu.setText("⋮")
        self.btn_menu.setFixedSize(22, 22)
        self.btn_menu.clicked.connect(self._show_menu_at_button)
        h.addWidget(self.btn_menu)

        self._apply_color()

    def _apply_color(self):
        """Применяет цвет фона. Текст автоматически белый/чёрный по яркости.
        Активная плашка - полная заливка цветом. Неактивная - выглядит
        приглушённее (полупрозрачный цвет). Во время drag-hover жёлтая рамка."""
        c = QColor(self._color)
        lum = (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000
        text_color = '#000000' if lum > 160 else '#FFFFFF'
        if self._drag_hover:
            border = "border: 2px solid #FFD600;"
        elif self._active:
            border = "border-bottom: 2px solid white;"
        else:
            border = "border: none;"

        # Активная плашка - полная заливка. Неактивная - чуть приглушённая,
        # чтобы было видно где сейчас фокус.
        if self._active:
            bg = self._color
            text_alpha = text_color
        else:
            # Mute через alpha: rgba от исходного цвета с прозрачностью
            bg = f"rgba({c.red()},{c.green()},{c.blue()},140)"
            text_alpha = text_color

        self.setStyleSheet(
            f"GroupHeader {{ background-color: {bg}; "
            f"border-top-left-radius: 4px; border-top-right-radius: 4px; "
            f"{border} }}"
        )
        self.lbl_name.setStyleSheet(
            f"color: {text_alpha}; font-weight: {'bold' if self._active else 'normal'}; "
            "background: transparent;"
        )
        hover_bg = "rgba(0,0,0,40)" if lum > 160 else "rgba(255,255,255,40)"
        btn_css = (
            f"QToolButton {{ color: {text_alpha}; background: transparent; "
            "border: none; font-size: 16px; font-weight: bold; }}"
            f"QToolButton:hover {{ background: {hover_bg}; border-radius: 3px; }}"
        )
        self.btn_menu.setStyleSheet(btn_css)

    def set_can_remove(self, can_remove):
        self._can_remove = bool(can_remove)

    def set_active(self, active):
        """Помечает плашку как активную (текущая группа в стэке)."""
        if active == self._active:
            return
        self._active = bool(active)
        self._apply_color()

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
        act_archive = menu.addAction("Архивировать (выгрузить файлы)")
        menu.addSeparator()
        act_add = menu.addAction("Добавить группу")
        act_remove = menu.addAction("Удалить группу")
        act_remove.setEnabled(self._can_remove)
        act_rename.triggered.connect(self._do_rename)
        act_color.triggered.connect(self._do_change_color)
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
    """Тонкая обёртка над EditorTabWidget. Раньше держала свой собственный
    header сверху, но в Stack-режиме header переехал в общий bar плашек в
    SplitManager - здесь остался только сам tabs. Сохраняем класс ради
    обратной совместимости и единого места для будущих расширений."""

    filesDropped = pyqtSignal(list)
    activateRequested = pyqtSignal()

    def __init__(self, name, color, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.tabs = EditorTabWidget()
        v.addWidget(self.tabs, 1)
        # Сохраняем имя/цвет здесь же - они актуальны только при создании;
        # дальше синхронизируются через GroupHeader в bar'е SplitManager'а.
        self._group_name = name
        self._group_color = color


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


# --- Bar плашек групп: горизонтальный контейнер с GroupChip'ами и кнопкой «+» ---

class _GroupsBar(QFrame):
    """Узкий бар сверху над содержимым: ряд плашек групп (GroupHeader),
    кнопка «+» в конце. Не управляет логикой - только держит виджеты."""

    addRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 4, 4, 0)
        h.setSpacing(2)
        # Контейнер для плашек (slot для динамического добавления/удаления)
        self._chips_layout = QHBoxLayout()
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(2)
        h.addLayout(self._chips_layout)

        # Кнопка «+» в конце - создать новую группу
        self.btn_add = QToolButton()
        self.btn_add.setText("+")
        self.btn_add.setFixedSize(28, 28)
        self.btn_add.setToolTip("Создать новую группу")
        self.btn_add.setStyleSheet(
            "QToolButton { color: white; background: rgba(255,255,255,30); "
            "border: none; border-radius: 4px; font-size: 16px; font-weight: bold; }"
            "QToolButton:hover { background: rgba(255,255,255,60); }"
        )
        self.btn_add.clicked.connect(self.addRequested.emit)
        h.addWidget(self.btn_add)
        h.addStretch()

    def add_chip(self, chip):
        # Вставляем перед растягивающим stretch
        self._chips_layout.addWidget(chip)

    def remove_chip(self, chip):
        self._chips_layout.removeWidget(chip)
        chip.setParent(None)
        chip.deleteLater()


class SplitManager(QWidget):
    """Контейнер групп табов в стиле «tabs-in-tabs»: сверху строка плашек
    (одна на группу + кнопка «+»), снизу QStackedWidget с EditorTabWidget'ом
    активной группы. Видна всегда только одна группа - переключение между
    ними кликом по плашке (как переключение папок).

    Атрибуты для backward-compat: left_tabs / right_tabs / left_panel /
    right_panel указывают на первые две группы (или None если их меньше).

    Сигналы:
      activeTabChanged(widget) - сменился активный таб;
      groupConfigChanged()     - имена/цвета/состав групп изменились;
      archiveChanged()         - архив изменился;
      filesDroppedOnGroup(idx, paths) - drop файлов на плашку конкретной группы."""

    activeTabChanged = pyqtSignal(object)
    groupConfigChanged = pyqtSignal()
    archiveChanged = pyqtSignal()
    filesDroppedOnGroup = pyqtSignal(int, list)

    def __init__(self, parent=None, group_configs=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Сверху - строка плашек
        self._bar = _GroupsBar()
        self._bar.addRequested.connect(self.add_group)
        v.addWidget(self._bar)

        # Снизу - стэк с TabWidget'ами групп (виден один за раз)
        self._stack = QStackedWidget()
        v.addWidget(self._stack, 1)

        # Каждая группа представлена парой (chip, panel) - храним их в одном
        # списке в порядке отображения. panel - GroupPanel с tabs.
        self._groups = []  # list of dict: {'chip': GroupHeader, 'panel': GroupPanel}
        self._archive = []  # архивированные группы (без UI), см. set_archive()

        cfgs = list(group_configs or [])
        if len(cfgs) < 1:
            cfgs = [
                {'name': 'Группа 1', 'color': DEFAULT_GROUP_COLORS[0]},
                {'name': 'Группа 2', 'color': DEFAULT_GROUP_COLORS[1]},
            ]
        for i, cfg in enumerate(cfgs):
            name = (cfg or {}).get('name') or f'Группа {i + 1}'
            color = (cfg or {}).get('color') or DEFAULT_GROUP_COLORS[
                i % len(DEFAULT_GROUP_COLORS)]
            self._add_group_internal(name, color)

        # Активируем первую группу
        if self._groups:
            self._set_active_group(0, emit_signal=False)

    # ----- Backward-compat: left_tabs / right_tabs / left_panel / right_panel -----

    @property
    def panels(self):
        return [g['panel'] for g in self._groups]

    @property
    def left_tabs(self):
        return self._groups[0]['panel'].tabs if self._groups else None

    @property
    def right_tabs(self):
        return self._groups[1]['panel'].tabs if len(self._groups) >= 2 else None

    @property
    def left_panel(self):
        return self._groups[0]['panel'] if self._groups else None

    @property
    def right_panel(self):
        return self._groups[1]['panel'] if len(self._groups) >= 2 else None

    @property
    def active_group(self):
        """tabs текущей активной группы (для legacy кода)."""
        idx = self._stack.currentIndex()
        if 0 <= idx < len(self._groups):
            return self._groups[idx]['panel'].tabs
        return None

    @active_group.setter
    def active_group(self, tabs):
        """Совместимый сеттер - находит группу по tabs и активирует."""
        for i, g in enumerate(self._groups):
            if g['panel'].tabs is tabs:
                self._set_active_group(i)
                return

    def iter_groups(self):
        for g in self._groups:
            yield g['panel'].tabs

    def iter_panels(self):
        return [g['panel'] for g in self._groups]

    # ----- Управление группами -----

    def _add_group_internal(self, name, color):
        """Создаёт пару (chip, panel) и регистрирует в bar+stack."""
        chip = GroupHeader(name, color)
        panel = GroupPanel(name, color)

        # Подписываемся на сигналы chip'а
        chip.renameRequested.connect(
            lambda new_name, p=panel: self._on_renamed(p, new_name))
        chip.colorRequested.connect(
            lambda new_color, p=panel: self._on_recolored(p, new_color))
        chip.addGroupRequested.connect(self.add_group)
        chip.removeGroupRequested.connect(
            lambda p=panel: self._remove_panel(p))
        chip.archiveRequested.connect(
            lambda p=panel: self._archive_panel(p))
        chip.activateRequested.connect(
            lambda p=panel: self._on_chip_clicked(p))
        chip.filesDropped.connect(
            lambda paths, p=panel: self._on_files_dropped_on_panel(p, paths))

        # И от tabs - tabActivated сигнализирует что юзер кликнул внутри
        panel.tabs.tabActivated.connect(self._on_tab_activated_in_panel)
        panel.tabs.tabDropped.connect(self.check_visibility)

        self._bar.add_chip(chip)
        self._stack.addWidget(panel)
        self._groups.append({'chip': chip, 'panel': panel})
        self._update_remove_enabled()
        return panel

    def add_group(self, name=None, color=None):
        """Создаёт новую группу и сразу делает её активной."""
        n = len(self._groups) + 1
        if name is None:
            name = f'Группа {n}'
        if color is None:
            color = DEFAULT_GROUP_COLORS[(n - 1) % len(DEFAULT_GROUP_COLORS)]
        panel = self._add_group_internal(name, color)
        # Активируем новую группу
        idx = self._stack.indexOf(panel)
        self._set_active_group(idx)
        self.groupConfigChanged.emit()
        return panel

    def _set_active_group(self, index, emit_signal=True):
        """Делает группу с указанным индексом активной: переключает стэк
        и подсветку плашек."""
        if not (0 <= index < len(self._groups)):
            return
        self._stack.setCurrentIndex(index)
        for i, g in enumerate(self._groups):
            g['chip'].set_active(i == index)
        if emit_signal:
            tabs = self._groups[index]['panel'].tabs
            cur = tabs.currentWidget()
            if cur is not None:
                self.activeTabChanged.emit(cur)
            else:
                self.activeTabChanged.emit(None)

    def _on_chip_clicked(self, panel):
        idx = self._stack.indexOf(panel)
        if idx >= 0:
            self._set_active_group(idx)

    def _on_tab_activated_in_panel(self, widget):
        """Юзер кликнул по табу внутри панели - найдём её и активируем."""
        sender = self.sender()
        for i, g in enumerate(self._groups):
            if g['panel'].tabs is sender:
                # Не вызываем _set_active_group целиком - только эмит сигнала
                # о смене активного таба, потому что группа уже была активной
                # (Stack показывает её, табы внутри переключают друг друга).
                if self._stack.currentIndex() != i:
                    self._set_active_group(i, emit_signal=False)
                if widget is not None:
                    self.activeTabChanged.emit(widget)
                else:
                    self.activeTabChanged.emit(None)
                return

    def _on_renamed(self, panel, new_name):
        panel._group_name = new_name
        self.groupConfigChanged.emit()

    def _on_recolored(self, panel, new_color):
        panel._group_color = new_color
        self.groupConfigChanged.emit()

    def _remove_panel(self, panel):
        """Удаляет группу. Если в ней есть файлы - подтверждение."""
        if len(self._groups) <= 1:
            return
        from PyQt6.QtWidgets import QMessageBox
        n_files = panel.tabs.count()
        if n_files > 0:
            r = QMessageBox.question(
                self, "Удалить группу",
                f"В группе открыто {n_files} файл(ов).\n"
                f"Удалить группу и закрыть эти файлы?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        # Найти группу
        group_idx = None
        for i, g in enumerate(self._groups):
            if g['panel'] is panel:
                group_idx = i
                break
        if group_idx is None:
            return
        group = self._groups[group_idx]
        # Закрываем все табы и удаляем виджеты
        while panel.tabs.count() > 0:
            w = panel.tabs.widget(0)
            panel.tabs.removeTab(0)
            if w is not None:
                if hasattr(w, 'unload'):
                    try:
                        w.unload()
                    except Exception:
                        pass
                w.deleteLater()
        # Убираем chip из bar'а и panel из стэка
        self._bar.remove_chip(group['chip'])
        self._stack.removeWidget(panel)
        panel.deleteLater()
        self._groups.pop(group_idx)
        # Переключаем активную группу
        new_active = min(group_idx, len(self._groups) - 1)
        if new_active >= 0:
            self._set_active_group(new_active)
        self._update_remove_enabled()
        self.groupConfigChanged.emit()

    def _archive_panel(self, panel):
        """Архивирует группу: unload файлов + конфиг в self._archive."""
        if len(self._groups) <= 1:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Архивировать",
                "Это единственная группа - её нельзя архивировать.\n"
                "Создайте ещё одну через «+», тогда эту можно будет архивировать."
            )
            return
        # Находим группу
        group_idx = None
        for i, g in enumerate(self._groups):
            if g['panel'] is panel:
                group_idx = i
                break
        if group_idx is None:
            return
        group = self._groups[group_idx]

        files = []
        for i in range(panel.tabs.count()):
            w = panel.tabs.widget(i)
            if isinstance(w, LogViewerWidget):
                files.append(w.file_path)

        if files:
            from PyQt6.QtWidgets import QMessageBox
            r = QMessageBox.question(
                self, "Архивировать группу",
                f"Группа «{group['chip'].name}»: {len(files)} файл(ов).\n"
                f"Все файлы будут выгружены из памяти. Группа уедет в меню «Архив» -\n"
                f"оттуда её можно вернуть в любой момент.\n\nАрхивировать?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if r != QMessageBox.StandardButton.Yes:
                return

        entry = {
            'name': group['chip'].name,
            'color': group['chip'].color,
            'files': files,
        }
        # Закрываем все виджеты табов
        while panel.tabs.count() > 0:
            w = panel.tabs.widget(0)
            panel.tabs.removeTab(0)
            if w is not None:
                if hasattr(w, 'unload'):
                    try:
                        w.unload()
                    except Exception:
                        pass
                w.deleteLater()
        # Убираем из UI
        self._bar.remove_chip(group['chip'])
        self._stack.removeWidget(panel)
        panel.deleteLater()
        self._groups.pop(group_idx)
        # Активируем следующую
        new_active = min(group_idx, len(self._groups) - 1)
        if new_active >= 0:
            self._set_active_group(new_active)
        self._update_remove_enabled()
        self._archive.append(entry)
        self.archiveChanged.emit()
        self.groupConfigChanged.emit()

    def _update_remove_enabled(self):
        can_remove = len(self._groups) > 1
        for g in self._groups:
            g['chip'].set_can_remove(can_remove)

    def _on_files_dropped_on_panel(self, panel, paths):
        idx = self._stack.indexOf(panel)
        if idx >= 0:
            self.filesDroppedOnGroup.emit(idx, list(paths))

    # ----- Archive -----

    def get_archive(self):
        return [dict(entry) for entry in self._archive]

    def set_archive(self, archive_list):
        self._archive = [dict(e) for e in (archive_list or [])]
        self.archiveChanged.emit()

    def restore_from_archive(self, index):
        if not (0 <= index < len(self._archive)):
            return None
        entry = self._archive.pop(index)
        panel = self._add_group_internal(entry['name'], entry['color'])
        idx = self._stack.indexOf(panel)
        self._set_active_group(idx)
        self.archiveChanged.emit()
        self.groupConfigChanged.emit()
        return panel, entry.get('files') or []

    # ----- Конфигурация для save/restore -----

    def get_group_configs(self):
        return [
            {'name': g['chip'].name, 'color': g['chip'].color}
            for g in self._groups
        ]

    def get_open_files_per_group(self):
        result = []
        for g in self._groups:
            files = []
            tabs = g['panel'].tabs
            for i in range(tabs.count()):
                w = tabs.widget(i)
                if isinstance(w, LogViewerWidget):
                    files.append(w.file_path)
            result.append(files)
        return result

    def get_open_files(self):
        per = self.get_open_files_per_group()
        files_left = per[0] if len(per) >= 1 else []
        files_right = per[1] if len(per) >= 2 else []
        return files_left, files_right

    # ----- check_visibility / add_tab -----

    def check_visibility(self):
        """В Stack-режиме нечего скрывать - один stack виджет всегда виден.
        Метод оставлен для совместимости со старыми сигналами tabDropped."""
        pass

    def add_tab(self, widget, title, side="active", silent=False):
        """Добавляет таб в активную группу (или в первую/вторую по side="left"/
        "right" для legacy кода). silent=True - не эмитим activeTabChanged
        (для restore_session, чтобы все табы оставались lazy)."""
        idx = self._stack.currentIndex()
        if side == "left":
            idx = 0
        elif side == "right":
            idx = 1 if len(self._groups) >= 2 else 0
        if not (0 <= idx < len(self._groups)):
            idx = 0
        target = self._groups[idx]['panel'].tabs

        if silent:
            target.blockSignals(True)
        try:
            tab_idx = target.addTab(widget, title)
            target.setCurrentIndex(tab_idx)
        finally:
            if silent:
                target.blockSignals(False)

        target.setFocus()
        # Активируем эту группу если она не была активной
        if self._stack.currentIndex() != idx:
            self._set_active_group(idx, emit_signal=False)
        if not silent:
            self.activeTabChanged.emit(widget)

    # ----- Stack/Splitter переключатель (заглушка - только Stack пока) -----

    def set_stack_mode(self, stack):
        """Stack-режим теперь единственный. Метод оставлен для совместимости
        с настройками - просто ничего не делает."""
        pass

    def get_current_viewer(self):
        tabs = self.active_group
        if tabs is None or tabs.count() == 0:
            return None
        w = tabs.currentWidget()
        if isinstance(w, LogViewerWidget):
            return w
        return None

