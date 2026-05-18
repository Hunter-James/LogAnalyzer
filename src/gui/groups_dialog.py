"""Диалог управления группами табов.

Открывается из кнопки «🗂 Группы» в главном тулбаре.

Структура:
  ┌── Группы ─────────────────────────────────┐
  │ [●] Группа 1   [Скрыть][Переим][Цвет][Удалить] │
  │ [●] Группа 2   [Показать][Переим][Цвет][Удалить] │   ← клик выбирает строку
  │ [➕ Добавить новую группу]                 │
  ├── Файлы выбранной группы ─────────────────┤
  │ Имя         Размер   Изменён    Путь      │
  │ app.log     3.5 MB   2026-05-12 C:/.../   │   ← таблица файлов
  │ app2.log    1.2 MB   2026-05-11 C:/.../   │
  └────────────────────────────────────────────┘
"""
import os
import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QWidget, QGroupBox, QMessageBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QInputDialog,
    QColorDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor


def _format_size(num_bytes):
    """Удобный формат размера: 3.5 MB / 187 KB / 42 B."""
    if num_bytes is None:
        return "—"
    n = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{num_bytes} B"


def _format_mtime(ts):
    if ts is None:
        return "—"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


class _GroupRow(QFrame):
    """Одна строка группы. Клик по строке (любому месту кроме кнопок)
    эмитит clicked → диалог переключает таблицу файлов на эту группу."""

    clicked = pyqtSignal()

    def __init__(self, name, color, hidden, n_files, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._selected = False

        h = QHBoxLayout(self)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)

        # Цветной квадратик
        swatch = QLabel()
        swatch.setFixedSize(18, 18)
        swatch.setStyleSheet(
            f"background-color: {color}; border-radius: 3px;"
        )
        h.addWidget(swatch)

        info = QLabel(f"{name}  —  {n_files} файл(ов)")
        if hidden:
            info.setStyleSheet("color: #888888; font-style: italic;")
        h.addWidget(info, 1)

        # Кнопки операций
        self.btn_toggle_visible = QPushButton(
            "Показать" if hidden else "Скрыть")
        self.btn_toggle_visible.setFixedWidth(85)
        h.addWidget(self.btn_toggle_visible)

        self.btn_rename = QPushButton("Переименовать")
        self.btn_rename.setFixedWidth(120)
        h.addWidget(self.btn_rename)

        self.btn_color = QPushButton("Цвет")
        self.btn_color.setFixedWidth(60)
        h.addWidget(self.btn_color)

        self.btn_remove = QPushButton("Удалить")
        self.btn_remove.setFixedWidth(80)
        h.addWidget(self.btn_remove)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_selected(self, selected):
        self._selected = bool(selected)
        if selected:
            self.setStyleSheet(
                "_GroupRow { background-color: rgba(42, 130, 218, 60); "
                "border: 1px solid #2A82DA; border-radius: 3px; }"
            )
        else:
            self.setStyleSheet("")


