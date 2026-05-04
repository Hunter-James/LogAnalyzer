import os
import re
import json
from datetime import datetime
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QSplitter, QAbstractItemView,
                             QMessageBox, QApplication, QLineEdit, QHBoxLayout, QLabel, QFrame,
                             QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QMenu,
                             QTreeWidgetItemIterator, QTextEdit, QToolButton, QCheckBox,
                             QWidgetAction, QScrollArea)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QKeySequence, QTextCursor, QTextCharFormat, QColor, QShortcut

from core.models import LogModel
from core.workers import LogLoader, IncrementalLogParser
from gui.custom_widgets import ScalableListView, ScalableTextEdit, MarkerScrollBar
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

    def __init__(self, file_path, theme_name, font_size, bookmarks=None,
                 ui_features=None, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.current_theme_name = theme_name
        self.current_font_size = font_size

        # Флаги видимости опциональных элементов UI - применяются в apply_ui_features.
        # По умолчанию все включены, иначе подхватываем переданный словарь.
        from config import DEFAULT_UI_FEATURES
        self._ui_features = dict(DEFAULT_UI_FEATURES)
        if ui_features:
            self._ui_features.update({k: bool(v) for k, v in ui_features.items() if k in DEFAULT_UI_FEATURES})

        self.stats = {}
        self.preserved_real_index = None
        # Закладки восстанавливаемые из settings (применяются после загрузки модели)
        self._initial_bookmarks = list(bookmarks or [])
        # Позиция в исходном файле в байтах (нужна для tail-режима)
        self._tail_position = 0
        # Парсер для tail - хранит open_entry между порциями новых строк
        self._tail_parser = None
        self._tail_timer = None
        # Файл архивирован - tail для него не имеет смысла
        self._is_archive = file_path.lower().endswith(('.gz', '.zip'))

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

        search_layout.addWidget(QLabel("Поиск:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск по этому файлу...")
        self.search_input.textChanged.connect(self.on_search_text_changed)
        search_layout.addWidget(self.search_input)

        # Match case (Aa) - точно как в Notepad++. По умолчанию off (регистронезависимо).
        # Явный стиль: при включённом состоянии синий фон + белый текст -
        # сразу видно, активна кнопка или нет, на любой теме.
        self.btn_match_case = QToolButton()
        self.btn_match_case.setText("Aa")
        self.btn_match_case.setCheckable(True)
        self.btn_match_case.setStyleSheet("""
            QToolButton {
                padding: 4px 10px;
                font-weight: bold;
                font-family: Consolas, 'Courier New', monospace;
                border: 1px solid #888;
                border-radius: 3px;
                min-width: 24px;
            }
            QToolButton:hover {
                border-color: #bbb;
            }
            QToolButton:checked {
                background-color: #2A82DA;
                color: #FFFFFF;
                border: 1px solid #1858A0;
            }
            QToolButton:checked:hover {
                background-color: #3A92EA;
            }
        """)
        self._update_match_case_tooltip()
        self.btn_match_case.toggled.connect(self._on_match_case_toggled)
        search_layout.addWidget(self.btn_match_case)

        # Кнопка-меню для фильтрации по логгеру/компоненту
        self.btn_loggers = QToolButton()
        self.btn_loggers.setText("Компоненты")
        self.btn_loggers.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_loggers.setEnabled(False)  # включится после загрузки файла
        self.loggers_menu = QMenu(self)
        self.btn_loggers.setMenu(self.loggers_menu)
        search_layout.addWidget(self.btn_loggers)

        # Кнопка-меню для фильтрации по партиям (setCurrentBatch / api/close)
        self.btn_batches = QToolButton()
        self.btn_batches.setText("Партии")
        self.btn_batches.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_batches.setEnabled(False)
        self.batches_menu = QMenu(self)
        self.btn_batches.setMenu(self.batches_menu)
        search_layout.addWidget(self.btn_batches)
        # Активные партии (set из batch_id или "" для "вне партии")
        self.all_batches = []  # list of (batch_id, count, first_ts, last_ts)
        self.active_batches = set()
        self.batch_checkboxes = {}

        # Поля диапазона времени. Принимают HH:MM, HH:MM:SS, HH:MM:SS.mmm.
        # "от" пустые поля заполняются нулями, "до" - девятками (см. _parse_time_input).
        # Сохраняем ссылки на лейблы и spacer чтобы их можно было скрывать вместе с полями.
        self._time_spacer_idx = search_layout.count()
        search_layout.addSpacing(8)
        self.lbl_time = QLabel("Время:")
        search_layout.addWidget(self.lbl_time)
        self.time_from = QLineEdit()
        self.time_from.setPlaceholderText("c (ЧЧ:ММ:СС)")
        self.time_from.setFixedWidth(110)
        self.time_from.setToolTip("Нижняя граница времени (включительно). Пусто = с начала файла.")
        self.time_from.textChanged.connect(self._on_time_changed)
        search_layout.addWidget(self.time_from)

        self.lbl_time_dash = QLabel("–")
        search_layout.addWidget(self.lbl_time_dash)

        self.time_to = QLineEdit()
        self.time_to.setPlaceholderText("по (ЧЧ:ММ:СС)")
        self.time_to.setFixedWidth(110)
        self.time_to.setToolTip("Верхняя граница времени (включительно). Пусто = до конца файла.")
        self.time_to.textChanged.connect(self._on_time_changed)
        search_layout.addWidget(self.time_to)

        # Tail-режим: следим за дописанием файла. Для архивов отключаем.
        self.btn_follow = QToolButton()
        self.btn_follow.setText("⏵ Следить")
        self.btn_follow.setCheckable(True)
        self.btn_follow.setStyleSheet("""
            QToolButton { padding: 4px 10px; border: 1px solid #888; border-radius: 3px; }
            QToolButton:hover { border-color: #bbb; }
            QToolButton:checked {
                background-color: #2E8B57; color: #FFFFFF; border: 1px solid #1E5C3A;
            }
            QToolButton:checked:hover { background-color: #3FA068; }
            QToolButton:disabled { color: #888; }
        """)
        self.btn_follow.setToolTip(
            "Следить за дописыванием файла (tail -f).\n"
            "Новые строки автоматически подгружаются и появляются в конце."
        )
        if self._is_archive:
            self.btn_follow.setEnabled(False)
            self.btn_follow.setToolTip("Tail-режим недоступен для архивов (.gz, .zip)")
        self.btn_follow.toggled.connect(self._on_follow_toggled)
        search_layout.addWidget(self.btn_follow)

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
        # Скроллбар с метками ERROR/WARN
        self.log_view.setVerticalScrollBar(MarkerScrollBar(Qt.Orientation.Vertical))

        # Bottom Tabs (Выделение / Журнал поиска)
        self.bottom_tabs = QTabWidget()

        # Кнопка-toggle "Форматировать JSON" в углу tab-бара
        self.btn_format_json = QToolButton()
        self.btn_format_json.setText("{ } JSON")
        self.btn_format_json.setCheckable(True)
        self.btn_format_json.setStyleSheet("""
            QToolButton { padding: 3px 10px; border: 1px solid #888; border-radius: 3px; }
            QToolButton:hover { border-color: #bbb; }
            QToolButton:checked {
                background-color: #2A82DA; color: #FFFFFF; border: 1px solid #1858A0;
            }
        """)
        self.btn_format_json.setToolTip(
            "Форматировать JSON-фрагменты в окне 'Выделение' с отступами.\n"
            "Полезно для длинных HTTP-боди и подписанных запросов."
        )
        self.btn_format_json.toggled.connect(lambda _: self._refresh_details_view())
        self.bottom_tabs.setCornerWidget(self.btn_format_json, Qt.Corner.TopRightCorner)

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

        self.lbl_stats = QLabel("Загрузка...")
        stats_layout.addWidget(self.lbl_stats)
        stats_layout.addStretch()
        # Информация о выделении: строка, время, Δt от предыдущей
        self.lbl_selection_info = QLabel("")
        stats_layout.addWidget(self.lbl_selection_info)

        layout.addWidget(self.stats_frame)

        # Connections
        self.log_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.log_view.zoomRequest.connect(self.on_zoom_request)
        self.details_view.zoomRequest.connect(self.on_zoom_request)
        self.model.filterFinished.connect(self.on_filter_finished_scroll)
        self.model.filterFinished.connect(self._update_scrollbar_markers)

        # F3 / Shift+F3 - навигация по совпадениям/строкам.
        # WidgetWithChildrenShortcut, чтобы хоткей работал и когда фокус в поле поиска,
        # и при этом не конфликтовал между разными вкладками.
        sc_next = QShortcut(QKeySequence(Qt.Key.Key_F3), self)
        sc_next.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_next.activated.connect(lambda: self._goto_match(1))

        sc_prev = QShortcut(QKeySequence("Shift+F3"), self)
        sc_prev.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_prev.activated.connect(lambda: self._goto_match(-1))

        # Закладки: Ctrl+B - toggle, F2 - следующая, Shift+F2 - предыдущая
        sc_bm_toggle = QShortcut(QKeySequence("Ctrl+B"), self)
        sc_bm_toggle.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_bm_toggle.activated.connect(self._toggle_bookmark_current)

        sc_bm_next = QShortcut(QKeySequence(Qt.Key.Key_F2), self)
        sc_bm_next.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_bm_next.activated.connect(lambda: self._goto_bookmark(1))

        sc_bm_prev = QShortcut(QKeySequence("Shift+F2"), self)
        sc_bm_prev.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_bm_prev.activated.connect(lambda: self._goto_bookmark(-1))

        # Применяем стартовую видимость UI согласно настройкам
        self.apply_ui_features(self._ui_features)

    def load_file(self):
        self.loader = LogLoader(self.file_path)
        self.loader.progress.connect(self.progressChanged.emit)
        self.loader.finished.connect(self.on_load_finished)
        self.loader.start()

    def on_load_finished(self, entries, stats, error_msg, last_pos):
        if error_msg:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить файл:\n{error_msg}")
            self.lbl_stats.setText("Ошибка загрузки файла")
            self.loadingFinished.emit()
            return

        self.model.set_entries(entries)
        self._tail_position = last_pos  # для tail-режима
        # Восстанавливаем закладки если были сохранены в сессии
        if self._initial_bookmarks:
            valid = [b for b in self._initial_bookmarks if 0 <= b < len(entries)]
            self.model.set_bookmarks(valid)
            self._initial_bookmarks = []
        self.stats = stats
        self.update_stats_text()
        self.statsChanged.emit(stats)
        self.loadingFinished.emit()

        # Собираем уникальные логгеры из файла и наполняем меню "Компоненты"
        self._collect_loggers(entries)
        # Собираем партии из распарсенных сегментов и наполняем меню "Партии"
        self._collect_batches()

        if self.model.rowCount() > 0:
            self.log_view.scrollToBottom()

        # Apply initial filters
        self.refresh_view()

    # ----- Tail / follow mode -----

    def _on_follow_toggled(self, checked):
        if checked:
            self._start_following()
        else:
            self._stop_following()

    def _start_following(self):
        """Запускает периодический опрос файла на предмет дописывания."""
        if self._is_archive:
            return
        # Парсер с пустым open_entry - последняя запись из исходного загрузка уже закрыта
        self._tail_parser = IncrementalLogParser()
        if self._tail_timer is None:
            self._tail_timer = QTimer(self)
            self._tail_timer.setInterval(1000)  # опрос раз в секунду
            self._tail_timer.timeout.connect(self._tail_check)
        self._tail_timer.start()
        self.btn_follow.setText("⏸ Остановить")

    def _stop_following(self):
        if self._tail_timer is not None:
            self._tail_timer.stop()
        self._tail_parser = None
        self.btn_follow.setText("⏵ Следить")

    def _tail_check(self):
        """Опрос файла. Если размер вырос - дочитываем новые байты, парсим, добавляем в модель.
        Если файл не рос с прошлого тика, закрываем "висящую" запись чтобы она показалась."""
        if self._is_archive:
            return
        try:
            current_size = os.path.getsize(self.file_path)
        except OSError:
            return  # файл удалён/недоступен

        if current_size < self._tail_position:
            # Файл "укоротили" (rotate, truncate) - читаем заново с начала
            self._tail_position = 0
            self._tail_parser = IncrementalLogParser()

        if current_size == self._tail_position:
            # Ничего нового - закрываем висящую запись чтобы она появилась в списке
            quiet_entry = self._tail_parser.take_open()
            if quiet_entry is not None:
                self._append_tail_entries([quiet_entry])
            return

        try:
            with open(self.file_path, 'rb') as f:
                f.seek(self._tail_position)
                chunk = f.read(current_size - self._tail_position)
        except OSError:
            return

        self._tail_position = current_size
        text = chunk.decode('utf-8', errors='replace')
        # split, сохраняя \n чтобы не сломать last_position при лишних/недостающих байтах
        lines = text.splitlines(keepends=True)
        if not lines:
            return

        new_closed = self._tail_parser.feed_lines(lines)
        if new_closed:
            self._append_tail_entries(new_closed)

    def _append_tail_entries(self, new_entries):
        """Общая логика добавления tail-записей: stats, model, новые логгеры, auto-scroll."""
        if not new_entries:
            return
        for e in new_entries:
            if e.level in self.stats:
                self.stats[e.level] = self.stats.get(e.level, 0) + 1

        # Был ли пользователь у нижнего края - тогда auto-scroll после фильтра
        sb = self.log_view.verticalScrollBar()
        was_at_bottom = sb.value() >= sb.maximum() - 5

        self.model.append_entries(new_entries)

        # Новые компоненты - подбираем их в меню "Компоненты"
        new_loggers = {e.logger for e in new_entries if e.logger} - set(self.all_loggers)
        if new_loggers:
            for lg in sorted(new_loggers):
                self.all_loggers.append(lg)
                self.active_loggers.add(lg)
            self.all_loggers.sort()
            self._rebuild_loggers_menu()

        self.update_stats_text()
        self.statsChanged.emit(self.stats)

        if was_at_bottom:
            QTimer.singleShot(50, self.log_view.scrollToBottom)

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

    # ----- Партии (сегментация по setCurrentBatch / api/close) -----

    def _collect_batches(self):
        """Подбирает summary партий из модели и пересобирает меню фильтра."""
        self.all_batches = self.model.get_batch_summary()
        self.active_batches = {bid for (bid, _c, _f, _l) in self.all_batches}
        self._rebuild_batches_menu()

    def _rebuild_batches_menu(self):
        self.batches_menu.clear()
        self.batch_checkboxes = {}

        if not self.all_batches:
            self.btn_batches.setEnabled(False)
            self.btn_batches.setText("Партии")
            return

        self.btn_batches.setEnabled(True)

        toggle_btn = QPushButton("Все / Ни одного")
        toggle_btn.clicked.connect(self._toggle_all_batches)
        toggle_action = QWidgetAction(self.batches_menu)
        toggle_action.setDefaultWidget(toggle_btn)
        self.batches_menu.addAction(toggle_action)
        self.batches_menu.addSeparator()

        for bid, count, first_ts, last_ts in self.all_batches:
            label = f"Вне партии  ({count} строк)" if not bid else f"Партия {bid}  ({count} строк)"
            cb = QCheckBox(label)
            cb.setChecked(bid in self.active_batches)
            tooltip = f"Время: {first_ts} → {last_ts}" if first_ts else "Время: —"
            cb.setToolTip(tooltip)
            cb.toggled.connect(lambda checked, b=bid: self._on_batch_toggled(b, checked))
            wa = QWidgetAction(self.batches_menu)
            wa.setDefaultWidget(cb)
            self.batches_menu.addAction(wa)
            self.batch_checkboxes[bid] = cb

        self._update_batches_button_label()

    def _on_batch_toggled(self, bid, checked):
        if checked:
            self.active_batches.add(bid)
        else:
            self.active_batches.discard(bid)
        self._update_batches_button_label()
        self.refresh_view()

    def _toggle_all_batches(self):
        all_ids = {bid for (bid, _c, _f, _l) in self.all_batches}
        if self.active_batches == all_ids:
            self.active_batches.clear()
        else:
            self.active_batches = set(all_ids)
        for bid, cb in self.batch_checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(bid in self.active_batches)
            cb.blockSignals(False)
        self._update_batches_button_label()
        self.refresh_view()

    def _update_batches_button_label(self):
        total = len(self.all_batches)
        if total == 0:
            self.btn_batches.setText("Партии")
        elif len(self.active_batches) == total:
            self.btn_batches.setText(f"Партии: все ({total})")
        else:
            self.btn_batches.setText(f"Партии: {len(self.active_batches)}/{total}")

    def update_stats_text(self):
        total = sum(self.stats.values())
        text = (
            f"Всего: {total:,} | "
            f"INFO: {self.stats.get('INFO', 0):,} | "
            f"ERROR: {self.stats.get('ERROR', 0):,} | "
            f"DEBUG: {self.stats.get('DEBUG', 0):,} | "
            f"WARN: {self.stats.get('WARN', 0):,}"
        )
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

        # Цвета меток ERROR/WARN на скроллбаре зависят от темы - пересчитываем
        self._update_scrollbar_markers()

    def apply_ui_features(self, features):
        """Применяет настройки видимости элементов UI.
        Скрытые виджеты не теряют состояние - можно вернуть в Настройках."""
        self._ui_features = dict(self._ui_features)
        self._ui_features.update({k: bool(v) for k, v in features.items() if k in self._ui_features})

        # Опциональные элементы поисковой панели
        self.btn_match_case.setVisible(self._ui_features["match_case"])
        self.btn_loggers.setVisible(self._ui_features["loggers_filter"])
        self.btn_batches.setVisible(self._ui_features["batches_filter"])
        self.lbl_time.setVisible(self._ui_features["time_range"])
        self.time_from.setVisible(self._ui_features["time_range"])
        self.lbl_time_dash.setVisible(self._ui_features["time_range"])
        self.time_to.setVisible(self._ui_features["time_range"])
        self.btn_follow.setVisible(self._ui_features["tail_mode"])
        self.btn_save_search.setVisible(self._ui_features["save_to_journal"])

        # Tail с скрытой кнопки нельзя контролировать - принудительно останавливаем
        if not self._ui_features["tail_mode"] and self.btn_follow.isChecked():
            self.btn_follow.setChecked(False)

        # Кнопка JSON в углу tab-бара
        self.btn_format_json.setVisible(self._ui_features["json_format"])

        # Δt и информация о выделении в статус-баре
        self.lbl_selection_info.setVisible(self._ui_features["selection_info"])

        # Скроллбар-метки: при выключении гасим текущие
        sb = self.log_view.verticalScrollBar()
        if isinstance(sb, MarkerScrollBar):
            if self._ui_features["scrollbar_markers"]:
                self._update_scrollbar_markers()
            else:
                sb.set_markers([])

        # Если фильтр времени скрыт, при следующем refresh_view применим как None
        # (само поле остаётся, но refresh_view проверяет видимость - см. ниже).
        self.refresh_view()

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

    def _on_match_case_toggled(self, _checked):
        self._update_match_case_tooltip()
        self.search_timer.start()

    def _update_match_case_tooltip(self):
        if self.btn_match_case.isChecked():
            self.btn_match_case.setToolTip(
                "Match case: ВКЛ — регистр учитывается.\nНажмите чтобы выключить."
            )
        else:
            self.btn_match_case.setToolTip(
                "Match case: ВЫКЛ — регистр игнорируется.\nНажмите чтобы включить."
            )

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

        # Если фильтр логгеров скрыт через настройки - игнорируем его состояние
        if (self._ui_features.get("loggers_filter", True)
                and self.all_loggers
                and len(self.active_loggers) < len(self.all_loggers)):
            loggers_filter = set(self.active_loggers)
        else:
            loggers_filter = None

        # Аналогично для диапазона времени
        if self._ui_features.get("time_range", True):
            time_from = self._parse_time_input(self.time_from.text(), is_upper=False)
            time_to = self._parse_time_input(self.time_to.text(), is_upper=True)
        else:
            time_from = None
            time_to = None

        # Match case учитываем только если кнопка Aa включена в настройках
        case_sensitive = (
            self._ui_features.get("match_case", True) and self.btn_match_case.isChecked()
        )

        # Фильтр партий: если все включены или фича скрыта - не передаём ограничения
        if (self._ui_features.get("batches_filter", True)
                and self.all_batches
                and len(self.active_batches) < len(self.all_batches)):
            batch_filter = set(self.active_batches)
        else:
            batch_filter = None

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
            case_sensitive,
            batch_filter,
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

    # ----- Закладки -----

    def _toggle_bookmark_current(self):
        """Ctrl+B: ставит/снимает закладку на текущей строке."""
        current = self.log_view.currentIndex()
        if not current.isValid():
            return
        real_index = self.model.get_real_index(current.row())
        if real_index is None:
            return
        self.model.toggle_bookmark(real_index)

    def _goto_bookmark(self, direction):
        """F2 / Shift+F2: следующая / предыдущая закладка относительно текущей строки.
        Закольцовано. Учитываются только закладки, строки которых видны после фильтра."""
        bookmarks = self.model.get_bookmarks_sorted()
        if not bookmarks:
            return
        # Превращаем real_indices закладок в видимые row через find_row_by_real_index
        visible_rows = sorted(
            r for r in (self.model.find_row_by_real_index(b) for b in bookmarks) if r != -1
        )
        if not visible_rows:
            return

        current = self.log_view.currentIndex()
        cur_row = current.row() if current.isValid() else -1

        if direction > 0:
            # Следующая закладка > cur_row, иначе первая
            next_row = next((r for r in visible_rows if r > cur_row), visible_rows[0])
        else:
            # Предыдущая < cur_row, иначе последняя
            next_row = next((r for r in reversed(visible_rows) if r < cur_row), visible_rows[-1])

        idx = self.model.index(next_row)
        self.log_view.setCurrentIndex(idx)
        self.log_view.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.log_view.setFocus()

    def _update_scrollbar_markers(self):
        """Пересчитывает метки ERROR/WARN на вертикальном скроллбаре после фильтрации.
        Биннинг по ~200 ячеек, чтобы не плодить тысячи перекрывающихся отметок."""
        scrollbar = self.log_view.verticalScrollBar()
        if not isinstance(scrollbar, MarkerScrollBar):
            return

        indices = self.model._filtered_indices
        total = len(indices)
        if total == 0:
            scrollbar.set_markers([])
            return

        BINS = 200
        bin_levels = [None] * BINS  # для каждого бина "наиболее серьёзный" уровень
        entries = self.model._entries

        for row, real_idx in enumerate(indices):
            level = entries[real_idx].level
            if level not in ("ERROR", "WARN"):
                continue
            bin_idx = min(BINS - 1, row * BINS // total)
            if level == "ERROR":
                bin_levels[bin_idx] = "ERROR"
            elif bin_levels[bin_idx] != "ERROR":
                bin_levels[bin_idx] = "WARN"

        t = THEMES[self.current_theme_name]
        error_color = QColor(t['error'])
        warn_color = QColor(t['warn'])

        markers = []
        for i, lvl in enumerate(bin_levels):
            if lvl is None:
                continue
            rel = i / BINS
            markers.append((rel, error_color if lvl == "ERROR" else warn_color))

        scrollbar.set_markers(markers)

    def on_filter_finished_scroll(self):
        if self.preserved_real_index is not None:
            new_row = self.model.find_row_by_real_index(self.preserved_real_index)
            if new_row != -1:
                new_index = self.model.index(new_row)
                self.log_view.setCurrentIndex(new_index)
                self.log_view.scrollTo(new_index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def on_selection_changed(self, selected, deselected):
        self._refresh_details_view()

    def _refresh_details_view(self):
        """Перестраивает содержимое окна 'Выделение' на основе текущего выделения.
        Учитывает кнопку 'Форматировать JSON'."""
        selected_indexes = self.log_view.selectedIndexes()
        if not selected_indexes:
            self.details_view.clear()
            self.details_view.setExtraSelections([])
            self._update_selection_info([])
            return
        selected_indexes.sort(key=lambda x: x.row())
        display_indexes = selected_indexes[:50]
        # Форматировать JSON только если фича включена в настройках И кнопка нажата
        format_json = (
            self._ui_features.get("json_format", True) and self.btn_format_json.isChecked()
        )
        full_text = ""
        for idx in display_indexes:
            text = self.model.data(idx, Qt.ItemDataRole.UserRole)
            if format_json:
                text = self._prettify_json_in_text(text)
            full_text += text + "\n" + "=" * 80 + "\n"
        if len(selected_indexes) > 50:
            full_text += f"\n... и ещё {len(selected_indexes) - 50} выделенных строк не показано."
        self.details_view.setPlainText(full_text)
        self._highlight_search_matches()
        self._update_selection_info(selected_indexes)

    def _update_selection_info(self, selected_indexes):
        """Показывает в правой части статус-бара информацию о выделении:
        - одна строка: '#N | время HH:MM:SS.mmm | Δt = +Xms от пред. с другим временем'.
          Если есть подряд видимые строки с тем же timestamp - они НЕ считаются "пред.",
          иначе Δt всегда был бы 0ms на цепочках логов внутри одного миллисекунда.
        - несколько строк: длительность диапазона от первой выделенной до последней."""
        if not selected_indexes:
            self.lbl_selection_info.setText("")
            return

        entries = self.model._entries

        # Множественное выделение: показываем длительность диапазона
        if len(selected_indexes) > 1:
            first_real = self.model.get_real_index(selected_indexes[0].row())
            last_real = self.model.get_real_index(selected_indexes[-1].row())
            text = f"Выделено строк: {len(selected_indexes)}"
            if first_real is not None and last_real is not None:
                first_ts = entries[first_real].timestamp
                last_ts = entries[last_real].timestamp
                if first_ts and last_ts:
                    delta = self._timestamp_delta_ms(first_ts, last_ts)
                    if delta is not None:
                        text += (
                            f" | диапазон {first_ts} → {last_ts}"
                            f" | Δt = {self._format_delta_ms(delta)}"
                        )
            self.lbl_selection_info.setText(text)
            return

        # Одна строка
        idx = selected_indexes[0]
        row = idx.row()
        real_index = self.model.get_real_index(row)
        if real_index is None or not (0 <= real_index < len(entries)):
            self.lbl_selection_info.setText("")
            return
        entry = entries[real_index]

        parts = [f"Строка #{real_index + 1}"]
        if entry.timestamp:
            parts.append(f"время {entry.timestamp}")
            # Идём вверх по ВИДИМЫМ строкам, пропуская такие же timestamp -
            # ищем первую с ДРУГИМ временем (в одном миллисекунде может быть много строк).
            prev_ts = None
            same_ts_count = 0
            for prev_row in range(row - 1, -1, -1):
                prev_real = self.model.get_real_index(prev_row)
                if prev_real is None:
                    continue
                prev_e = entries[prev_real]
                if not prev_e.timestamp:
                    continue
                if prev_e.timestamp == entry.timestamp:
                    same_ts_count += 1
                    continue
                prev_ts = prev_e.timestamp
                break

            if prev_ts:
                delta_ms = self._timestamp_delta_ms(prev_ts, entry.timestamp)
                if delta_ms is not None:
                    label = f"Δt = {self._format_delta_ms(delta_ms)} от {prev_ts}"
                    if same_ts_count > 0:
                        label += f" (через {same_ts_count} с тем же временем)"
                    parts.append(label)
            elif same_ts_count > 0:
                parts.append(
                    f"Δt = 0ms (в этот же миллисекунду уже было {same_ts_count} строк выше)"
                )
            else:
                parts.append("Δt = — (первая видимая с временем)")

        self.lbl_selection_info.setText(" | ".join(parts))

    @staticmethod
    def _timestamp_delta_ms(ts_from, ts_to):
        """ts в формате HH:MM:SS.mmm. Возвращает разницу в миллисекундах (может быть отрицательной)."""
        try:
            def to_ms(ts):
                h, m, s_ms = ts.split(':')
                s, ms = s_ms.split('.')
                return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms))
            return to_ms(ts_to) - to_ms(ts_from)
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _format_delta_ms(ms):
        """Форматирует мс в человеко-читаемый вид: +15ms / +1.2s / +3m / +1.05h."""
        sign = '+' if ms >= 0 else '-'
        a = abs(ms)
        if a < 1000:
            return f"{sign}{a}ms"
        if a < 60_000:
            return f"{sign}{a/1000:.2f}s"
        if a < 3_600_000:
            return f"{sign}{a/60_000:.1f}m"
        return f"{sign}{a/3_600_000:.2f}h"

    @staticmethod
    def _prettify_json_in_text(text):
        """В каждой строке ищет JSON-фрагмент (объект или массив) и заменяет на
        форматированный с отступами. Перебирает ВСЕ позиции '{' и '[' пока не найдёт
        валидный JSON - нужно потому что в типичной строке лога много '['
        (`[INFO]`, `[Logger]`), и наивный поиск первого `[` всегда падает."""
        result_lines = []
        decoder = json.JSONDecoder()
        for line in text.split('\n'):
            # Все позиции { и [ слева направо
            positions = [i for i, ch in enumerate(line) if ch in '{[']
            rebuilt = None
            for pos in positions:
                try:
                    obj, end = decoder.raw_decode(line[pos:])
                except (json.JSONDecodeError, ValueError):
                    continue
                # Принимаем только объекты и массивы - чтобы не "форматировать"
                # случайные числа/строки/true/null которые тоже валидный JSON
                if not isinstance(obj, (dict, list)):
                    continue
                # Желательно нетривиальный размер - иначе [INFO] (если когда-нибудь
                # станет валидным JSON-массивом) тоже пойдёт в форматирование
                if isinstance(obj, dict) and len(obj) == 0:
                    continue
                if isinstance(obj, list) and len(obj) == 0:
                    continue
                pretty = json.dumps(obj, indent=2, ensure_ascii=False)
                rebuilt = line[:pos] + pretty + line[pos + end:]
                break
            result_lines.append(rebuilt if rebuilt is not None else line)
        return '\n'.join(result_lines)

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

        # Совпадения ищем так же, как FilterWorker: сначала regex, потом literal fallback.
        # Match case учитываем согласно состоянию кнопки "Aa".
        case_sensitive = self.btn_match_case.isChecked()
        positions = []
        try:
            pattern = re.compile(search_text, 0 if case_sensitive else re.IGNORECASE)
            for m in pattern.finditer(full_text):
                if m.start() != m.end():
                    positions.append((m.start(), m.end()))
                if len(positions) >= self.MAX_DETAIL_HIGHLIGHTS:
                    break
        except re.error:
            slen = len(search_text)
            if case_sensitive:
                pos = full_text.find(search_text)
                while pos != -1 and len(positions) < self.MAX_DETAIL_HIGHLIGHTS:
                    positions.append((pos, pos + slen))
                    pos = full_text.find(search_text, pos + slen)
            else:
                search_lower = search_text.lower()
                text_lower = full_text.lower()
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