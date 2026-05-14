import os
import re
import json
from datetime import datetime
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QSplitter, QAbstractItemView,
                             QMessageBox, QApplication, QLineEdit, QHBoxLayout, QLabel, QFrame,
                             QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QMenu,
                             QTreeWidgetItemIterator, QTextEdit, QToolButton, QCheckBox,
                             QWidgetAction, QScrollArea, QStackedWidget, QPlainTextEdit)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QKeySequence, QTextCursor, QTextCharFormat, QColor, QShortcut

from core.models import LogModel
from core.workers import LogLoader, IncrementalLogParser
from gui.custom_widgets import (ScalableListView, MarkerScrollBar,
                                FoldableJsonTextEdit)
from gui.batch_analysis_dialog import BatchAnalysisDialog
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
    # Сколько строк показывать под каждой партией в дереве (lazy)
    MAX_BATCH_TREE_ROWS = 1000

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
            self._ui_features.update({k: bool(v)
                                     for k, v in ui_features.items() if k in DEFAULT_UI_FEATURES})

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
        self._is_archive = file_path.lower().endswith(('.gz', '.zip', '.7z', '.rar'))

        # Палитра JSON-токенов и дерева - наполняется в apply_theme.
        # Инициализируем пустым словарём, чтобы _populate_json_tree до первого
        # apply_theme не падал.
        self._json_palette = {}

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
        # Встроенный крестик очистки справа от текста (стандартная Qt-фича).
        self.search_input.setClearButtonEnabled(True)
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

        # Regex (.*) - по умолчанию OFF. Иначе любое '?' '(' '+' '*' в запросе
        # трактовалось как regex, и поиск буквальных URL вида /api/...?id=N
        # ничего не находил.
        self.btn_use_regex = QToolButton()
        self.btn_use_regex.setText(".*")
        self.btn_use_regex.setCheckable(True)
        self.btn_use_regex.setStyleSheet(self.btn_match_case.styleSheet())
        self._update_use_regex_tooltip()
        self.btn_use_regex.toggled.connect(self._on_use_regex_toggled)
        search_layout.addWidget(self.btn_use_regex)

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
            self.btn_follow.setToolTip("Tail-режим недоступен для архивов (.gz, .zip, .7z, .rar)")
        self.btn_follow.toggled.connect(self._on_follow_toggled)
        search_layout.addWidget(self.btn_follow)

        # "Добавить в журнал" живёт теперь на главном тулбаре MainWindow
        # (общая кнопка для активного вьювера); см. MainWindow.btn_save_search.
        # Локальную кнопку убрали - меньше шума в строке поиска.

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

        # Контейнер для кнопок в углу tab-бара (setCornerWidget берёт ровно один виджет)
        corner_widget = QWidget()
        corner_layout = QHBoxLayout(corner_widget)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(4)

        toolbtn_qss = """
            QToolButton { padding: 3px 10px; border: 1px solid #888; border-radius: 3px; }
            QToolButton:hover { border-color: #bbb; }
            QToolButton:checked {
                background-color: #2A82DA; color: #FFFFFF; border: 1px solid #1858A0;
            }
        """

        # Кнопка-toggle "Форматировать JSON" - показывает JSON в виде текста с отступами
        self.btn_format_json = QToolButton()
        self.btn_format_json.setText("{ } JSON")
        self.btn_format_json.setCheckable(True)
        self.btn_format_json.setStyleSheet(toolbtn_qss)
        self.btn_format_json.setToolTip(
            "Форматировать JSON-фрагменты в окне 'Выделение' с отступами.\n"
            "Полезно для длинных HTTP-боди и подписанных запросов."
        )
        self.btn_format_json.toggled.connect(self._on_json_format_toggled)
        corner_layout.addWidget(self.btn_format_json)

        # Кнопка-toggle "Дерево" - показывает JSON как сворачиваемое дерево (как в Notepad++)
        self.btn_json_tree = QToolButton()
        self.btn_json_tree.setText("▶ Дерево")
        self.btn_json_tree.setCheckable(True)
        self.btn_json_tree.setStyleSheet(toolbtn_qss)
        self.btn_json_tree.setToolTip(
            "Показать JSON-фрагмент из выделенной строки как сворачиваемое дерево.\n"
            "Двойной клик по узлу - свернуть/развернуть."
        )
        self.btn_json_tree.toggled.connect(self._on_json_tree_toggled)
        corner_layout.addWidget(self.btn_json_tree)

        # Кнопка-toggle "Перенос" - перенос длинных строк в окне 'Выделение' по
        # ширине виджета. Не зависит от JSON/Дерева.
        self.btn_word_wrap = QToolButton()
        self.btn_word_wrap.setText("↵ Перенос")
        self.btn_word_wrap.setCheckable(True)
        self.btn_word_wrap.setStyleSheet(toolbtn_qss)
        self.btn_word_wrap.setToolTip(
            "Переносить длинные строки в окне 'Выделение' по ширине окна.\n"
            "Удобно когда строка лога не помещается в виджет."
        )
        self.btn_word_wrap.toggled.connect(self._on_word_wrap_toggled)
        corner_layout.addWidget(self.btn_word_wrap)

        self.bottom_tabs.setCornerWidget(corner_widget, Qt.Corner.TopRightCorner)

        # Details: stack из текстового вида и древовидного
        self.details_stack = QStackedWidget()

        self.details_view = FoldableJsonTextEdit()
        self.details_stack.addWidget(self.details_view)

        self.details_tree = QTreeWidget()
        self.details_tree.setHeaderLabels(["Ключ", "Значение"])
        self.details_tree.setColumnWidth(0, 280)
        self.details_tree.setAlternatingRowColors(True)
        self.details_tree.setUniformRowHeights(True)
        self.details_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.details_tree.customContextMenuRequested.connect(self._show_details_tree_menu)
        self.details_stack.addWidget(self.details_tree)

        self.bottom_tabs.addTab(self.details_stack, "Выделение")

        # Search Journal Tree
        self.search_journal_tree = QTreeWidget()
        self.search_journal_tree.setHeaderHidden(True)
        # Включаем множественное выделение (с Shift / Ctrl)
        self.search_journal_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        # Подключаем кастомное контекстное меню
        self.search_journal_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.search_journal_tree.customContextMenuRequested.connect(self.show_journal_context_menu)
        self.search_journal_tree.itemDoubleClicked.connect(self.on_journal_item_double_clicked)
        self.bottom_tabs.addTab(self.search_journal_tree, "Поиск")

        # Batches Tree - дерево партий с lazy-load дочерних строк
        self.batches_tree = QTreeWidget()
        self.batches_tree.setHeaderHidden(True)
        self.batches_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.batches_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.batches_tree.customContextMenuRequested.connect(self._show_batches_context_menu)
        self.batches_tree.itemDoubleClicked.connect(self._on_batches_tree_item_double_clicked)
        self.batches_tree.itemExpanded.connect(self._on_batches_tree_item_expanded)

        # Контейнер с двумя подвкладками: "Все события" (дерево партий) и
        # "История кода" (дерево Партия→Код→События).
        self.batches_container = QTabWidget()
        self.batches_container.setTabPosition(QTabWidget.TabPosition.North)
        self.batches_container.addTab(self.batches_tree, "Все события")
        self.code_history_widget = self._build_code_history_widget()
        self.batches_container.addTab(self.code_history_widget, "История кода")
        # При первом открытии подвкладки "История кода" строим индекс лениво
        self.batches_container.currentChanged.connect(self._on_batches_container_changed)

        self.bottom_tabs.addTab(self.batches_container, "Партии")

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
        # Индекс кодов устарел - перестроится при следующем открытии вкладки
        self._invalidate_code_history()

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

        # Индекс кодов устарел (появились новые строки) - перестроим при следующем открытии вкладки
        self._invalidate_code_history()

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
        """Подбирает summary партий из модели и пересобирает меню + дерево фильтра."""
        self.all_batches = self.model.get_batch_summary()
        self.active_batches = {bid for (bid, _c, _f, _l) in self.all_batches}
        self._rebuild_batches_menu()
        self._rebuild_batches_tree()

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

    # ----- Дерево партий во вкладке "Партии" -----

    @staticmethod
    def _classify_for_stats(line):
        """Считаем по тем же сигналам, которые показывает отчёт smartl2 при
        остановке сериализации (Напечатано / Прочитано / NoRead / Верифицировано /
        Отбраковано). Возвращает ключ счётчика или None."""
        # Статус кода → Printed = напечатан, → PrintConfirmed = верифицирован.
        # Прямые строки smartl2 - самый надёжный источник.
        if 'изменён на Printed' in line or 'изменен на Printed' in line:
            return 'printed'
        if ('изменён на PrintConfirmed' in line
                or 'изменен на PrintConfirmed' in line
                or 'изменён на Verified' in line):
            return 'verified'
        if 'изменён на Rejected' in line or 'изменен на Rejected' in line:
            return 'rejected'
        # Hikrobot события
        if 'Hikrobot получены данные' in line:
            return 'scanned'
        lower = line.lower()
        if 'hikrobot noread' in lower or 'noread' in lower or 'не прочитан камерой' in lower:
            return 'noread'
        if 'отбракован' in lower or 'rejection' in lower:
            return 'rejected'
        if 'не верифицирован' in lower:
            return 'not_verified'
        return None

    def _ensure_batches_stats(self):
        """Считает {bid: {printed, scanned, noread, verified, rejected, not_verified}}
        один раз. Линейный проход по entries с быстрой классификацией. Кэш
        инвалидируется в _invalidate_code_history (при reload / tail-append)."""
        if getattr(self, '_batches_stats', None) is not None:
            return self._batches_stats

        from core.models import NO_BATCH
        entries = self.model._entries
        bfi = self.model._batch_for_index
        keys = ('printed', 'scanned', 'noread',
                'verified', 'rejected', 'not_verified')

        stats = {}
        for i, e in enumerate(entries):
            key = self._classify_for_stats(e.full_line)
            if not key:
                continue
            bid = bfi[i] if i < len(bfi) else NO_BATCH
            bucket = stats.get(bid)
            if bucket is None:
                bucket = {k: 0 for k in keys}
                stats[bid] = bucket
            bucket[key] += 1

        self._batches_stats = stats
        return stats

    def _add_stats_subnode(self, parent_item, bid):
        """Добавляет под parent_item раскрываемый узел '📊 Статистика партии'
        со счётчиками. Применяется и в Все события, и в Истории кода."""
        stats = self._ensure_batches_stats().get(bid)
        if not stats:
            return None
        printed = stats.get('printed', 0)
        scanned = stats.get('scanned', 0)
        noread = stats.get('noread', 0)
        verified = stats.get('verified', 0)
        rejected = stats.get('rejected', 0)
        not_verified = stats.get('not_verified', 0)
        # Если в логе нет явных "Не верифицирован" - считаем как printed-verified
        not_verified_calc = max(0, printed - verified)
        if not_verified == 0 and not_verified_calc > 0:
            not_verified = not_verified_calc

        node = QTreeWidgetItem(parent_item, ["📊 Статистика партии"])
        node.setData(0, Qt.ItemDataRole.UserRole, ('stats', None))

        t = THEMES.get(self.current_theme_name, {})
        info = QColor(t.get('info', '#2E8B57'))
        warn = QColor(t.get('warn', '#FFA500'))
        error = QColor(t.get('error', '#CD5C5C'))
        muted = QColor(t.get('text_muted', '#999999'))

        rows = [
            ("Напечатано", printed, info if printed else muted),
            ("Прочитано", scanned, info if scanned else muted),
            ("No read", noread, warn if noread else muted),
            ("Верифицировано", verified, info if verified else muted),
            ("Отбраковано", rejected, error if rejected else muted),
            ("Не верифицировано", not_verified, warn if not_verified else muted),
        ]
        for label, n, color in rows:
            ci = QTreeWidgetItem(node, [f"{label}: {n:,}"])
            ci.setData(0, Qt.ItemDataRole.UserRole, ('stat_row', None))
            ci.setForeground(0, color)
        return node

    def _rebuild_batches_tree(self):
        """Перестраивает дерево партий: заголовки + узел статистики; строки lazy."""
        self.batches_tree.clear()
        if not self.all_batches:
            return

        self.batches_tree.setUpdatesEnabled(False)
        try:
            for bid, count, first_ts, last_ts in self.all_batches:
                if not bid:
                    label = f"Вне партии  —  {count:,} строк  ({first_ts} → {last_ts})"
                else:
                    label = f"Партия {bid}  —  {count:,} строк  ({first_ts} → {last_ts})"
                root = QTreeWidgetItem(self.batches_tree, [label])
                root.setData(0, Qt.ItemDataRole.UserRole, ('batch', bid))
                # Сводка по партии - первым дочерним узлом
                self._add_stats_subnode(root, bid)
                # Фейк-чайлд для появления треугольника раскрытия. Заменим на
                # реальные строки при expand. Статистика остаётся.
                if count > 0:
                    placeholder = QTreeWidgetItem(root, ["…"])
                    placeholder.setData(0, Qt.ItemDataRole.UserRole, ('placeholder', None))
        finally:
            self.batches_tree.setUpdatesEnabled(True)

    def _on_batches_tree_item_expanded(self, item):
        """Lazy-load: заполняем строки партии только когда юзер раскрывает её узел.
        Узел статистики и его дети остаются на месте, мы трогаем только placeholder."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != 'batch':
            return
        # Ищем placeholder среди детей (он один если ещё не подгружали строки).
        placeholder_idx = -1
        for ci in range(item.childCount()):
            ch = item.child(ci)
            cd = ch.data(0, Qt.ItemDataRole.UserRole)
            if cd and cd[0] == 'placeholder':
                placeholder_idx = ci
                break
        if placeholder_idx == -1:
            return  # уже подгрузили

        item.takeChild(placeholder_idx)

        bid = data[1]
        entries = self.model._entries
        bfi = self.model._batch_for_index
        max_rows = self.MAX_BATCH_TREE_ROWS
        max_preview = self.MAX_JOURNAL_LINE_PREVIEW

        new_items = []
        total_in_batch = 0
        for ri in range(len(bfi)):
            if bfi[ri] != bid:
                continue
            total_in_batch += 1
            if len(new_items) >= max_rows:
                continue  # дальше только считаем для финальной пометки
            e = entries[ri]
            line = e.full_line.strip()
            if len(line) > max_preview:
                line = line[:max_preview] + '...'
            text = f"Строка {ri + 1}: {line}"
            ci = QTreeWidgetItem([text])
            ci.setData(0, Qt.ItemDataRole.UserRole, ('row', ri))
            new_items.append(ci)

        self.batches_tree.setUpdatesEnabled(False)
        try:
            item.addChildren(new_items)
            if total_in_batch > max_rows:
                hidden = total_in_batch - max_rows
                note = QTreeWidgetItem(
                    item, [
                        f"… ещё {
                            hidden:,} строк не показано (лимит {max_rows} для производительности)"])
                note.setData(0, Qt.ItemDataRole.UserRole, ('note', None))
        finally:
            self.batches_tree.setUpdatesEnabled(True)

    def _on_batches_tree_item_double_clicked(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, payload = data
        if kind == 'batch':
            # Двойной клик по партии = "показать только эту партию"
            self._filter_to_single_batch(payload)
        elif kind == 'row':
            # Двойной клик по строке = переход к ней (как в журнале поиска)
            real_index = payload
            row = self.model.find_row_by_real_index(real_index)
            if row != -1:
                idx = self.model.index(row)
                self.log_view.setCurrentIndex(idx)
                self.log_view.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
                self.log_view.setFocus()
            else:
                QMessageBox.information(
                    self, "Информация",
                    "Эта строка скрыта текущими фильтрами (уровни / поиск / время).\n"
                    "Очистите фильтры или включите соответствующую партию для перехода к ней."
                )

    def _filter_to_single_batch(self, bid):
        """Оставляет видимой только указанную партию. Синхронизирует чекбоксы меню."""
        self.active_batches = {bid}
        for b, cb in self.batch_checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(b == bid)
            cb.blockSignals(False)
        self._update_batches_button_label()
        self.refresh_view()

    def _show_batches_context_menu(self, pos):
        item = self.batches_tree.itemAt(pos)
        menu = QMenu(self)
        act_only = None
        act_analyze = None
        act_show_all = menu.addAction("Показать все партии")
        if item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == 'batch':
                act_only = menu.addAction("Показать только эту партию")
                menu.addSeparator()
                act_analyze = menu.addAction("📊 Анализ партии…")
        menu.addSeparator()
        act_expand_all = menu.addAction("Раскрыть всё")
        act_collapse_all = menu.addAction("Свернуть всё")

        action = menu.exec(self.batches_tree.mapToGlobal(pos))
        if action is None:
            return
        if action == act_show_all:
            self.active_batches = {bid for (bid, _c, _f, _l) in self.all_batches}
            for b, cb in self.batch_checkboxes.items():
                cb.blockSignals(True)
                cb.setChecked(True)
                cb.blockSignals(False)
            self._update_batches_button_label()
            self.refresh_view()
        elif action == act_only and item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == 'batch':
                self._filter_to_single_batch(data[1])
        elif action == act_analyze and item is not None:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == 'batch':
                self._open_batch_analysis(data[1])
        elif action == act_expand_all:
            self.batches_tree.expandAll()
        elif action == act_collapse_all:
            self.batches_tree.collapseAll()

    # ----- Подвкладка "История кода" -----

    # Лимит кодов на партию в дереве (на больших логах это десятки тысяч);
    # сверх лимита выводим пометку "ещё N кодов".
    MAX_CODES_PER_BATCH = 5000
    # Регекс SSCC (палеты/короба): 18 цифр после префикса 00.
    _SSCC_RE = re.compile(r'\b00\d{18}\b')

    def _build_code_history_widget(self):
        """Виджет под вкладкой Партии: дерево Партия → Код → События.
        Поле поиска фильтрует видимость узлов кодов в дереве по подстроке."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(5, 5, 5, 5)
        v.setSpacing(5)

        # Строка поиска (live-фильтр дерева)
        row = QHBoxLayout()
        row.addWidget(QLabel("Поиск кода:"))
        self.code_history_input = QLineEdit()
        self.code_history_input.setPlaceholderText(
            "Часть кода — фильтрует дерево по мере ввода. Пусто — все коды.")
        self.code_history_input.textChanged.connect(self._filter_code_history_tree)
        row.addWidget(self.code_history_input, 1)

        btn_clear = QPushButton("×")
        btn_clear.setToolTip("Очистить поиск")
        btn_clear.setFixedWidth(28)
        btn_clear.clicked.connect(self.code_history_input.clear)
        row.addWidget(btn_clear)

        btn_expand = QPushButton("Раскрыть видимые")
        btn_expand.clicked.connect(self._expand_visible_code_history_codes)
        row.addWidget(btn_expand)

        v.addLayout(row)

        # Дерево Партия → Код → События
        self.code_history_tree = QTreeWidget()
        self.code_history_tree.setHeaderLabels(["Партия / Код / Время", "Событие", "Сообщение"])
        self.code_history_tree.setColumnWidth(0, 320)
        self.code_history_tree.setColumnWidth(1, 220)
        self.code_history_tree.setAlternatingRowColors(True)
        self.code_history_tree.itemDoubleClicked.connect(
            self._on_code_history_item_double_clicked)
        self.code_history_tree.itemExpanded.connect(
            self._on_code_history_tree_item_expanded)
        v.addWidget(self.code_history_tree, 1)

        # Статус-строка
        self.code_history_status = QLabel("Откройте вкладку, чтобы построить дерево кодов.")
        v.addWidget(self.code_history_status)

        # Индекс {batch_id: {code: [(real_index, event_label, kind), ...]}}.
        # Строится лениво при первом открытии вкладки; инвалидируется при
        # перезагрузке файла / tail-append.
        self._code_history_index = None
        # Флаг "дерево уже построено для текущего индекса"
        self._code_history_tree_built = False
        # Кэш счётчиков по партиям {bid: {printed, scanned, ...}}.
        # Используется и Все события, и История кода; пересчитывается лениво.
        self._batches_stats = None

        return w

    def _invalidate_code_history(self):
        """Сбрасывает индекс и помечает дерево как устаревшее (надо перепостроить).
        Вызывается при on_load_finished и при append_entries (tail).
        Заодно сбрасывает кэш статистики по партиям."""
        self._code_history_index = None
        self._code_history_tree_built = False
        self._batches_stats = None  # кэш статистики тоже устарел
        # Чистим визуально, чтобы старые ссылки на real_index не указывали в никуда
        if hasattr(self, 'code_history_tree'):
            self.code_history_tree.clear()
            self.code_history_status.setText(
                "Дерево устарело — переключитесь на вкладку, чтобы перестроить.")

    def _on_batches_container_changed(self, index):
        """Подвкладка переключилась. Если открыли «История кода» и дерево
        не построено — строим лениво."""
        if index < 0:
            return
        w = self.batches_container.widget(index)
        if w is self.code_history_widget and not self._code_history_tree_built:
            self._build_code_history_index()
            self._populate_code_history_tree()
            self._code_history_tree_built = True

    def _build_code_history_index(self):
        """Сканирует все entries, извлекает коды (SGTIN / групповой / SSCC)
        и собирает индекс {batch_id: {code: [(real_index, label, kind)]}}.
        Тяжёлая операция - вызывать только при первом обращении / после reload."""
        from core.models import SGTIN_CODE_RE, GROUP_CODE_RE, NO_BATCH

        entries = self.model._entries
        bfi = self.model._batch_for_index

        index = {}
        sgtin_find = SGTIN_CODE_RE.findall
        group_find = GROUP_CODE_RE.findall
        sscc_find = self._SSCC_RE.findall

        for i, e in enumerate(entries):
            line = e.full_line
            codes = set()
            codes.update(sgtin_find(line))
            codes.update(group_find(line))
            codes.update(sscc_find(line))
            if not codes:
                continue
            bid = bfi[i] if i < len(bfi) else NO_BATCH
            bucket = index.setdefault(bid, {})
            label, kind = self._classify_event_for_code(e, line)
            for c in codes:
                bucket.setdefault(c, []).append((i, label, kind))

        self._code_history_index = index

    def _populate_code_history_tree(self):
        """Наполняет дерево из готового индекса. Партии сортируются по
        first_ts из get_batch_summary(); коды внутри партии - по индексу
        первого упоминания (=хронологически). События загружаются лениво."""
        from core.models import NO_BATCH

        self.code_history_tree.clear()
        index = self._code_history_index or {}
        if not index:
            self.code_history_status.setText("Кодов в логе не обнаружено.")
            return

        # Партии в хронологическом порядке - get_batch_summary уже сортирует по first_ts.
        batches_summary = self.model.get_batch_summary()
        # На случай если в индексе есть batch_id, которого нет в summary
        # (теоретически не должно случаться) - подмешаем их в конец.
        known = {b[0] for b in batches_summary}
        extra = [(bid, 0, "", "") for bid in index.keys() if bid not in known]
        ordered = list(batches_summary) + extra

        total_codes = 0
        self.code_history_tree.setUpdatesEnabled(False)
        try:
            for bid, _count, _f, _l in ordered:
                bucket = index.get(bid)
                if not bucket:
                    continue
                if bid == NO_BATCH:
                    bid_label = f"Вне партии  —  {len(bucket):,} кодов"
                else:
                    bid_label = f"Партия {bid}  —  {len(bucket):,} кодов"
                b_item = QTreeWidgetItem(self.code_history_tree, [bid_label, "", ""])
                b_item.setData(0, Qt.ItemDataRole.UserRole, ('batch', bid))
                # Сводка по партии - первым дочерним узлом
                self._add_stats_subnode(b_item, bid)
                total_codes += len(bucket)

                # Коды в хронологическом порядке (по первому индексу)
                sorted_codes = sorted(
                    bucket.items(), key=lambda kv: kv[1][0][0]
                )[:self.MAX_CODES_PER_BATCH]

                for code, events in sorted_codes:
                    c_label = f"{code}   ({len(events)} событий)"
                    c_item = QTreeWidgetItem(b_item, [c_label, "", ""])
                    c_item.setData(0, Qt.ItemDataRole.UserRole, ('code', code, bid))
                    # Placeholder - реальные события подгрузим при раскрытии
                    placeholder = QTreeWidgetItem(c_item, ["…", "", ""])
                    placeholder.setData(0, Qt.ItemDataRole.UserRole,
                                        ('placeholder', None))

                if len(bucket) > self.MAX_CODES_PER_BATCH:
                    hidden = len(bucket) - self.MAX_CODES_PER_BATCH
                    note = QTreeWidgetItem(b_item, [
                        f"… ещё {hidden:,} кодов не показано (лимит {self.MAX_CODES_PER_BATCH})",
                        "", ""
                    ])
                    note.setData(0, Qt.ItemDataRole.UserRole, ('note', None))
        finally:
            self.code_history_tree.setUpdatesEnabled(True)

        self.code_history_status.setText(
            f"Партий: {len(index):,}, всего кодов: {total_codes:,}"
        )

    def _on_code_history_tree_item_expanded(self, item):
        """Lazy-load: при раскрытии узла кода подгружаем его события."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != 'code':
            return
        if item.childCount() != 1:
            return  # уже наполнено
        first = item.child(0)
        fd = first.data(0, Qt.ItemDataRole.UserRole)
        if not fd or fd[0] != 'placeholder':
            return

        _, code, bid = data
        bucket = (self._code_history_index or {}).get(bid, {})
        events = bucket.get(code, [])

        t = THEMES.get(self.current_theme_name, {})
        color_for_kind = {
            'info': QColor(t.get('info', '#2E8B57')),
            'debug': QColor(t.get('debug', '#4682B4')),
            'warn': QColor(t.get('warn', '#FFA500')),
            'error': QColor(t.get('error', '#CD5C5C')),
        }

        entries = self.model._entries
        new_children = []
        for (idx, ev_label, kind) in events:
            e = entries[idx]
            ts = e.timestamp or ""
            msg = e.message.split('\n', 1)[0]
            if len(msg) > 300:
                msg = msg[:300] + "..."
            ev_item = QTreeWidgetItem([ts, ev_label, msg])
            ev_item.setData(0, Qt.ItemDataRole.UserRole, ('event', idx))
            color = color_for_kind.get(kind)
            if color is not None:
                ev_item.setForeground(1, color)
            new_children.append(ev_item)

        item.takeChildren()
        item.addChildren(new_children)

    def _filter_code_history_tree(self, text):
        """Live-фильтр по подстроке кода. Пустая строка - показываем всё.
        Узел статистики партии остаётся видим в любом случае."""
        needle = text.strip().lower()
        root = self.code_history_tree.invisibleRootItem()
        for i in range(root.childCount()):
            batch_item = root.child(i)
            visible_codes = 0
            for j in range(batch_item.childCount()):
                code_item = batch_item.child(j)
                data = code_item.data(0, Qt.ItemDataRole.UserRole)
                if data and data[0] == 'stats':
                    # Статистика партии видна всегда
                    code_item.setHidden(False)
                    continue
                if not data or data[0] != 'code':
                    # placeholder/note всегда скрываем при активном фильтре
                    code_item.setHidden(bool(needle))
                    continue
                code = data[1]
                visible = (not needle) or (needle in code.lower())
                code_item.setHidden(not visible)
                if visible:
                    visible_codes += 1
            # Партию скрываем если ни один её код не виден (но если активен фильтр)
            batch_item.setHidden(bool(needle) and visible_codes == 0)

    def _expand_visible_code_history_codes(self):
        """Раскрывает все видимые узлы кода. Полезно после фильтра по подстроке -
        когда осталось 5-10 кодов и хочется сразу увидеть их события."""
        root = self.code_history_tree.invisibleRootItem()
        for i in range(root.childCount()):
            batch_item = root.child(i)
            if batch_item.isHidden():
                continue
            batch_item.setExpanded(True)
            for j in range(batch_item.childCount()):
                code_item = batch_item.child(j)
                if code_item.isHidden():
                    continue
                data = code_item.data(0, Qt.ItemDataRole.UserRole)
                if data and data[0] == 'code':
                    code_item.setExpanded(True)

    @staticmethod
    def _classify_event_for_code(entry, line):
        """Классифицирует строку лога относительно кода - возвращает (label, kind),
        где kind in {'info','debug','warn','error'} для подсветки.

        Правила построены под smartl2-логи (печать / скан / агрегация / отбраковка)."""
        lvl = entry.level
        logger = entry.logger or ''
        lower = line.lower()

        # --- Печать ---
        if '.printAggregationCode' in line:
            return ("Напечатан", 'info')
        if logger == 'SATO' and '.sendCode' in line:
            return ("Отправлен на печать (SATO)", 'info')
        if logger == 'PrintService' and '.sendData' in line:
            return ("Отправлен на принтер", 'info')
        if '.getAndPrintAggregationCode' in line:
            return ("Запрос печати агрегата", 'info')
        if 'manageNextConfirmedPrint' in line:
            return ("Подтверждение печати", 'info')

        # --- Сканирование / верификация камерой ---
        if logger == 'HIKROBOT' and '.run' in line:
            if 'noread' in lower or 'не прочитан' in lower:
                return ("Не прочитан камерой", 'warn')
            return ("Считан камерой", 'info')
        if 'noread' in lower or 'не прочитан камерой' in lower:
            return ("NoRead", 'warn')
        if 'verif' in lower and 'не' not in lower[:max(0, lower.find('verif'))]:
            # 'verified' / 'verification' - аккуратно с "не верифицирован"
            if 'не верифиц' in lower:
                return ("Не верифицирован", 'warn')
            return ("Верифицирован", 'info')

        # --- Агрегация / разагрегация ---
        if '.finishAggregation' in line:
            return ("Агрегирован", 'info')
        if '.manageAggregationCode' in line:
            return ("Попытка агрегации", 'debug')
        if '.clearAggGroup' in line:
            return ("Группа очищена", 'warn')
        if '.manageFinishAggregationResponse' in line:
            return ("Ответ на завершение агрегации", 'debug')
        if 'дезагрегац' in lower or 'разагрегац' in lower or 'disaggregat' in lower:
            return ("Разагрегирован", 'warn')

        # --- Отбраковка / выбытие ---
        if 'отбракован' in lower or 'rejection' in lower or 'rejected' in lower:
            return ("Отбракован", 'error')
        if 'выбыл' in lower or 'utilizat' in lower or 'withdraw' in lower:
            return ("Выбыл", 'warn')

        # --- Дубли / ошибки кодов ---
        if 'уже находится в одной из агрегационных групп' in line:
            return ("Дубль (в другой группе)", 'error')
        if 'уже добавлен в агрегационную группу' in line:
            return ("Дубль (в текущей группе)", 'error')
        if 'не найден в базе' in line:
            return ("Не найден в базе", 'error')
        if 'процессор для заданного уровня не найден или занят' in line:
            return ("Процессор занят/не найден", 'error')

        # --- Обмен с Л2 / отчёты ---
        if 'exchangeSgtinEvents' in line:
            return ("Синхронизация с Л2", 'info')
        if 'отчет о нанесении' in lower or 'introduction' in lower:
            return ("Отчёт о нанесении", 'info')
        if 'отчет о вводе' in lower:
            return ("Отчёт о вводе в оборот", 'info')

        # --- По умолчанию: по уровню ---
        if lvl == 'ERROR':
            return ("Ошибка", 'error')
        if lvl == 'WARN':
            return ("Предупреждение", 'warn')
        if lvl == 'DEBUG':
            return ("Отладка", 'debug')
        return ("Событие", 'info')

    def _on_code_history_item_double_clicked(self, item, column):
        """Двойной клик по строке истории. Прыгаем к строке только если это узел
        конкретного события ('event'). Двойной клик по партии/коду - просто
        разворачивает/сворачивает узел (стандартное поведение QTreeWidget)."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) < 2 or data[0] != 'event':
            return
        real_index = data[1]
        row = self.model.find_row_by_real_index(real_index)
        if row != -1:
            idx = self.model.index(row)
            self.log_view.setCurrentIndex(idx)
            self.log_view.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
            self.log_view.setFocus()
        else:
            QMessageBox.information(
                self, "Информация",
                "Эта строка скрыта текущими фильтрами / выбором партии.\n"
                "Очистите фильтры для перехода к ней."
            )

    def _open_batch_analysis(self, batch_id):
        """Запускает подсчёт метрик и открывает диалог BatchAnalysisDialog."""
        analysis = self.model.analyze_batch(batch_id)
        dlg = BatchAnalysisDialog(analysis, self.current_theme_name, self)
        dlg.exec()

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
        self.details_tree.setFont(font)
        self.search_journal_tree.setFont(font)
        self.code_history_tree.setFont(font)

        # Палитра для gutter + JSON-подсветки + цветов в дереве: уезжает в
        # FoldableJsonTextEdit и используется в _add_json_field/_populate_json_tree.
        self._json_palette = t.get("json_palette", {})
        self.details_view.apply_theme_palette(self._json_palette)
        # Перерисовать дерево, если оно сейчас активно (цвета примитивов привязаны к палитре)
        if self.details_stack.currentIndex() == 1:
            self._refresh_details_view()

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
        self._ui_features.update({k: bool(v)
                                 for k, v in features.items() if k in self._ui_features})

        # Опциональные элементы поисковой панели
        self.btn_match_case.setVisible(self._ui_features["match_case"])
        self.btn_use_regex.setVisible(self._ui_features.get("use_regex", True))
        self.btn_loggers.setVisible(self._ui_features["loggers_filter"])
        self.btn_batches.setVisible(self._ui_features["batches_filter"])

        # Tab "Партии" - скрываем через тот же ui_features.batches_filter
        # Вкладка "Партии" теперь - контейнер с подвкладками ("Все события" + "История кода"),
        # поэтому ищем по контейнеру, а не по самому дереву.
        batches_tab_idx = self.bottom_tabs.indexOf(self.batches_container)
        if batches_tab_idx != -1:
            self.bottom_tabs.setTabVisible(batches_tab_idx, self._ui_features["batches_filter"])
        self.lbl_time.setVisible(self._ui_features["time_range"])
        self.time_from.setVisible(self._ui_features["time_range"])
        self.lbl_time_dash.setVisible(self._ui_features["time_range"])
        self.time_to.setVisible(self._ui_features["time_range"])
        self.btn_follow.setVisible(self._ui_features["tail_mode"])
        # "Добавить в журнал" теперь на главном тулбаре MainWindow,
        # видимостью управляет _apply_ui_features_everywhere там же.

        # Tail с скрытой кнопки нельзя контролировать - принудительно останавливаем
        if not self._ui_features["tail_mode"] and self.btn_follow.isChecked():
            self.btn_follow.setChecked(False)

        # Кнопки JSON в углу tab-бара (одна фича управляет обеими: формат + дерево)
        self.btn_format_json.setVisible(self._ui_features["json_format"])
        self.btn_json_tree.setVisible(self._ui_features["json_format"])
        if not self._ui_features["json_format"] and self.btn_json_tree.isChecked():
            self.btn_json_tree.setChecked(False)

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

    def _on_use_regex_toggled(self, _checked):
        self._update_use_regex_tooltip()
        self.search_timer.start()

    def _update_use_regex_tooltip(self):
        if self.btn_use_regex.isChecked():
            self.btn_use_regex.setToolTip(
                "Regex: ВКЛ — поисковая строка трактуется как регулярное выражение.\n"
                "Спецсимволы: . * + ? ( ) [ ] | ^ $ \\d \\w …"
            )
        else:
            self.btn_use_regex.setToolTip(
                "Regex: ВЫКЛ — поиск буквальный.\nСимволы вроде ? ( ) * + ищутся как есть.\n"
                "Нажмите чтобы включить regex."
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
                    file_item, [
                        f"... ещё {
                            match_count - max_per_search} совпадений не показано (для производительности)"])

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
        # QTreeWidgetItemIterator позволяет пройти по дереву сверху вниз и
        # сохранить правильный порядок
        iterator = QTreeWidgetItemIterator(
            self.search_journal_tree,
            QTreeWidgetItemIterator.IteratorFlag.Selected)
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
                QMessageBox.information(
                    self,
                    "Информация",
                    "Эта строка скрыта текущими фильтрами (INFO/DEBUG/WARN/ERROR) "
                    "или текстом поиска.\n\nОчистите фильтры для перехода к ней.")

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
        # Аналогично для regex: пользователь явно должен включить '.*',
        # иначе search_text трактуется буквально (любые ?, (, ), +, *).
        use_regex = (
            self._ui_features.get("use_regex", True) and self.btn_use_regex.isChecked()
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
            use_regex,
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

    def _on_json_format_toggled(self, checked):
        """{} JSON и Дерево взаимоисключающие - включение одного выключает другое.
        После того как выключили Дерево, обязательно переключаем stack обратно на
        текстовый view, иначе UI остался бы на дереве (старый баг)."""
        if checked and self.btn_json_tree.isChecked():
            self.btn_json_tree.blockSignals(True)
            self.btn_json_tree.setChecked(False)
            self.btn_json_tree.blockSignals(False)
        # Любое нажатие/отжатие "{ } JSON" возвращает stack на текстовую страницу
        self.details_stack.setCurrentIndex(0)
        self._refresh_details_view()

    def _on_json_tree_toggled(self, checked):
        if checked and self.btn_format_json.isChecked():
            self.btn_format_json.blockSignals(True)
            self.btn_format_json.setChecked(False)
            self.btn_format_json.blockSignals(False)
        # Переключаем страницу stack: 0 = текст, 1 = дерево
        self.details_stack.setCurrentIndex(1 if checked else 0)
        self._refresh_details_view()

    def _on_word_wrap_toggled(self, checked):
        """Включает/выключает перенос длинных строк в текстовом details_view."""
        mode = (QPlainTextEdit.LineWrapMode.WidgetWidth if checked
                else QPlainTextEdit.LineWrapMode.NoWrap)
        self.details_view.setLineWrapMode(mode)

    def _refresh_details_view(self):
        """Перестраивает содержимое окна 'Выделение' на основе текущего выделения.
        Учитывает кнопки 'Форматировать JSON' и 'Дерево'."""
        selected_indexes = self.log_view.selectedIndexes()
        if not selected_indexes:
            self.details_view.clear()
            self.details_view.setExtraSelections([])
            self.details_tree.clear()
            self._update_selection_info([])
            return
        selected_indexes.sort(key=lambda x: x.row())

        json_feature_on = self._ui_features.get("json_format", True)
        tree_mode = json_feature_on and self.btn_json_tree.isChecked()

        if tree_mode:
            # Берём первую выделенную строку и парсим из неё JSON в дерево.
            first_idx = selected_indexes[0]
            text = self.model.data(first_idx, Qt.ItemDataRole.UserRole) or ""
            extras = len(selected_indexes) - 1
            self._populate_json_tree(text, extras_count=extras)
        else:
            display_indexes = selected_indexes[:50]
            format_json = json_feature_on and self.btn_format_json.isChecked()
            full_text = ""
            for idx in display_indexes:
                text = self.model.data(idx, Qt.ItemDataRole.UserRole)
                if format_json:
                    text = self._prettify_json_in_text(text)
                full_text += text + "\n" + "=" * 80 + "\n"
            if len(selected_indexes) > 50:
                full_text += f"\n... и ещё {len(selected_indexes) -
                                            50} выделенных строк не показано."
            # В режиме JSON-формата включаем code folding + подсветку.
            # В обычном режиме - просто текст.
            if format_json:
                self.details_view.setPlainTextWithFolding(full_text)
            else:
                self.details_view.setPlainText(full_text)
            self._highlight_search_matches()

        self._update_selection_info(selected_indexes)

    def _populate_json_tree(self, text, extras_count=0):
        """Парсит структуру (JSON или Java-style toString) из text и наполняет details_tree.
        Если в строке несколько структур - берётся самая длинная.
        extras_count - сколько ещё строк выделено (для подсказки в заголовке)."""
        self.details_tree.clear()

        # Сначала пробуем JSON - он структурно богаче
        obj, prefix, suffix = self._extract_largest_json(text)
        kv_name = None

        if obj is None:
            # JSON нет - пробуем Name(key=value, ...)
            kv_obj, prefix, suffix, kv_name = self._extract_largest_kv_call(text)
            obj = kv_obj

        if obj is None:
            root = QTreeWidgetItem(
                self.details_tree, ["(структуры не найдено)", text[:200]])
            root.setFirstColumnSpanned(False)
            return

        # Метаданные строки до и после структуры - в отдельный узел сверху,
        # чтобы не терялся контекст (timestamp, уровень, логгер).
        meta_color = QColor(self._json_palette.get("tree_meta", "#888888"))
        meta_text = (prefix.strip() + " ... " + suffix.strip()).strip(" .")
        if meta_text:
            meta = QTreeWidgetItem(self.details_tree, ["(контекст строки)", meta_text[:500]])
            meta.setForeground(0, meta_color)
            meta.setForeground(1, meta_color)

        if kv_name is not None:
            root_label = f"{kv_name}(...)  ({len(obj)} параметров)"
            root_key = kv_name
        elif isinstance(obj, dict):
            root_label = f"{{...}}  ({len(obj)} полей)"
            root_key = "root"
        else:
            root_label = f"[...]  ({len(obj)} элементов)"
            root_key = "root"
        root = QTreeWidgetItem(self.details_tree, [root_key, root_label])
        self._add_json_node(root, obj)
        root.setExpanded(True)

        if extras_count > 0:
            note = QTreeWidgetItem(
                self.details_tree, [
                    "", f"... и ещё {extras_count} выделенных строк не показано (показана только первая)"])
            note.setForeground(1, meta_color)

    @staticmethod
    def _extract_largest_json(text):
        """Ищет в тексте все валидные JSON-фрагменты (объекты или массивы) и возвращает
        самый длинный + текст до и после него. Если ничего нет - (None, text, '').
        Длинный обычно и есть тот, который пользователь хочет видеть как дерево."""
        decoder = json.JSONDecoder()
        best = None  # (length, pos, end, obj)
        for pos, ch in enumerate(text):
            if ch not in '{[':
                continue
            try:
                obj, end = decoder.raw_decode(text[pos:])
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, (dict, list)):
                continue
            if isinstance(obj, dict) and len(obj) == 0:
                continue
            if isinstance(obj, list) and len(obj) == 0:
                continue
            length = end
            if best is None or length > best[0]:
                best = (length, pos, pos + end, obj)
        if best is None:
            return None, text, ""
        _, pos, end_abs, obj = best
        return obj, text[:pos], text[end_abs:]

    @staticmethod
    def _extract_largest_kv_call(text):
        """Ищет в text вызов вида Name(key=value, key=value, ...) с >=2 параметрами
        (все строго key=value). Возвращает самый длинный.

        Возвращает (kv_dict, prefix, suffix, name) или (None, text, '', None).
        Значения парсятся в Python-типы (true/false/null/числа/строки в кавычках).
        Это нужно чтобы дерево могло их подсветить как примитивы по типу."""
        best = None  # (length, name, kv, start, end)
        for m in LogViewerWidget._KV_NAME_RE.finditer(text):
            open_pos = m.end() - 1
            close_pos = LogViewerWidget._find_matching_paren(text, open_pos)
            if close_pos == -1:
                continue
            inner = text[open_pos + 1:close_pos]
            parts = LogViewerWidget._split_top_level(inner, ',')
            if len(parts) < 2:
                continue
            kv = {}
            ok = True
            for p in parts:
                mm = re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*\Z',
                              p, re.DOTALL)
                if not mm:
                    ok = False
                    break
                kv[mm.group(1)] = LogViewerWidget._parse_kv_value(mm.group(2))
            if not ok:
                continue
            length = close_pos - m.start() + 1
            if best is None or length > best[0]:
                best = (length, m.group(1), kv, m.start(), close_pos + 1)
        if best is None:
            return None, text, "", None
        _, name, kv, start, end = best
        return kv, text[:start], text[end:], name

    @staticmethod
    def _parse_kv_value(s):
        """Превращает текстовое значение из kv-call в Python-тип (для подсветки в дереве):
        true/false/null/None -> bool/None, число -> int/float, "..." или '...' -> str без
        кавычек, иначе исходная строка как есть."""
        s = s.strip()
        if s in ('true',):
            return True
        if s in ('false',):
            return False
        if s in ('null', 'None'):
            return None
        # Число
        try:
            if '.' in s or 'e' in s or 'E' in s:
                return float(s)
            return int(s)
        except ValueError:
            pass
        # Строка в кавычках
        if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
            return s[1:-1]
        return s

    def _add_json_node(self, parent_item, value):
        """Рекурсивно добавляет дочерние узлы к parent_item для значения value."""
        if isinstance(value, dict):
            for k, v in value.items():
                self._add_json_field(parent_item, str(k), v)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                self._add_json_field(parent_item, f"[{i}]", v)

    def _add_json_field(self, parent_item, key, value):
        """Создаёт один узел дерева под parent_item для пары (key, value).
        Цвета примитивов берутся из текущей палитры темы (self._json_palette)."""
        palette = self._json_palette
        meta_color = QColor(palette.get("tree_meta", "#888888"))
        if isinstance(value, dict):
            label = f"{{...}}  ({len(value)} полей)" if value else "{} (пусто)"
            item = QTreeWidgetItem(parent_item, [key, label])
            item.setForeground(1, meta_color)
            self._add_json_node(item, value)
        elif isinstance(value, list):
            label = f"[...]  ({len(value)} элементов)" if value else "[] (пусто)"
            item = QTreeWidgetItem(parent_item, [key, label])
            item.setForeground(1, meta_color)
            self._add_json_node(item, value)
        else:
            # Примитив: строка/число/bool/null - одна строка с подсветкой типа
            if value is None:
                shown = "null"
                color = meta_color
            elif isinstance(value, bool):
                shown = "true" if value else "false"
                color = QColor(palette.get("json_keyword", "#569CD6"))
            elif isinstance(value, (int, float)):
                shown = str(value)
                color = QColor(palette.get("json_number", "#B5CEA8"))
            else:
                # Строка - в кавычках, чтобы было видно что это строка
                s = str(value)
                if len(s) > 500:
                    s = s[:500] + "…"
                shown = json.dumps(s, ensure_ascii=False)
                color = QColor(palette.get("json_string", "#CE9178"))
            item = QTreeWidgetItem(parent_item, [key, shown])
            item.setForeground(1, color)

    def _show_details_tree_menu(self, pos):
        """Контекстное меню в дереве JSON: копировать ключ, значение или путь."""
        item = self.details_tree.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        act_key = menu.addAction("Копировать ключ")
        act_value = menu.addAction("Копировать значение")
        act_pair = menu.addAction("Копировать «ключ: значение»")
        chosen = menu.exec(self.details_tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        cb = QApplication.clipboard()
        if chosen == act_key:
            cb.setText(item.text(0))
        elif chosen == act_value:
            cb.setText(item.text(1))
        else:
            cb.setText(f"{item.text(0)}: {item.text(1)}")

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
            return f"{sign}{a / 1000:.2f}s"
        if a < 3_600_000:
            return f"{sign}{a / 60_000:.1f}m"
        return f"{sign}{a / 3_600_000:.2f}h"

    @staticmethod
    def _prettify_json_in_text(text):
        """В каждой строке ищет JSON-фрагмент (объект или массив) и заменяет на
        форматированный с отступами. Перебирает ВСЕ позиции '{' и '[' пока не найдёт
        валидный JSON - нужно потому что в типичной строке лога много '['
        (`[INFO]`, `[Logger]`), и наивный поиск первого `[` всегда падает.

        Если JSON не нашёлся, на той же строке пробуем переписать вызовы вида
        Name(key=value, key=value, ...) - в логах Java/Kotlin это типичный
        toString() длинных DTO, и читать их одной строкой невозможно."""
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
            if rebuilt is None:
                # JSON не нашёлся - пробуем kv-call. Если и его нет - оставляем как было.
                rebuilt = LogViewerWidget._prettify_kv_calls(line)
            result_lines.append(rebuilt)
        return '\n'.join(result_lines)

    # ----- Pretty-print для Java-style toString: Name(key=value, key=value, ...) -----

    # Имя должно быть валидным Java/Kotlin/Python-идентификатором, сразу после которого '('.
    # \b в начале не используем - имя может быть после non-word char (например ': ').
    _KV_NAME_RE = re.compile(r'(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\(')

    @staticmethod
    def _split_top_level(s, sep=','):
        """Разбивает s на части по sep, который встречается ВНЕ любых вложенных
        скобок/кавычек. Поддерживает '(' '[' '{' и одинарные/двойные кавычки."""
        parts = []
        cur = []
        depth = 0
        in_str = False
        quote = None
        prev = ''
        for ch in s:
            if in_str:
                if ch == quote and prev != '\\':
                    in_str = False
                cur.append(ch)
            elif ch in ('"', "'"):
                in_str = True
                quote = ch
                cur.append(ch)
            elif ch in '([{':
                depth += 1
                cur.append(ch)
            elif ch in ')]}':
                depth -= 1
                cur.append(ch)
            elif ch == sep and depth == 0:
                parts.append(''.join(cur))
                cur = []
            else:
                cur.append(ch)
            prev = ch
        parts.append(''.join(cur))
        return parts

    @staticmethod
    def _find_matching_paren(s, open_idx):
        """В строке s на позиции open_idx стоит '('. Возвращает позицию соответствующей
        ')' с учётом вложенности и кавычек. -1 если не нашлось."""
        depth = 0
        in_str = False
        quote = None
        prev = ''
        for i in range(open_idx, len(s)):
            ch = s[i]
            if in_str:
                if ch == quote and prev != '\\':
                    in_str = False
            elif ch in ('"', "'"):
                in_str = True
                quote = ch
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    return i
            prev = ch
        return -1

    @staticmethod
    def _prettify_kv_calls(line):
        """Ищет в строке вызовы Name(key=value, key=value, ...) (>=2 параметров,
        все key=value) и переписывает их с переносом строк и отступом 2 пробела.
        Возвращает изменённую строку (или исходную, если ничего не подошло)."""
        out = []
        pos = 0
        changed = False
        while pos < len(line):
            m = LogViewerWidget._KV_NAME_RE.search(line, pos)
            if not m:
                break
            open_pos = m.end() - 1
            close_pos = LogViewerWidget._find_matching_paren(line, open_pos)
            if close_pos == -1:
                break
            inner = line[open_pos + 1:close_pos]
            parts = LogViewerWidget._split_top_level(inner, ',')
            # Применяем только если >=2 параметров и КАЖДЫЙ выглядит как key=value:
            # \b\w+\s*= в начале (после strip).
            if len(parts) >= 2 and all(re.match(r'\s*[A-Za-z_][A-Za-z0-9_]*\s*=', p) for p in parts):
                name = m.group(1)
                formatted_parts = ',\n  '.join(p.strip() for p in parts)
                out.append(line[pos:m.start()])
                out.append(f"{name}(\n  {formatted_parts}\n)")
                pos = close_pos + 1
                changed = True
            else:
                # Сдвигаемся за эту '(' и ищем дальше
                out.append(line[pos:open_pos + 1])
                pos = open_pos + 1
        out.append(line[pos:])
        return ''.join(out) if changed else line

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

        # Совпадения ищем точно так же, как FilterWorker. Match case и regex -
        # по состоянию кнопок "Aa" и ".*". Regex по умолчанию выключен,
        # иначе любой '?' в URL ломал бы подсветку.
        case_sensitive = self.btn_match_case.isChecked()
        use_regex = self.btn_use_regex.isChecked()
        positions = []
        pattern = None
        if use_regex:
            try:
                pattern = re.compile(search_text, 0 if case_sensitive else re.IGNORECASE)
            except re.error:
                pattern = None
        if pattern is not None:
            for m in pattern.finditer(full_text):
                if m.start() != m.end():
                    positions.append((m.start(), m.end()))
                if len(positions) >= self.MAX_DETAIL_HIGHLIGHTS:
                    break
        else:
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
