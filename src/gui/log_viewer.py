import os
import re
from datetime import datetime
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QSplitter, QAbstractItemView,
                             QMessageBox, QApplication, QLineEdit, QHBoxLayout, QLabel, QFrame,
                             QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QMenu,
                             QTreeWidgetItemIterator, QTextEdit, QToolButton, QCheckBox,
                             QWidgetAction, QScrollArea)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QKeySequence, QTextCursor, QTextCharFormat, QColor, QShortcut

from core.models import LogModel
from core.workers import LogLoader
from gui.custom_widgets import ScalableListView, ScalableTextEdit
from config import THEMES


class LogViewerWidget(QWidget):
    # Signals to notify MainWindow about state changes
    statsChanged = pyqtSignal(dict)
    progressChanged = pyqtSignal(int)
    loadingFinished = pyqtSignal()

    # Лимиты, чтобы журнал не приводил к лагам/вылетам
    MAX_JOURNAL_MATCHES_PER_SEARCH = 1000
    MAX_JOURNAL_LINE_PREVIEW = 300
    MAX_JOURNAL_SEARCHES = 50
    MAX_DETAIL_HIGHLIGHTS = 1000

    def __init__(self, file_path, theme_name, font_size, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.current_theme_name = theme_name
        self.current_font_size = font_size

        self.stats = {}
        self.preserved_real_index = None

        # Все логгеры, обнаруженные в файле, и подмножество включённых
        self.all_loggers = []
        self.active_loggers = set()
        self.logger_checkboxes = {}

        # Filter states (Global filters passed from MainWindow, Search is local)
        self.global_filters = {
            "info": True,
            "debug": True,
            "warn": True,
            "error": True,
            "group_dupes": False,
        }

        # Search debounce timer
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self.refresh_view)

        self.setup_ui()
        self.apply_theme(theme_name, font_size)

        # Load the file immediately
        self.load_file()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Local Search Bar ---
        self.search_frame = QFrame()
        self.search_frame.setObjectName("SearchPanel")
        search_layout = QHBoxLayout(self.search_frame)
        search_layout.setContentsMargins(5, 5, 5, 5)

        search_layout.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search in this file...")
        self.search_input.textChanged.connect(self.on_search_text_changed)
        search_layout.addWidget(self.search_input)

        # Кнопка-меню для фильтрации по логгеру/компоненту
        self.btn_loggers = QToolButton()
        self.btn_loggers.setText("Компоненты")
        self.btn_loggers.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_loggers.setEnabled(False)  # включится после загрузки файла
        self.loggers_menu = QMenu(self)
        self.btn_loggers.setMenu(self.loggers_menu)
        search_layout.addWidget(self.btn_loggers)

        # Поля диапазона времени. Принимают HH:MM, HH:MM:SS, HH:MM:SS.mmm.
        # "от" пустые поля заполняются нулями, "до" - девятками (см. _parse_time_input).
        search_layout.addSpacing(8)
        search_layout.addWidget(QLabel("Время:"))
        self.time_from = QLineEdit()
        self.time_from.setPlaceholderText("c (ЧЧ:ММ:СС)")
        self.time_from.setFixedWidth(110)
        self.time_from.setToolTip("Нижняя граница времени (включительно). Пусто = с начала файла.")
        self.time_from.textChanged.connect(self._on_time_changed)
        search_layout.addWidget(self.time_from)

        search_layout.addWidget(QLabel("–"))

        self.time_to = QLineEdit()
        self.time_to.setPlaceholderText("по (ЧЧ:ММ:СС)")
        self.time_to.setFixedWidth(110)
        self.time_to.setToolTip("Верхняя граница времени (включительно). Пусто = до конца файла.")
        self.time_to.textChanged.connect(self._on_time_changed)
        search_layout.addWidget(self.time_to)

        # Кнопка для сохранения результатов поиска в журнал
        self.btn_save_search = QPushButton("Добавить в журнал")
        self.btn_save_search.clicked.connect(self.on_save_search_clicked)
        search_layout.addWidget(self.btn_save_search)

        layout.addWidget(self.search_frame)

        # --- Splitter (List + Details) ---
        self.splitter = QSplitter(Qt.Orientation.Vertical)

        # Log List
        self.model = LogModel()
        self.log_view = ScalableListView()
        self.log_view.setUniformItemSizes(True)
        self.log_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.log_view.setModel(self.model)

        # Bottom Tabs (Выделение / Журнал поиска)
        self.bottom_tabs = QTabWidget()

        # Details View
        self.details_view = ScalableTextEdit()
        self.details_view.setReadOnly(True)
        self.bottom_tabs.addTab(self.details_view, "Выделение")

        # Search Journal Tree
        self.search_journal_tree = QTreeWidget()
        self.search_journal_tree.setHeaderHidden(True)
        # Включаем множественное выделение (с Shift / Ctrl)
        self.search_journal_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Подключаем кастомное контекстное меню
        self.search_journal_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.search_journal_tree.customContextMenuRequested.connect(self.show_journal_context_menu)
        self.search_journal_tree.itemDoubleClicked.connect(self.on_journal_item_double_clicked)
        self.bottom_tabs.addTab(self.search_journal_tree, "Поиск")

        self.splitter.addWidget(self.log_view)
        self.splitter.addWidget(self.bottom_tabs)
        self.splitter.setSizes([600, 250])

        layout.addWidget(self.splitter)

        # --- Local Stats Panel ---
        self.stats_frame = QFrame()
        self.stats_frame.setObjectName("StatsPanel")
        stats_layout = QHBoxLayout(self.stats_frame)
        stats_layout.setContentsMargins(5, 2, 5, 2)

        self.lbl_stats = QLabel("Loading...")
        stats_layout.addWidget(self.lbl_stats)
        stats_layout.addStretch()

        layout.addWidget(self.stats_frame)

        # Connections
        self.log_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.log_view.zoomRequest.connect(self.on_zoom_request)
        self.details_view.zoomRequest.connect(self.on_zoom_request)
        self.model.filterFinished.connect(self.on_filter_finished_scroll)

        # F3 / Shift+F3 - навигация по совпадениям/строкам.
        # WidgetWithChildrenShortcut, чтобы хоткей работал и когда фокус в поле поиска,
        # и при этом не конфликтовал между разными вкладками.
        sc_next = QShortcut(QKeySequence(Qt.Key.Key_F3), self)
        sc_next.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_next.activated.connect(lambda: self._goto_match(1))

        sc_prev = QShortcut(QKeySequence("Shift+F3"), self)
        sc_prev.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_prev.activated.connect(lambda: self._goto_match(-1))

    def load_file(self):
        self.loader = LogLoader(self.file_path)
        self.loader.progress.connect(self.progressChanged.emit)
        self.loader.finished.connect(self.on_load_finished)
        self.loader.start()

    def on_load_finished(self, entries, stats, error_msg):
        if error_msg:
            QMessageBox.critical(self, "Error", f"Failed to load file:\n{error_msg}")
            self.lbl_stats.setText("Error loading file")
            self.loadingFinished.emit()
            return

        self.model.set_entries(entries)
        self.stats = stats
        self.update_stats_text()
        self.statsChanged.emit(stats)
        self.loadingFinished.emit()

        # Собираем уникальные логгеры из файла и наполняем меню "Компоненты"
        self._collect_loggers(entries)

        if self.model.rowCount() > 0:
            self.log_view.scrollToBottom()

        # Apply initial filters
        self.refresh_view()

    def _collect_loggers(self, entries):
        """Собирает уникальные имена логгеров и пересобирает меню фильтра."""
        loggers = sorted({e.logger for e in entries if e.logger})
        self.all_loggers = loggers
        self.active_loggers = set(loggers)
        self._rebuild_loggers_menu()

    def _rebuild_loggers_menu(self):
        self.loggers_menu.clear()
        self.logger_checkboxes = {}

        if not self.all_loggers:
            self.btn_loggers.setEnabled(False)
            self.btn_loggers.setText("Компоненты")
            return

        self.btn_loggers.setEnabled(True)

        # Кнопка "Все / Ни одного" сверху
        toggle_btn = QPushButton("Все / Ни одного")
        toggle_btn.setFlat(False)
        toggle_btn.clicked.connect(self._toggle_all_loggers)
        toggle_action = QWidgetAction(self.loggers_menu)
        toggle_action.setDefaultWidget(toggle_btn)
        self.loggers_menu.addAction(toggle_action)
        self.loggers_menu.addSeparator()

        # Чекбоксы оборачиваем в QWidgetAction, чтобы клик не закрывал меню
        for logger in self.all_loggers:
            cb = QCheckBox(logger)
            cb.setChecked(logger in self.active_loggers)
            cb.toggled.connect(lambda checked, lg=logger: self._on_logger_toggled(lg, checked))
            wa = QWidgetAction(self.loggers_menu)
            wa.setDefaultWidget(cb)
            self.loggers_menu.addAction(wa)
            self.logger_checkboxes[logger] = cb

        self._update_loggers_button_label()

    def _on_logger_toggled(self, logger, checked):
        if checked:
            self.active_loggers.add(logger)
        else:
            self.active_loggers.discard(logger)
        self._update_loggers_button_label()
        self.refresh_view()

    def _toggle_all_loggers(self):
        # Если включено всё - выключаем всё; иначе включаем всё
        if len(self.active_loggers) == len(self.all_loggers):
            self.active_loggers.clear()
        else:
            self.active_loggers = set(self.all_loggers)

        for logger, cb in self.logger_checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(logger in self.active_loggers)
            cb.blockSignals(False)

        self._update_loggers_button_label()
        self.refresh_view()

    def _update_loggers_button_label(self):
        total = len(self.all_loggers)
        if total == 0:
            self.btn_loggers.setText("Компоненты")
        elif len(self.active_loggers) == total:
            self.btn_loggers.setText(f"Компоненты: все ({total})")
        else:
            self.btn_loggers.setText(f"Компоненты: {len(self.active_loggers)}/{total}")

    def update_stats_text(self):
        total = sum(self.stats.values())
        text = f"Total: {total:,} | Info: {self.stats.get('INFO', 0):,} | Error: {self.stats.get('ERROR', 0):,} | Debug: {self.stats.get('DEBUG', 0):,} | Warn: {self.stats.get('WARN', 0):,}"
        self.lbl_stats.setText(text)

    def apply_theme(self, theme_name, font_size):
        self.current_theme_name = theme_name
        self.current_font_size = font_size
        t = THEMES[theme_name]

        # Apply font to details view and search journal
        font = QFont(t['mono_font'], font_size)
        self.details_view.setFont(font)
        self.search_journal_tree.setFont(font)

        # Update model theme
        self.model.set_theme(theme_name, font_size)

        # Apply styles to local panels
        style = f"""
            #SearchPanel, #StatsPanel {{ background-color: {t['bg_panel']}; border: 1px solid {t['border']}; }}
            QLabel {{ color: {t['text_main']}; }}
            QLineEdit {{
                background-color: {t['bg_main']}; border: 1px solid {t['border']};
                padding: 4px;
                color: {t['text_main']};
            }}
            QPushButton {{
                background-color: {t['bg_panel']}; color: {t['text_main']};
                border: 1px solid {t['border']}; padding: 4px 10px;
                border-radius: 2px;
            }}
            QPushButton:hover {{ background-color: {t['selection']}; }}
        """
        self.search_frame.setStyleSheet(style)
        self.stats_frame.setStyleSheet(style)

        # Стилизация дерева под общую тему текста
        self.search_journal_tree.setStyleSheet(f"""
            QTreeWidget {{ background-color: {t['bg_panel']}; color: {t['text_main']}; border: none; }}
            QTreeWidget::item:selected {{ background-color: {t['selection']}; color: {t['text_main']}; }}
        """)

    def on_zoom_request(self, delta):
        if delta > 0:
            self.current_font_size = min(24, self.current_font_size + 1)
        else:
            self.current_font_size = max(6, self.current_font_size - 1)

        self.apply_theme(self.current_theme_name, self.current_font_size)

        window = self.window()
        if hasattr(window, 'on_zoom_request'):
            window.on_zoom_request(delta)

    def set_global_filters(self, info, debug, warn, error, group_dupes=False):
        """Called by MainWindow when global checkboxes change"""
        self.global_filters["info"] = info
        self.global_filters["debug"] = debug
        self.global_filters["warn"] = warn
        self.global_filters["error"] = error
        self.global_filters["group_dupes"] = group_dupes
        self.refresh_view()

    def on_search_text_changed(self, text):
        self.search_timer.start()

    def _on_time_changed(self):
        # Подкрашиваем поле красноватым, если ввод не парсится (пустое - всегда ОК)
        for field, is_upper in ((self.time_from, False), (self.time_to, True)):
            text = field.text().strip()
            ok = (not text) or (self._parse_time_input(text, is_upper) is not None)
            field.setStyleSheet("" if ok else "background-color: #5a2a2a; color: #ffdddd;")
        # Дебаунсим вместе с поиском
        self.search_timer.start()

    @staticmethod
    def _parse_time_input(text, is_upper):
        """Парсит ввод пользователя в строку HH:MM:SS.mmm для лексикографического сравнения.
        Возвращает None если ввод невалиден.
        Принимаются HH:MM, HH:MM:SS, HH:MM:SS.mmm. Для is_upper=True недостающие
        части заполняются 9-ками ("10:00" -> "10:00:59.999"), иначе нулями."""
        text = text.strip()
        if not text:
            return None
        parts = text.split(':')
        if len(parts) < 2 or len(parts) > 3:
            return None
        try:
            h = int(parts[0])
            m = int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                return None

            s = 0
            ms = 0
            if len(parts) == 3:
                sec_part = parts[2]
                if '.' in sec_part:
                    s_str, ms_str = sec_part.split('.', 1)
                    s = int(s_str)
                    # Дополняем дробные мс до 3 знаков справа нулями, потом обрезаем
                    ms = int((ms_str + '000')[:3])
                else:
                    s = int(sec_part)
                    ms = 999 if is_upper else 0
            else:
                # Секунды не указаны
                if is_upper:
                    s = 59
                    ms = 999
                else:
                    s = 0
                    ms = 0

            if not (0 <= s <= 59):
                return None

            return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
        except ValueError:
            return None

    def on_save_search_clicked(self):
        """Логика добавления текущего поиска в журнал"""
        search_text = self.search_input.text()
        if not search_text:
            return

        self.bottom_tabs.setCurrentWidget(self.search_journal_tree)

        # Берём _raw_filtered_indices, а не _filtered_indices: при включённой группировке
        # последний содержит только лидеров групп, а в журнал нужно сохранить все совпадения.
        filtered_indices = self.model._raw_filtered_indices
        match_count = len(filtered_indices)
        current_time = datetime.now().strftime("%H:%M:%S")

        max_per_search = self.MAX_JOURNAL_MATCHES_PER_SEARCH
        max_preview = self.MAX_JOURNAL_LINE_PREVIEW
        display_count = min(match_count, max_per_search)
        entries = self.model._entries

        # Отключаем перерисовку и сортировку на время массовой вставки -
        # иначе при больших объёмах журнал лагает.
        self.search_journal_tree.setUpdatesEnabled(False)
        was_sorting = self.search_journal_tree.isSortingEnabled()
        self.search_journal_tree.setSortingEnabled(False)
        try:
            # Удаляем старые поиски, чтобы общее количество узлов не росло бесконечно
            while self.search_journal_tree.topLevelItemCount() >= self.MAX_JOURNAL_SEARCHES:
                old = self.search_journal_tree.takeTopLevelItem(0)
                del old

            root_text = f'Поиск "{search_text}" (найдено {match_count} совпадений) - {current_time}'
            root_item = QTreeWidgetItem(self.search_journal_tree, [root_text])

            file_item = QTreeWidgetItem(
                root_item,
                [f"Файл: {os.path.basename(self.file_path)} (совпадений: {match_count})"]
            )

            # Готовим items пачкой и добавляем через addChildren - это заметно быстрее,
            # чем создавать каждый item с указанием родителя.
            new_items = []
            for row in range(display_count):
                real_index = filtered_indices[row]
                entry = entries[real_index]

                line = entry.full_line.strip()
                if len(line) > max_preview:
                    line = line[:max_preview] + '...'

                match_item = QTreeWidgetItem([f"Строка {real_index + 1}: {line}"])
                match_item.setData(0, Qt.ItemDataRole.UserRole, real_index)
                new_items.append(match_item)

            file_item.addChildren(new_items)

            if match_count > max_per_search:
                QTreeWidgetItem(
                    file_item,
                    [f"... ещё {match_count - max_per_search} совпадений не показано (для производительности)"]
                )

            root_item.setExpanded(True)
            file_item.setExpanded(True)
        finally:
            self.search_journal_tree.setSortingEnabled(was_sorting)
            self.search_journal_tree.setUpdatesEnabled(True)

        self.search_journal_tree.scrollToItem(root_item)

    def show_journal_context_menu(self, pos):
        """Контекстное меню для журнала поиска"""
        menu = QMenu(self)
        copy_action = menu.addAction("Копировать выделенное")
        menu.addSeparator()
        clear_action = menu.addAction("Очистить журнал")

        action = menu.exec(self.search_journal_tree.mapToGlobal(pos))

        if action == copy_action:
            self.copy_journal_selection()
        elif action == clear_action:
            self.search_journal_tree.clear()

    def copy_journal_selection(self):
        """Копирование выделенных элементов из дерева журнала"""
        text_list = []
        # QTreeWidgetItemIterator позволяет пройти по дереву сверху вниз и сохранить правильный порядок
        iterator = QTreeWidgetItemIterator(self.search_journal_tree, QTreeWidgetItemIterator.IteratorFlag.Selected)
        while iterator.value():
            item = iterator.value()
            text_list.append(item.text(0))
            iterator += 1

        if text_list:
            QApplication.clipboard().setText("\n".join(text_list))

    def on_journal_item_double_clicked(self, item, column):
        real_index = item.data(0, Qt.ItemDataRole.UserRole)
        if real_index is not None:
            row = self.model.find_row_by_real_index(real_index)
            if row != -1:
                index = self.model.index(row)
                self.log_view.setCurrentIndex(index)
                self.log_view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
                self.log_view.setFocus()
            else:
                QMessageBox.information(self, "Информация",
                                        "Эта строка скрыта текущими фильтрами (INFO/DEBUG/WARN/ERROR) или текстом поиска.\n\nОчистите фильтры для перехода к ней.")

    def refresh_view(self):
        current_index = self.log_view.currentIndex()
        if current_index.isValid():
            self.preserved_real_index = self.model.get_real_index(current_index.row())
        else:
            self.preserved_real_index = None

        # Если включены все логгеры - не передаём ограничение (None = пропускаем все)
        if self.all_loggers and len(self.active_loggers) < len(self.all_loggers):
            loggers_filter = set(self.active_loggers)
        else:
            loggers_filter = None

        time_from = self._parse_time_input(self.time_from.text(), is_upper=False)
        time_to = self._parse_time_input(self.time_to.text(), is_upper=True)

        self.model.update_filters(
            self.global_filters["info"],
            self.global_filters["debug"],
            self.global_filters["error"],
            self.global_filters["warn"],
            self.search_input.text(),
            self.global_filters["group_dupes"],
            loggers_filter,
            time_from,
            time_to,
        )

        # Перерисовываем подсветку, если поисковый запрос изменился
        self._highlight_search_matches()

    def _goto_match(self, direction):
        """F3 / Shift+F3: перейти к следующей/предыдущей строке в отфильтрованном виде.
        Поскольку фильтр поиска уже отбрасывает несовпадения, "следующая строка" = "следующее совпадение"."""
        row_count = self.model.rowCount()
        if row_count == 0:
            return

        current = self.log_view.currentIndex()
        if current.isValid():
            new_row = current.row() + direction
        else:
            new_row = 0 if direction > 0 else row_count - 1

        # Закольцовываем
        if new_row < 0:
            new_row = row_count - 1
        elif new_row >= row_count:
            new_row = 0

        new_index = self.model.index(new_row)
        self.log_view.setCurrentIndex(new_index)
        self.log_view.scrollTo(new_index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.log_view.setFocus()

    def on_filter_finished_scroll(self):
        if self.preserved_real_index is not None:
            new_row = self.model.find_row_by_real_index(self.preserved_real_index)
            if new_row != -1:
                new_index = self.model.index(new_row)
                self.log_view.setCurrentIndex(new_index)
                self.log_view.scrollTo(new_index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def on_selection_changed(self, selected, deselected):
        selected_indexes = self.log_view.selectedIndexes()
        if not selected_indexes:
            self.details_view.clear()
            self.details_view.setExtraSelections([])
            return
        selected_indexes.sort(key=lambda x: x.row())
        display_indexes = selected_indexes[:50]
        full_text = ""
        for idx in display_indexes:
            text = self.model.data(idx, Qt.ItemDataRole.UserRole)
            full_text += text + "\n" + "=" * 80 + "\n"
        if len(selected_indexes) > 50:
            full_text += f"\n... and {len(selected_indexes) - 50} more items selected."
        self.details_view.setPlainText(full_text)
        self._highlight_search_matches()

    def _highlight_search_matches(self):
        """Подсвечивает совпадения текущего поискового запроса в окне 'Выделение'
        и автоматически прокручивает к первому совпадению."""
        # Очищаем прошлые подсветки в любом случае
        self.details_view.setExtraSelections([])

        search_text = self.search_input.text()
        if not search_text:
            return

        full_text = self.details_view.toPlainText()
        if not full_text:
            return

        # Совпадения ищем так же, как FilterWorker: сначала regex, потом literal fallback
        positions = []
        try:
            pattern = re.compile(search_text, re.IGNORECASE)
            for m in pattern.finditer(full_text):
                if m.start() != m.end():
                    positions.append((m.start(), m.end()))
                if len(positions) >= self.MAX_DETAIL_HIGHLIGHTS:
                    break
        except re.error:
            search_lower = search_text.lower()
            text_lower = full_text.lower()
            slen = len(search_text)
            pos = text_lower.find(search_lower)
            while pos != -1 and len(positions) < self.MAX_DETAIL_HIGHLIGHTS:
                positions.append((pos, pos + slen))
                pos = text_lower.find(search_lower, pos + slen)

        if not positions:
            return

        document = self.details_view.document()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#FFD400"))
        fmt.setForeground(QColor("#000000"))

        extra_selections = []
        for start, end in positions:
            cursor = QTextCursor(document)
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = fmt
            extra_selections.append(sel)

        self.details_view.setExtraSelections(extra_selections)

        # Прыгаем к первому совпадению, чтобы при длинной строке не пришлось скроллить вручную
        first_start = positions[0][0]
        scroll_cursor = QTextCursor(document)
        scroll_cursor.setPosition(first_start)
        self.details_view.setTextCursor(scroll_cursor)
        self.details_view.ensureCursorVisible()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            # Проверяем, какой элемент сейчас в фокусе, и копируем оттуда
            if self.details_view.hasFocus():
                self.details_view.copy()
                return
            elif self.search_journal_tree.hasFocus():
                self.copy_journal_selection()
                return

            selected_indexes = self.log_view.selectedIndexes()
            if selected_indexes:
                selected_indexes.sort(key=lambda x: x.row())
                text_list = []
                for idx in selected_indexes:
                    text_list.append(self.model.data(idx, Qt.ItemDataRole.UserRole))
                full_text = "\n".join(text_list)
                QApplication.clipboard().setText(full_text)
        else:
            super().keyPressEvent(event)