class GroupsManagerDialog(QDialog):
    """Диалог управления группами. Открывается из главного тулбара."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle("Управление группами")
        self.resize(820, 560)

        self._row_widgets = []  # для подсветки выделенной строки
        self._selected_index = 0  # индекс выбранной группы (default - первая)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # --- Группы (верхняя секция) ---
        active_group = QGroupBox("Группы")
        ag_layout = QVBoxLayout(active_group)
        self._active_container = QWidget()
        self._active_layout = QVBoxLayout(self._active_container)
        self._active_layout.setContentsMargins(0, 0, 0, 0)
        self._active_layout.setSpacing(4)

        scroll1 = QScrollArea()
        scroll1.setWidgetResizable(True)
        scroll1.setWidget(self._active_container)
        ag_layout.addWidget(scroll1, 1)

        btn_add = QPushButton("➕ Добавить новую группу")
        btn_add.clicked.connect(self._on_add)
        ag_layout.addWidget(btn_add)

        root.addWidget(active_group, 1)

        # --- Файлы выбранной группы (нижняя секция) ---
        files_group = QGroupBox("Файлы выбранной группы")
        fg_layout = QVBoxLayout(files_group)
        self._files_table = QTableWidget(0, 4)
        self._files_table.setHorizontalHeaderLabels(
            ["Имя", "Размер", "Изменён", "Путь"])
        self._files_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._files_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self._files_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self._files_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self._files_table.verticalHeader().setVisible(False)
        self._files_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._files_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._files_table.setAlternatingRowColors(True)
        fg_layout.addWidget(self._files_table)
        root.addWidget(files_group, 1)

        # --- Кнопка закрыть ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

        self._refresh()

    # ----- Утилиты -----

    def _clear_layout(self, layout):
        while layout.count() > 0:
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _refresh(self):
        """Пересоздаёт список групп и обновляет таблицу файлов."""
        sm = self.main_window.split_manager
        self._clear_layout(self._active_layout)
        self._row_widgets = []

        panels = sm.iter_panels()
        for i, panel in enumerate(panels):
            chip = sm._groups[i]['chip']
            n_files = panel.tabs.count()
            hidden = bool(getattr(panel, '_hidden', False))
            row = _GroupRow(chip.name, chip.color, hidden, n_files)
            row.clicked.connect(
                lambda idx=i: self._on_row_clicked(idx))
            row.btn_toggle_visible.clicked.connect(
                lambda _checked=False, p=panel, h=hidden:
                self._on_toggle_hidden(p, not h))
            row.btn_rename.clicked.connect(
                lambda _checked=False, p=panel: self._on_rename(p))
            row.btn_color.clicked.connect(
                lambda _checked=False, p=panel: self._on_change_color(p))
            row.btn_remove.setEnabled(len(panels) > 1)
            row.btn_remove.clicked.connect(
                lambda _checked=False, p=panel: self._on_remove(p))
            self._active_layout.addWidget(row)
            self._row_widgets.append(row)
        self._active_layout.addStretch(1)

        # Корректируем индекс выделенной группы (если группу удалили)
        if not self._row_widgets:
            self._selected_index = -1
        elif self._selected_index < 0 or self._selected_index >= len(self._row_widgets):
            self._selected_index = 0

        self._highlight_selected()
        self._refresh_files_table()

    def _highlight_selected(self):
        for i, row in enumerate(self._row_widgets):
            row.set_selected(i == self._selected_index)

    def _refresh_files_table(self):
        """Заполняет таблицу файлов выбранной группы."""
        self._files_table.setRowCount(0)
        sm = self.main_window.split_manager
        if not (0 <= self._selected_index < len(sm._groups)):
            return
        panel = sm._groups[self._selected_index]['panel']
        # Собираем file_path из всех виджетов в tabs
        files = []
        for i in range(panel.tabs.count()):
            w = panel.tabs.widget(i)
            file_path = getattr(w, 'file_path', None)
            if file_path:
                files.append(file_path)

        self._files_table.setRowCount(len(files))
        for row, path in enumerate(files):
            name = os.path.basename(path)
            try:
                st = os.stat(path)
                size = st.st_size
                mtime = st.st_mtime
            except OSError:
                size = None
                mtime = None
            self._files_table.setItem(row, 0, QTableWidgetItem(name))
            self._files_table.setItem(row, 1, QTableWidgetItem(_format_size(size)))
            self._files_table.setItem(row, 2, QTableWidgetItem(_format_mtime(mtime)))
            self._files_table.setItem(row, 3, QTableWidgetItem(path))

    # ----- Обработчики -----

    def _on_row_clicked(self, idx):
        self._selected_index = idx
        self._highlight_selected()
        self._refresh_files_table()

    def _on_add(self):
        self.main_window.split_manager.add_group()
        self._refresh()

    def _on_toggle_hidden(self, panel, hidden):
        self.main_window.split_manager.set_group_hidden(panel, hidden)
        self._refresh()

    def _on_rename(self, panel):
        sm = self.main_window.split_manager
        # Находим chip
        chip = None
        for g in sm._groups:
            if g['panel'] is panel:
                chip = g['chip']
                break
        if chip is None:
            return
        new, ok = QInputDialog.getText(
            self, "Переименовать группу", "Новое название:",
            text=chip.name,
        )
        if ok and new.strip():
            chip.set_name(new.strip())
            panel._group_name = new.strip()
            sm.groupConfigChanged.emit()
            self._refresh()

    def _on_change_color(self, panel):
        sm = self.main_window.split_manager
        chip = None
        for g in sm._groups:
            if g['panel'] is panel:
                chip = g['chip']
                break
        if chip is None:
            return
        col = QColorDialog.getColor(QColor(chip.color), self, "Цвет группы")
        if col.isValid():
            chip.set_color(col.name())
            panel._group_color = col.name()
            sm.groupConfigChanged.emit()
            self._refresh()

    def _on_remove(self, panel):
        self.main_window.split_manager._remove_panel(panel)
        self._refresh()
