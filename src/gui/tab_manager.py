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
    hideRequested = pyqtSignal()  # скрыть группу (chip уходит, файлы в RAM сохраняются)
    filesDropped = pyqtSignal(list)  # drop'нуты пути файлов прямо на плашку
    activateRequested = pyqtSignal()  # клик по плашке - сделать группу активной
    # Drop таба из другой группы (через DraggableTabBar): source - EditorTabWidget,
    # tab_index - индекс в нём. SplitManager перенесёт widget в свою группу.
    tabDroppedFromOther = pyqtSignal(object, int)
    # Drop ДРУГОЙ плашки (reorder групп) - источник по object id'у self
    groupDroppedHere = pyqtSignal(object)

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

    # ----- Drop файлов / табов на плашку -----

    def dragEnterEvent(self, event):
        md = event.mimeData()
        # Drop другой плашки (reorder) - принимаем, но не от самой себя
        if md.hasFormat("application/x-loganalyzer-group"):
            if GroupHeader._drag_source_chip is not self:
                self._drag_hover = True
                self._apply_color()
                event.acceptProposedAction()
                return
        # Drop таба из другой группы - принимаем
        if md.hasFormat("application/x-loganalyzer-tab"):
            self._drag_hover = True
            self._apply_color()
            event.acceptProposedAction()
            return
        # Drop файлов с диска - принимаем
        if md.hasUrls():
            urls = md.urls()
            if any(u.isLocalFile() for u in urls):
                self._drag_hover = True
                self._apply_color()
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event):
        md = event.mimeData()
        if (md.hasFormat("application/x-loganalyzer-tab")
                or md.hasFormat("application/x-loganalyzer-group")
                or (md.hasUrls() and any(u.isLocalFile() for u in md.urls()))):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._drag_hover = False
        self._apply_color()
        event.accept()

    def dropEvent(self, event):
        self._drag_hover = False
        self._apply_color()
        md = event.mimeData()
        # 0) Drop другой плашки - reorder групп
        if md.hasFormat("application/x-loganalyzer-group"):
            source = GroupHeader._drag_source_chip
            if source is None or source is self:
                event.ignore()
                return
            self.groupDroppedHere.emit(source)
            event.acceptProposedAction()
            return
        # 1) Drop таба из другой группы. DraggableTabBar кладёт индекс таба
        # в mime.text(), а сам source widget сохраняет в EditorTabWidget._drag_source.
        if md.hasFormat("application/x-loganalyzer-tab"):
            source = EditorTabWidget._drag_source
            if source is None:
                event.ignore()
                return
            try:
                idx = int(md.text())
            except (ValueError, TypeError):
                event.ignore()
                return
            self.tabDroppedFromOther.emit(source, idx)
            event.acceptProposedAction()
            return
        # 2) Drop файлов
        if md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if paths:
                self.filesDropped.emit(paths)
                event.acceptProposedAction()
                return
        event.ignore()

    def mousePressEvent(self, event):
        # Левый клик по плашке - просим SplitManager сделать эту группу активной
        # (полезно в Stack-режиме, где это переключает видимый контент).
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self.activateRequested.emit()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Начало QDrag для перестановки группы. Должно сработать только если
        # юзер удерживает ЛКМ и сместил курсор достаточно далеко.
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if not getattr(self, '_drag_start_pos', None):
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return
        # Сохраняем источник для drop-target'а на уровне класса (similar to
        # EditorTabWidget._drag_source).
        GroupHeader._drag_source_chip = self

        drag = QDrag(self)
        md = QMimeData()
        md.setData("application/x-loganalyzer-group", b"chip")
        md.setText(self._name)
        drag.setMimeData(md)
        # Pixmap самой плашки в качестве drag-картинки
        drag.setPixmap(self.grab(self.rect()))
        drag.setHotSpot(event.pos() - self.rect().topLeft())
        drag.exec(Qt.DropAction.MoveAction)
        GroupHeader._drag_source_chip = None
        super().mouseMoveEvent(event)

    # На уровне класса: используется как глобальная переменная во время drag.
    _drag_source_chip = None

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
        act_hide = menu.addAction("Скрыть из bar'а")
        act_hide.setToolTip("Chip пропадёт из строки. Вернуть через кнопку «🗂 Группы».")
        menu.addSeparator()
        act_add = menu.addAction("Добавить группу")
        act_remove = menu.addAction("Удалить группу")
        act_remove.setEnabled(self._can_remove)
        act_rename.triggered.connect(self._do_rename)
        act_color.triggered.connect(self._do_change_color)
        act_hide.triggered.connect(self.hideRequested.emit)
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
    """Группа табов с возможностью разделения экрана ВНУТРИ группы (как
    Split editor в VS Code / IntelliJ). Содержит QSplitter с одним или
    несколькими EditorTabWidget (panes); каждая pane показывает свой
    набор табов одновременно с остальными.

    .tabs — алиас на активную pane (та куда последний раз кликали или
    куда добавляли таб). Нужен ради backward-compat кода, который привык
    что в группе ровно один EditorTabWidget.

    Сигналы:
      paneAdded(EditorTabWidget) — создана новая pane через add_pane(),
        SplitManager слушает чтобы подключить tabActivated / moveTabRequested
        / tabDropped новой pane к своим слотам.
      paneRemoved(EditorTabWidget) — pane удалена (закрылся последний
        таб; единственная pane не удаляется)."""

    filesDropped = pyqtSignal(list)
    activateRequested = pyqtSignal()
    paneAdded = pyqtSignal(object)
    paneRemoved = pyqtSignal(object)

    def __init__(self, name, color, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        # Splitter ровно для panes внутри группы. По умолчанию одна pane —
        # выглядит как обычный TabWidget (handle сплиттера не отображается
        # пока pane одна).
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        v.addWidget(self._splitter, 1)
        # Все panes в порядке отображения. self.tabs всегда указывает на
        # одну из них.
        self._panes = []
        self.tabs = self._create_pane()
        # Сохраняем имя/цвет здесь же - они актуальны только при создании;
        # дальше синхронизируются через GroupHeader в bar'е SplitManager'а.
        self._group_name = name
        self._group_color = color

    def _create_pane(self):
        """Внутренний хелпер: создаёт EditorTabWidget, добавляет в splitter
        и в список panes. Сигнал paneAdded НЕ эмитим (для самой первой
        pane никто не подписан; для последующих используем add_pane)."""
        pane = EditorTabWidget()
        self._splitter.addWidget(pane)
        self._panes.append(pane)
        return pane

    def add_pane(self):
        """Создаёт новую pane справа от существующих. Эмитит paneAdded,
        чтобы SplitManager подключил сигналы. Возвращает новую pane."""
        pane = self._create_pane()
        # Делим пространство равномерно — иначе новая pane получит почти 0px
        n = len(self._panes)
        total = self._splitter.size().width() or self.size().width() or 800
        if total > 0:
            self._splitter.setSizes([max(1, total // n)] * n)
        self.paneAdded.emit(pane)
        return pane

    def remove_pane(self, pane):
        """Удаляет указанную pane, если она пустая и не единственная.
        Если pane — текущая self.tabs, переключаем на соседнюю.
        Возвращает True если удалили."""
        if pane not in self._panes:
            return False
        if len(self._panes) <= 1:
            return False
        if pane.count() > 0:
            return False
        # Переключаем .tabs если это была активная
        if self.tabs is pane:
            other = next((p for p in self._panes if p is not pane), None)
            if other is not None:
                self.tabs = other
        self._panes.remove(pane)
        pane.setParent(None)
        pane.deleteLater()
        self.paneRemoved.emit(pane)
        return True

    def panes(self):
        """Возвращает копию списка всех panes (для итерации в SplitManager
        / save_settings / iter_groups)."""
        return list(self._panes)

    def has_pane(self, w):
        return w in self._panes

    def set_active_pane(self, pane):
        """Делает указанную pane активной (self.tabs). Используется когда
        юзер кликает таб внутри pane или после move_tab_to_new_pane."""
        if pane in self._panes:
            self.tabs = pane


class DraggableTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.drag_start_pos = None
        self.selected_indices = set()
        self.last_clicked_index = -1

    def wheelEvent(self, event):
        """Колесо мыши над таб-баром листает видимую часть (когда табов больше
        чем влезает в строку), но НЕ меняет активный таб. Это привычное
        поведение в браузерах. Qt по умолчанию при wheel ещё и сменяет
        current - перехватываем: запоминаем current до super(), и если он
        сменился - возвращаем обратно (signals блокируем, чтобы не дёргать
        activeTabChanged зря)."""
        prev = self.currentIndex()
        super().wheelEvent(event)
        if self.currentIndex() != prev:
            self.blockSignals(True)
            try:
                self.setCurrentIndex(prev)
            finally:
                self.blockSignals(False)

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
    # Эмитится когда после очередного removeTab() осталось 0 табов. Нужен
    # GroupPanel'у чтобы удалить пустую сплит-pane (если она не единственная).
    lastTabClosed = pyqtSignal()

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

    def tabRemoved(self, index):
        """Qt-callback: вызывается после каждого removeTab. Используем чтобы
        уведомить GroupPanel о пустой сплит-pane (он удалит её, если она
        не единственная)."""
        super().tabRemoved(index)
        if self.count() == 0:
            self.lastTabClosed.emit()

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
      filesDroppedOnGroup(idx, paths) - drop файлов на плашку конкретной группы."""

    activeTabChanged = pyqtSignal(object)
    groupConfigChanged = pyqtSignal()
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

        # Снизу - переключатель layout'а: stack (одна группа видна) или
        # splitter (несколько групп бок о бок).
        self._mode_switcher = QStackedWidget()
        self._stack = QStackedWidget()  # page 0: stack-mode контейнер
        self._splitter = QSplitter(Qt.Orientation.Horizontal)  # page 1: splitter-mode
        self._mode_switcher.addWidget(self._stack)
        self._mode_switcher.addWidget(self._splitter)
        v.addWidget(self._mode_switcher, 1)

        # 'stack' или 'splitter' - какой layout сейчас активен
        self._layout_mode = 'stack'
        # Активная группа (для add_tab/save_settings). В stack совпадает с
        # _stack.currentIndex, в splitter хранится отдельно, потому что все
        # панели видны одновременно.
        self._active_index = 0

        # Каждая группа представлена парой (chip, panel) - храним их в одном
        # списке в порядке отображения. panel - GroupPanel с tabs.
        self._groups = []  # list of dict: {'chip': GroupHeader, 'panel': GroupPanel}

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
            hidden = bool((cfg or {}).get('hidden', False))
            self._add_group_internal(name, color, hidden=hidden)

        # Активируем первую НЕскрытую группу
        for i, g in enumerate(self._groups):
            if not getattr(g['panel'], '_hidden', False):
                self._set_active_group(i, emit_signal=False)
                break

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
        idx = self._active_index
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
        """Итерирует ВСЕ EditorTabWidget'ы во всех группах. С появлением
        intra-group split в группе может быть несколько panes - возвращаем
        каждую отдельно, чтобы вызывающий код (LRU, save, font update и т.д.)
        проходил по всем виджетам, а не только по первой pane."""
        for g in self._groups:
            for pane in g['panel'].panes():
                yield pane

    def iter_panels(self):
        return [g['panel'] for g in self._groups]

    def get_active_group_index(self):
        """Индекс активной группы (для save_settings)."""
        return self._active_index

    @property
    def _container(self):
        """Текущий контейнер для GroupPanel'ей: QStackedWidget или QSplitter
        в зависимости от _layout_mode. Все методы добавления/удаления панелей
        работают через него абстрактно."""
        return self._stack if self._layout_mode == 'stack' else self._splitter

    def activate_group_index(self, index, emit_signal=True):
        """Публичный обёрток над _set_active_group: активировать группу по
        индексу. Используется в restore_session, чтобы один раз в конце
        активировать сохранённую группу с эмитом activeTabChanged."""
        self._set_active_group(index, emit_signal=emit_signal)

    # ----- Управление группами -----

    def _connect_pane_signals(self, pane, panel):
        """Подключает tabActivated / tabDropped / moveTabRequested любой pane
        (изначальной или созданной через GroupPanel.add_pane) к слотам
        SplitManager'а. Без этого новые panes были бы 'мертвы' — клик на
        табе не активировал бы группу, drag&drop не работал бы."""
        pane.tabActivated.connect(self._on_tab_activated_in_panel)
        pane.tabDropped.connect(self.check_visibility)
        # «Переместить в другую панель» теперь делает split ВНУТРИ группы
        # (вместо межгруппового splitter mode из предыдущей версии).
        pane.moveTabRequested.connect(
            lambda idx, p=panel, src=pane: self.move_tab_to_new_pane(p, src, idx))
        # Когда пользователь закрывает последний таб в pane (через крестик
        # или Закрыть все), удаляем pane, если она не единственная.
        pane.lastTabClosed.connect(
            lambda p=panel, src=pane: p.remove_pane(src))

    def _add_group_internal(self, name, color, hidden=False):
        """Создаёт пару (chip, panel) и регистрирует в bar+stack.
        hidden=True - chip создаётся, но не показывается в bar'е (юзер вернёт
        его через диалог управления). Stack-страница тоже скрывается."""
        chip = GroupHeader(name, color)
        panel = GroupPanel(name, color)
        panel._hidden = bool(hidden)
        # В splitter-режиме: панель может быть «свёрнутой» — chip остаётся
        # в баре (клик возвращает), но в QSplitter саму панель не показываем.
        # По умолчанию все группы видны в splitter; move_tab_to_other_panel
        # схлопывает все КРОМЕ source/target, чтобы юзер не получал 4 столбца
        # вместо ожидаемых 2.
        panel._collapsed_in_splitter = False

        # Подписываемся на сигналы chip'а
        chip.renameRequested.connect(
            lambda new_name, p=panel: self._on_renamed(p, new_name))
        chip.colorRequested.connect(
            lambda new_color, p=panel: self._on_recolored(p, new_color))
        chip.addGroupRequested.connect(self.add_group)
        chip.removeGroupRequested.connect(
            lambda p=panel: self._remove_panel(p))
        chip.hideRequested.connect(
            lambda p=panel: self.set_group_hidden(p, True))
        chip.activateRequested.connect(
            lambda p=panel: self._on_chip_clicked(p))
        chip.filesDropped.connect(
            lambda paths, p=panel: self._on_files_dropped_on_panel(p, paths))
        chip.tabDroppedFromOther.connect(
            lambda src, idx, p=panel: self._on_tab_dropped_on_panel(p, src, idx))
        chip.groupDroppedHere.connect(
            lambda src_chip, t=chip: self._on_group_dropped_here(src_chip, t))

        # Подключаем сигналы первой pane группы. При создании дополнительных
        # panes (через GroupPanel.add_pane) их сигналы подключим в обработчике
        # paneAdded ниже.
        self._connect_pane_signals(panel.tabs, panel)
        panel.paneAdded.connect(
            lambda new_pane, pn=panel: self._connect_pane_signals(new_pane, pn))
        # Если из pane вынесли последний таб — она автоматически удаляется.
        panel.paneRemoved.connect(lambda p: None)

        self._bar.add_chip(chip)
        self._container.addWidget(panel)
        self._groups.append({'chip': chip, 'panel': panel})
        if panel._hidden:
            chip.setVisible(False)
            panel.setVisible(False)
        self._update_remove_enabled()
        return panel

    def set_group_hidden(self, panel, hidden):
        """Скрывает / показывает группу. Скрытая = chip убран из bar'а,
        панель не в стэке, но всё остаётся в памяти. Вернуть можно через
        диалог управления."""
        if panel not in [g['panel'] for g in self._groups]:
            return
        panel._hidden = bool(hidden)
        # Найдём chip
        chip = None
        for g in self._groups:
            if g['panel'] is panel:
                chip = g['chip']
                break
        if chip is None:
            return
        chip.setVisible(not panel._hidden)
        # В splitter-режиме скрываем саму панель - в stack-режиме QStackedWidget
        # сам показывает только активную, скрытие лишнее, но не мешает.
        panel.setVisible(not panel._hidden)
        # Когда юзер «вернул» группу через диалог управления - принудительно
        # сбрасываем collapsed_in_splitter, иначе она останется свёрнутой
        # в splitter mode даже после un-hide.
        if not panel._hidden:
            panel._collapsed_in_splitter = False
            self._apply_collapsed_visibility()
        # Если скрыли активную - переключаемся на следующую видимую
        if panel._hidden:
            cur_idx = self._active_index
            cur_panel = self._groups[cur_idx]['panel'] if 0 <= cur_idx < len(self._groups) else None
            if cur_panel is panel:
                # Ищем первую видимую группу
                for i, g in enumerate(self._groups):
                    if not g['panel']._hidden and g['panel'] is not panel:
                        self._set_active_group(i)
                        break
        self.groupConfigChanged.emit()

    def add_group(self, name=None, color=None):
        """Создаёт новую группу и сразу делает её активной."""
        n = len(self._groups) + 1
        if name is None:
            name = f'Группа {n}'
        if color is None:
            color = DEFAULT_GROUP_COLORS[(n - 1) % len(DEFAULT_GROUP_COLORS)]
        panel = self._add_group_internal(name, color)
        # Активируем новую группу (поиск по _groups, а не по контейнеру -
        # контейнер мог поменяться при set_splitter_mode)
        idx = next((i for i, g in enumerate(self._groups)
                    if g['panel'] is panel), -1)
        if idx >= 0:
            self._set_active_group(idx)
        self.groupConfigChanged.emit()
        return panel

    def _set_active_group(self, index, emit_signal=True):
        """Делает группу с указанным индексом активной: переключает стэк
        и подсветку плашек. В stack-режиме файлы НЕактивных групп выгружаются
        из RAM. В splitter-режиме все панели видны - выгружать ничего не
        нужно, меняется только активный chip и фокус."""
        if not (0 <= index < len(self._groups)):
            return
        previously_active = self._active_index
        self._active_index = index
        if self._layout_mode == 'stack':
            self._stack.setCurrentIndex(index)
        for i, g in enumerate(self._groups):
            g['chip'].set_active(i == index)
        # В stack-mode при смене активной группы — unload предыдущей; иначе
        # файлы продолжали бы тяжело висеть в RAM и обходить LRU. В splitter
        # все панели видны постоянно, выгрузка ломала бы пользовательский опыт.
        if previously_active != index and self._layout_mode == 'stack':
            self._unload_inactive_groups(keep_index=index)
        if emit_signal:
            tabs = self._groups[index]['panel'].tabs
            cur = tabs.currentWidget()
            if cur is not None:
                self.activeTabChanged.emit(cur)
            else:
                self.activeTabChanged.emit(None)

    def _unload_inactive_groups(self, keep_index):
        """Вызывает viewer.unload() у всех viewer'ов во всех panes НЕ-активных
        групп. Освобождает RAM; при возврате на группу файлы lazy-перезагрузятся."""
        for i, g in enumerate(self._groups):
            if i == keep_index:
                continue
            for pane in g['panel'].panes():
                for j in range(pane.count()):
                    w = pane.widget(j)
                    if hasattr(w, 'unload') and getattr(w, '_loaded', False):
                        try:
                            w.unload()
                        except Exception:
                            pass

    def _on_chip_clicked(self, panel):
        idx = next((i for i, g in enumerate(self._groups)
                    if g['panel'] is panel), -1)
        if idx < 0:
            return
        # В splitter-режиме, если панель свёрнута (move_tab_to_other_panel её
        # схлопнул) — клик по chip'у разворачивает её и делает активной.
        if self._layout_mode == 'splitter' and getattr(panel, '_collapsed_in_splitter', False):
            self.expand_group_in_splitter(panel)
        self._set_active_group(idx)

    def _on_tab_activated_in_panel(self, widget):
        """Юзер кликнул по табу внутри pane — найдём её GroupPanel и
        активируем. Внутри группы может быть несколько panes (split editor) —
        активной становится та, в которой кликнули (panel.set_active_pane)."""
        sender = self.sender()
        for i, g in enumerate(self._groups):
            panel = g['panel']
            if panel.has_pane(sender):
                # active pane внутри группы = та куда кликнули
                panel.set_active_pane(sender)
                if self._active_index != i:
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
        # Считаем файлы по ВСЕМ panes группы (split editor)
        n_files = sum(p.count() for p in panel.panes())
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
        # Закрываем все табы во всех panes и удаляем виджеты
        for pane in panel.panes():
            while pane.count() > 0:
                w = pane.widget(0)
                pane.removeTab(0)
                if w is not None:
                    if hasattr(w, 'unload'):
                        try:
                            w.unload()
                        except Exception:
                            pass
                    w.deleteLater()
        # Убираем chip из bar'а и panel из текущего контейнера
        self._bar.remove_chip(group['chip'])
        self._container.removeWidget(panel)
        panel.setParent(None)
        panel.deleteLater()
        self._groups.pop(group_idx)
        # Переключаем активную группу
        new_active = min(group_idx, len(self._groups) - 1)
        if new_active >= 0:
            self._set_active_group(new_active)
        self._update_remove_enabled()
        self.groupConfigChanged.emit()

    def _update_remove_enabled(self):
        can_remove = len(self._groups) > 1
        for g in self._groups:
            g['chip'].set_can_remove(can_remove)

    def _on_files_dropped_on_panel(self, panel, paths):
        idx = next((i for i, g in enumerate(self._groups)
                    if g['panel'] is panel), -1)
        if idx >= 0:
            self.filesDroppedOnGroup.emit(idx, list(paths))

    def _on_group_dropped_here(self, source_chip, target_chip):
        """Перетаскивание ПЛАШКИ source_chip на плашку target_chip - меняем
        порядок групп. Кидаем source перед target (или после, если source был
        слева от target)."""
        src_idx = None
        tgt_idx = None
        for i, g in enumerate(self._groups):
            if g['chip'] is source_chip:
                src_idx = i
            if g['chip'] is target_chip:
                tgt_idx = i
        if src_idx is None or tgt_idx is None or src_idx == tgt_idx:
            return
        # Перемещаем элемент в _groups
        item = self._groups.pop(src_idx)
        # Корректируем target после удаления source
        new_tgt = tgt_idx if src_idx > tgt_idx else tgt_idx - 1
        # Вставляем "над" target если source был ниже, иначе "под"
        insert_at = new_tgt if src_idx > tgt_idx else new_tgt + 1
        self._groups.insert(insert_at, item)

        # Перебираем виджеты в bar'е и стэке в новом порядке
        # Для chips - убрать всех из bar'а и добавить заново
        for g in self._groups:
            self._bar._chips_layout.removeWidget(g['chip'])
        for g in self._groups:
            self._bar._chips_layout.addWidget(g['chip'])

        # Пересобираем виджеты в контейнере в новом порядке. Это работает
        # одинаково и для QStackedWidget, и для QSplitter (у обоих есть
        # addWidget/removeWidget).
        active_panel_obj = item['panel']
        new_active_idx = self._groups.index(item)
        cont = self._container
        # Сохраним текущую активную панель чтобы восстановить после reordering
        prev_active_panel = None
        if 0 <= self._active_index < len(self._groups):
            prev_active_panel = self._groups[self._active_index]['panel']
        for g in self._groups:
            cont.removeWidget(g['panel'])
        for g in self._groups:
            cont.addWidget(g['panel'])
        # Восстанавливаем активную панель по объекту (а не по индексу - тот
        # сменился после reordering)
        if prev_active_panel is not None:
            new_idx = next((i for i, g in enumerate(self._groups)
                            if g['panel'] is prev_active_panel), new_active_idx)
            self._set_active_group(new_idx, emit_signal=False)
        else:
            self._set_active_group(new_active_idx, emit_signal=False)

        self.groupConfigChanged.emit()

    def _on_tab_dropped_on_panel(self, target_panel, source_tabs, tab_index):
        """Перетаскивание таба с панели source_tabs в target_panel.
        Если source - та же панель что target, ничего не делаем (иначе
        переключение в ту же группу + срабатывание визуальных эффектов
        выглядит как dup-операция)."""
        if source_tabs is target_panel.tabs:
            return
        if not (0 <= tab_index < source_tabs.count()):
            return
        widget = source_tabs.widget(tab_index)
        title = source_tabs.tabText(tab_index)
        if widget is None:
            return
        source_tabs.removeTab(tab_index)
        target_panel.tabs.addTab(widget, title)
        target_panel.tabs.setCurrentWidget(widget)
        # Делаем target_panel активной чтобы юзер видел результат
        new_idx = next((i for i, g in enumerate(self._groups)
                        if g['panel'] is target_panel), -1)
        if new_idx >= 0:
            self._set_active_group(new_idx)

    # ----- Конфигурация для save/restore -----

    def get_group_configs(self):
        return [
            {
                'name': g['chip'].name,
                'color': g['chip'].color,
                'hidden': bool(getattr(g['panel'], '_hidden', False)),
            }
            for g in self._groups
        ]

    def get_open_files_per_group(self):
        """Список списков: для каждой группы — все файлы во всех её panes.
        Intra-group split НЕ сохраняется между запусками — все табы
        восстанавливаются в одной pane группы (юзер сам разделит при
        необходимости через «Переместить в другую панель»)."""
        result = []
        for g in self._groups:
            files = []
            for pane in g['panel'].panes():
                for i in range(pane.count()):
                    w = pane.widget(i)
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
        (для restore_session, чтобы все табы оставались lazy).

        side может быть "active", "left", "right" или "group:N" (N - индекс
        группы). Последний вариант нужен для restore_session, чтобы класть
        файл в произвольную группу без побочного активирования."""
        idx = self._active_index
        if side == "left":
            idx = 0
        elif side == "right":
            idx = 1 if len(self._groups) >= 2 else 0
        elif isinstance(side, str) and side.startswith("group:"):
            try:
                idx = int(side.split(":", 1)[1])
            except (ValueError, IndexError):
                pass
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
        # При silent НЕ переключаем активную группу - таб лёг куда надо, но
        # пользователь продолжит работать в той же группе что и был. Без
        # silent — активируем целевую группу (обычное поведение open_file).
        if not silent and self._active_index != idx:
            self._set_active_group(idx, emit_signal=False)
        if not silent:
            self.activeTabChanged.emit(widget)

    # ----- Stack/Splitter переключатель -----

    def set_stack_mode(self, stack):
        """Переключает layout-режим. stack=True → одна группа видна за раз
        (QStackedWidget). stack=False → splitter с несколькими видимыми
        группами одновременно.

        При переходе панели мигрируют из одного контейнера в другой; активная
        группа сохраняется."""
        new_mode = 'stack' if stack else 'splitter'
        if new_mode == self._layout_mode:
            return
        self._set_layout_mode(new_mode)

    def set_splitter_mode(self, splitter):
        """Алиас: set_splitter_mode(True) == set_stack_mode(False).
        В UI меньше путаницы какой флаг что означает."""
        self.set_stack_mode(not splitter)

    def get_layout_mode(self):
        return self._layout_mode

    def _set_layout_mode(self, mode):
        """Внутренняя миграция панелей между _stack и _splitter."""
        if mode == self._layout_mode:
            return
        # Сохраняем активную панель чтобы восстановить после миграции
        prev_active_idx = self._active_index

        # Извлекаем панели из старого контейнера
        old = self._container
        for g in self._groups:
            old.removeWidget(g['panel'])

        # Переключаем режим и заливаем панели в новый контейнер
        self._layout_mode = mode
        new = self._container
        for g in self._groups:
            new.addWidget(g['panel'])
            # В splitter скрываем: 1) hidden (через диалог управления) и
            # 2) collapsed_in_splitter (через move_tab_to_other_panel).
            # В stack — QStackedWidget сам показывает только активную,
            # видимость остальных не критична.
            if mode == 'splitter':
                p = g['panel']
                visible = (not getattr(p, '_hidden', False)
                           and not getattr(p, '_collapsed_in_splitter', False))
                p.setVisible(visible)
            else:
                g['panel'].setVisible(True)

        # Показываем нужную страницу в mode_switcher
        self._mode_switcher.setCurrentWidget(new)

        # Восстанавливаем активную группу
        if 0 <= prev_active_idx < len(self._groups):
            self._set_active_group(prev_active_idx, emit_signal=False)

    def move_tab_to_new_pane(self, group_panel, source_pane, tab_index):
        """Контекстное меню вкладки → «Переместить в другую панель».
        Делает split ВНУТРИ той же группы: создаёт новую pane справа от
        source_pane (или использует существующую, если их уже >=2) и
        переносит туда таб.

        Это intra-group split — как «Split editor right» в VS Code: видишь
        два файла одной группы рядом, можно сравнивать. Между ГРУППАМИ
        разделения не делаем — на скриншоте было ровно эта проблема:
        4 группы → 4 столбца после move, что не было целью."""
        # Валидация
        if not isinstance(source_pane, EditorTabWidget):
            return False
        if not group_panel.has_pane(source_pane):
            return False
        if not (0 <= tab_index < source_pane.count()):
            return False

        widget = source_pane.widget(tab_index)
        title = source_pane.tabText(tab_index)
        if widget is None:
            return False

        # Целевая pane: если в группе уже есть другая — используем её. Иначе
        # создаём новую справа от source. Так двойное «Переместить» подряд
        # не плодит бесконечно panes, а просто перебрасывает таб туда-сюда.
        existing_other = next(
            (p for p in group_panel.panes() if p is not source_pane), None)
        from PyQt6.QtCore import QElapsedTimer
        _t = QElapsedTimer()
        _t.start()
        target_pane = existing_other if existing_other is not None else group_panel.add_pane()
        _add_pane_ms = _t.elapsed()

        # ПРОИЗВОДИТЕЛЬНОСТЬ. blockSignals(True) на pane'ях — иначе каскад
        # currentChanged → tabActivated → activeTabChanged многократно
        # запускает release_heavy_caches() на всех других viewer'ах
        # (QTreeWidget.clear() на десятках тысяч элементов = секунды).
        # setUpdatesEnabled(False) — Qt не перерисовывает промежуточные
        # кадры.
        # ВРЕМЕННО: профайлим каждую операцию в crash_log, чтобы понять
        # где именно тормозит на больших логах.
        timer = QElapsedTimer()
        timer.start()
        timings = {'add_pane': _add_pane_ms}

        group_panel.setUpdatesEnabled(False)
        source_pane.blockSignals(True)
        target_pane.blockSignals(True)
        target_was_empty = target_pane.count() == 0
        try:
            t0 = timer.elapsed()
            source_pane.removeTab(tab_index)
            timings['removeTab'] = timer.elapsed() - t0

            t0 = timer.elapsed()
            target_pane.addTab(widget, title)
            timings['addTab'] = timer.elapsed() - t0

            if not target_was_empty:
                t0 = timer.elapsed()
                target_pane.setCurrentWidget(widget)
                timings['setCurrentWidget'] = timer.elapsed() - t0
        finally:
            source_pane.blockSignals(False)
            target_pane.blockSignals(False)
            t0 = timer.elapsed()
            group_panel.setUpdatesEnabled(True)
            timings['setUpdatesEnabled(True)'] = timer.elapsed() - t0

        t0 = timer.elapsed()
        group_panel.set_active_pane(target_pane)
        timings['set_active_pane'] = timer.elapsed() - t0

        # Если source pane стал пустым — lastTabClosed мы задавили
        # blockSignals выше, поэтому проверяем вручную.
        if source_pane.count() == 0:
            t0 = timer.elapsed()
            group_panel.remove_pane(source_pane)
            timings['remove_pane'] = timer.elapsed() - t0

        t0 = timer.elapsed()
        self.activeTabChanged.emit(widget)
        timings['activeTabChanged.emit'] = timer.elapsed() - t0

        t0 = timer.elapsed()
        self.groupConfigChanged.emit()
        timings['groupConfigChanged.emit'] = timer.elapsed() - t0

        total = timer.elapsed()
        # Логируем профайл, если операция заняла > 200мс. Юзер найдёт
        # лог в crash_log.txt рядом с .exe и пришлёт.
        if total > 200:
            import logging
            _log = logging.getLogger('tab_manager')
            _log.warning(
                "SLOW move_tab_to_new_pane: total=%dms, breakdown=%s",
                total,
                ', '.join(f'{k}={v}ms' for k, v in timings.items() if v > 0),
            )
        return True

    # Backward-compat: старое имя метода. Делегирует на новый intra-group split.
    def move_tab_to_other_panel(self, source_panel, tab_index):
        return self.move_tab_to_new_pane(source_panel, source_panel.tabs, tab_index)

    def _apply_collapsed_visibility(self):
        """В splitter-режиме применяет _collapsed_in_splitter и _hidden к
        видимости каждой панели. В stack-режиме — no-op (там QStackedWidget
        сам управляет видимостью)."""
        if self._layout_mode != 'splitter':
            return
        for g in self._groups:
            p = g['panel']
            visible = (not getattr(p, '_hidden', False)
                       and not getattr(p, '_collapsed_in_splitter', False))
            p.setVisible(visible)

    def expand_group_in_splitter(self, panel):
        """Разворачивает свёрнутую группу: убирает _collapsed_in_splitter и
        делает панель видимой. Используется при клике на chip свёрнутой
        группы в splitter-режиме."""
        if not hasattr(panel, '_collapsed_in_splitter'):
            return
        panel._collapsed_in_splitter = False
        self._apply_collapsed_visibility()

    def get_current_viewer(self):
        tabs = self.active_group
        if tabs is None or tabs.count() == 0:
            return None
        w = tabs.currentWidget()
        if isinstance(w, LogViewerWidget):
            return w
        return None

