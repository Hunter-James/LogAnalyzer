"""Диалог управления группами табов.

Открывается из кнопки «🗂 Группы» в главном тулбаре. Содержит:
- список всех активных групп с операциями:
    показать/скрыть, переименовать, цвет, архивировать, удалить;
- список архивированных групп с операцией «Восстановить» / «Удалить навсегда»;
- кнопку «Добавить группу».

Работает напрямую с SplitManager через переданные коллбэки/методы:
- split_manager.iter_panels() / get_archive() для чтения;
- split_manager.set_group_hidden(panel, hidden) для скрытия;
- split_manager._archive_panel(panel) для архивации;
- split_manager._remove_panel(panel) для удаления;
- split_manager.add_group() для создания;
- split_manager.restore_from_archive(idx) для восстановления.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QWidget, QGroupBox, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor


class _GroupRow(QFrame):
    """Одна строка в списке: цветной квадратик + имя + кнопки операций."""

    def __init__(self, name, color, hidden, n_files, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 4, 6, 4)
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

        self.btn_toggle_visible = QPushButton(
            "Показать" if hidden else "Скрыть")
        self.btn_toggle_visible.setFixedWidth(85)
        h.addWidget(self.btn_toggle_visible)

        self.btn_archive = QPushButton("Архивировать")
        self.btn_archive.setFixedWidth(110)
        h.addWidget(self.btn_archive)

        self.btn_remove = QPushButton("Удалить")
        self.btn_remove.setFixedWidth(85)
        h.addWidget(self.btn_remove)


class _ArchiveRow(QFrame):
    """Одна строка в списке архива: цвет + имя + N файлов + Восстановить / Удалить."""

    def __init__(self, name, color, n_files, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(8)

        swatch = QLabel()
        swatch.setFixedSize(18, 18)
        swatch.setStyleSheet(
            f"background-color: {color}; border-radius: 3px;"
        )
        h.addWidget(swatch)

        info = QLabel(f"{name}  —  {n_files} файл(ов)")
        info.setStyleSheet("color: #AAAAAA; font-style: italic;")
        h.addWidget(info, 1)

        self.btn_restore = QPushButton("Восстановить")
        self.btn_restore.setFixedWidth(110)
        h.addWidget(self.btn_restore)

        self.btn_delete = QPushButton("Удалить")
        self.btn_delete.setFixedWidth(85)
        h.addWidget(self.btn_delete)


class GroupsManagerDialog(QDialog):
    """Диалог управления группами. Открывается из главного тулбара."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle("Управление группами")
        self.resize(620, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # --- Активные группы ---
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

        # --- Архив ---
        archive_group = QGroupBox("Архив (файлы выгружены)")
        arc_layout = QVBoxLayout(archive_group)
        self._archive_container = QWidget()
        self._archive_layout = QVBoxLayout(self._archive_container)
        self._archive_layout.setContentsMargins(0, 0, 0, 0)
        self._archive_layout.setSpacing(4)

        scroll2 = QScrollArea()
        scroll2.setWidgetResizable(True)
        scroll2.setWidget(self._archive_container)
        arc_layout.addWidget(scroll2, 1)

        root.addWidget(archive_group, 1)

        # --- Кнопка закрыть ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

        self._refresh()

    def _clear_layout(self, layout):
        while layout.count() > 0:
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _refresh(self):
        """Пересоздаёт списки активных и архивных групп с актуальными
        кнопками-обработчиками."""
        sm = self.main_window.split_manager

        # ----- Активные группы -----
        self._clear_layout(self._active_layout)
        panels = sm.iter_panels()
        for i, panel in enumerate(panels):
            # Достаём данные из chip соответствующей группы
            chip = sm._groups[i]['chip']
            n_files = panel.tabs.count()
            hidden = bool(getattr(panel, '_hidden', False))
            row = _GroupRow(chip.name, chip.color, hidden, n_files)
            row.btn_toggle_visible.clicked.connect(
                lambda _checked=False, p=panel, h=hidden:
                self._on_toggle_hidden(p, not h))
            row.btn_archive.clicked.connect(
                lambda _checked=False, p=panel: self._on_archive(p))
            # Удалить - блокируем если осталась 1 группа
            row.btn_remove.setEnabled(len(panels) > 1)
            row.btn_remove.clicked.connect(
                lambda _checked=False, p=panel: self._on_remove(p))
            self._active_layout.addWidget(row)
        self._active_layout.addStretch(1)

        # ----- Архив -----
        self._clear_layout(self._archive_layout)
        archive = sm.get_archive()
        if not archive:
            empty = QLabel("(архив пуст)")
            empty.setStyleSheet("color: #888888; padding: 8px;")
            self._archive_layout.addWidget(empty)
        else:
            for idx, entry in enumerate(archive):
                n = len(entry.get('files') or [])
                row = _ArchiveRow(entry['name'], entry['color'], n)
                row.btn_restore.clicked.connect(
                    lambda _checked=False, i=idx: self._on_restore(i))
                row.btn_delete.clicked.connect(
                    lambda _checked=False, i=idx: self._on_delete_archived(i))
                self._archive_layout.addWidget(row)
        self._archive_layout.addStretch(1)

    # ----- Обработчики -----

    def _on_add(self):
        self.main_window.split_manager.add_group()
        self._refresh()

    def _on_toggle_hidden(self, panel, hidden):
        self.main_window.split_manager.set_group_hidden(panel, hidden)
        self._refresh()

    def _on_archive(self, panel):
        self.main_window.split_manager._archive_panel(panel)
        self._refresh()

    def _on_remove(self, panel):
        self.main_window.split_manager._remove_panel(panel)
        self._refresh()

    def _on_restore(self, index):
        result = self.main_window.split_manager.restore_from_archive(index)
        if result:
            panel, files = result
            # Раскладываем файлы lazy
            import os
            self.main_window.split_manager.active_group = panel.tabs
            for f in files:
                if os.path.exists(f):
                    self.main_window.load_file(f, side="active", lazy=True)
        self._refresh()

    def _on_delete_archived(self, index):
        r = QMessageBox.question(
            self, "Удалить из архива",
            "Удалить группу из архива безвозвратно?\n"
            "Файлы на диске не пострадают, потеряется только её группировка.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        sm = self.main_window.split_manager
        archive = sm.get_archive()
        if 0 <= index < len(archive):
            del archive[index]
            sm.set_archive(archive)
        self._refresh()
