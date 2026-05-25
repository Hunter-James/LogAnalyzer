"""Модальный диалог «Сводка партий по группе».

Запускает GroupStatsWorker над списком файлов активной группы, показывает
прогресс по файлам, по окончании — дерево партий с агрегированной
статистикой, списком файлов, временным диапазоном и кодами."""

import os
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QProgressBar, QPushButton, QTreeWidget,
                             QTreeWidgetItem, QStackedWidget, QWidget,
                             QAbstractItemView)

from core.group_stats_worker import GroupStatsWorker
from core.models import NO_BATCH
from config import THEMES


class GroupStatsDialog(QDialog):
    """Окно: при открытии стартует worker; пока он считает — виден прогресс,
    после — дерево результатов. Кнопка «Отмена» прерывает worker, дерево
    показывает то что успели насчитать."""

    # Лимит уникальных кодов на партию в дереве - больше класть нет смысла,
    # юзер всё равно столько вручную не просмотрит.
    MAX_CODES_PER_BATCH = 5000

    def __init__(self, file_paths, group_name='', theme_name='Default', parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Сводка партий — {group_name}" if group_name else "Сводка партий")
        self.resize(900, 700)
        self._theme_name = theme_name if theme_name in THEMES else 'Default'

        v = QVBoxLayout(self)

        # Прогресс-блок: показывается пока worker работает
        self._progress_widget = self._build_progress_widget()
        # Результат-блок: дерево + кнопка закрытия
        self._result_widget, self._tree = self._build_result_widget()

        # Stack чтобы не плодить условную видимость
        self._stack = QStackedWidget()
        self._stack.addWidget(self._progress_widget)
        self._stack.addWidget(self._result_widget)
        self._stack.setCurrentWidget(self._progress_widget)
        v.addWidget(self._stack)

        # Стилизация под тему окна
        self._apply_theme()

        # Запускаем worker
        self._worker = GroupStatsWorker(file_paths)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._total_files = len(file_paths)
        self._worker.start()

    # ----- UI builders -----

    def _build_progress_widget(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addStretch()

        self._lbl_progress_title = QLabel("Анализ партий по всем файлам группы …")
        self._lbl_progress_title.setStyleSheet("font-size: 13pt; font-weight: bold;")
        self._lbl_progress_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._lbl_progress_title)

        self._lbl_progress_file = QLabel("")
        self._lbl_progress_file.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._lbl_progress_file)

        self._pbar = QProgressBar()
        self._pbar.setRange(0, 100)
        layout.addWidget(self._pbar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_cancel = QPushButton("Отмена")
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        return w

    def _build_result_widget(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 10, 10, 10)

        self._lbl_summary = QLabel("")
        layout.addWidget(self._lbl_summary)

        tree = QTreeWidget()
        tree.setHeaderLabels(["Партия / Категория", "Значение"])
        tree.setColumnWidth(0, 480)
        tree.setUniformRowHeights(True)
        tree.setAlternatingRowColors(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(tree, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        return w, tree

    def _apply_theme(self):
        t = THEMES[self._theme_name]
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QLabel {{ color: {t['text_main']}; }}
            QProgressBar {{
                background-color: {t['bg_panel']};
                color: {t['text_main']};
                border: 1px solid {t['border']};
                text-align: center;
            }}
            QProgressBar::chunk {{ background-color: {t['accent']}; }}
            QPushButton {{
                background-color: {t['bg_panel']};
                color: {t['text_main']};
                border: 1px solid {t['border']};
                padding: 6px 16px;
            }}
            QPushButton:hover {{ background-color: {t['accent']}; color: white; }}
            QTreeWidget {{
                background-color: {t['bg_panel']};
                color: {t['text_main']};
                border: 1px solid {t['border']};
            }}
        """)

    # ----- Worker callbacks -----

    def _on_progress(self, current, total, filename):
        pct = int((current + 1) / max(1, total) * 100)
        self._pbar.setValue(pct)
        self._lbl_progress_file.setText(f"Файл {current + 1} из {total}: {filename}")

    def _on_finished(self, batches, error_msg):
        if error_msg == '__CANCELLED__':
            # Юзер нажал «Отмена» — показываем что успели насчитать.
            self._lbl_summary.setText(
                f"<i>Анализ отменён. Показаны частичные данные по "
                f"{len(batches)} партии(ям).</i>"
            )
        elif error_msg:
            self._lbl_summary.setText(f"<i>Ошибка: {error_msg}</i>")
        else:
            self._lbl_summary.setText(
                f"<b>Найдено партий: {len(batches)}.</b> "
                f"Двойной клик по партии — развернуть/свернуть."
            )
        self._populate_tree(batches)
        self._stack.setCurrentWidget(self._result_widget)

    def _on_cancel(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._btn_cancel.setEnabled(False)
            self._btn_cancel.setText("Останавливаем …")

    # ----- Tree population -----

    def _populate_tree(self, batches):
        """Раскладываем batches в дерево. Сортировка по first_ts (хронологически),
        партии без timestamp — в конец."""
        self._tree.clear()
        if not batches:
            return

        t = THEMES[self._theme_name]
        info_c = QColor(t.get('info', '#2E8B57'))
        warn_c = QColor(t.get('warn', '#FFA500'))
        error_c = QColor(t.get('error', '#CD5C5C'))
        muted_c = QColor(t.get('text_muted', '#999999'))

        # Сортируем партии: сначала с известным first_ts (хронологически),
        # потом NO_BATCH («вне партии») в самый конец.
        def sort_key(item):
            bid, data = item
            no_batch_flag = 1 if bid == NO_BATCH else 0
            ts = data['first_ts'] or '99:99:99.999'
            first_file = data.get('first_file', '')
            return (no_batch_flag, first_file, ts)

        self._tree.setUpdatesEnabled(False)
        try:
            for bid, data in sorted(batches.items(), key=sort_key):
                counters = data['counters']
                files = data['files']
                codes = data['codes']
                total_events = sum(counters.values())

                if bid == NO_BATCH:
                    title = (f"Вне партии  —  {total_events:,} событий, "
                             f"{len(files)} файл(ов), {len(codes):,} уникальных кодов")
                else:
                    title = (f"Партия {bid}  —  {total_events:,} событий, "
                             f"{len(files)} файл(ов), {len(codes):,} уникальных кодов")
                root = QTreeWidgetItem(self._tree, [title, ""])

                # 📊 Статистика
                stats_node = QTreeWidgetItem(root, ["📊 Статистика", ""])
                rows = [
                    ("Напечатано", counters['printed'], info_c),
                    ("Прочитано", counters['scanned'], info_c),
                    ("No read", counters['noread'], warn_c),
                    ("Верифицировано", counters['verified'], info_c),
                    ("Отбраковано", counters['rejected'], error_c),
                    ("Не верифицировано", counters['not_verified'], warn_c),
                ]
                for label, n, color in rows:
                    ci = QTreeWidgetItem(stats_node, [label, f"{n:,}"])
                    ci.setForeground(0, color if n else muted_c)
                    ci.setForeground(1, color if n else muted_c)

                # 📂 Файлы
                if files:
                    files_node = QTreeWidgetItem(root, [
                        f"📂 Файлы ({len(files)})", ""])
                    # Сортируем по имени файла
                    for path in sorted(files.keys()):
                        cnt = files[path]
                        QTreeWidgetItem(files_node,
                                        [os.path.basename(path),
                                         f"{cnt:,} событий"])

                # 📅 Временной диапазон
                if data['first_ts']:
                    range_node = QTreeWidgetItem(root, ["📅 Диапазон", ""])
                    QTreeWidgetItem(range_node, [
                        f"начало: {data['first_ts']}",
                        os.path.basename(data['first_file'])])
                    QTreeWidgetItem(range_node, [
                        f"конец: {data['last_ts']}",
                        os.path.basename(data['last_file'])])

                # 🔑 Уникальные коды (limit MAX_CODES_PER_BATCH)
                if codes:
                    codes_node = QTreeWidgetItem(root, [
                        f"🔑 Уникальные коды ({len(codes):,})", ""])
                    sorted_codes = sorted(codes)
                    for c in sorted_codes[:self.MAX_CODES_PER_BATCH]:
                        QTreeWidgetItem(codes_node, [c, ""])
                    if len(sorted_codes) > self.MAX_CODES_PER_BATCH:
                        hidden = len(sorted_codes) - self.MAX_CODES_PER_BATCH
                        QTreeWidgetItem(codes_node, [
                            f"… ещё {hidden:,} кодов не показано "
                            f"(лимит {self.MAX_CODES_PER_BATCH})", ""])
        finally:
            self._tree.setUpdatesEnabled(True)

    # ----- Cleanup -----

    def closeEvent(self, event):
        """Останавливаем worker если ещё работает (юзер закрыл крестиком)."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            # Ждём короткое время — обычно loader проверяет
            # isInterruptionRequested на каждой итерации файла.
            self._worker.wait(500)
        super().closeEvent(event)
