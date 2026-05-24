import os
import sys
import ctypes
import datetime
import logging
import tracemalloc

_log = logging.getLogger('window')

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:  # pragma: no cover
    psutil = None
    _HAS_PSUTIL = False

from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLineEdit, QLabel, QFileDialog, QProgressBar, QToolButton,
                             QMessageBox, QStyle, QFrame, QCheckBox, QApplication, QMenu)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QIcon, QKeySequence, QCloseEvent, QShortcut

from config import THEMES, APP_VERSION, DEFAULT_UI_FEATURES, load_settings, save_settings
from gui.log_viewer import LogViewerWidget
from gui.tab_manager import SplitManager
from gui.settings import SettingsDialog
from gui.help_dialog import HelpDialog
from gui.custom_widgets import BusyOverlay


def resource_path(relative_path):
    """ Получает абсолютный путь к ресурсу, работает для dev и для PyInstaller """
    try:
        # PyInstaller создает временную папку _MEIPASS и хранит пути в ней
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        _log.info("MainWindow.__init__ start, APP_VERSION=%s", APP_VERSION)

        # Отвязываем приложение от стандартной иконки Python в панели задач Windows
        try:
            myappid = 'LogAnalyzer.CustomApp.1.0'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

        self.setWindowTitle(f"Log Analyzer v{APP_VERSION}")
        self.resize(1400, 900)

        # Устанавливаем вашу кастомную иконку
        icon_path = resource_path("log_perfect.ico")
        self.setWindowIcon(QIcon(icon_path))

        # drag and drop support
        self.setAcceptDrops(True)

        # Load settings
        self.settings = load_settings()
        self.current_theme_name = self.settings.get("theme", "Default")
        self.current_font_size = self.settings.get("font_size", 10)
        self.ui_features = self.settings.get("ui_features", dict(DEFAULT_UI_FEATURES))

        # LRU список загруженных viewer'ов (от давнего к свежему). Когда длина
        # превышает MAX_LOADED_VIEWERS, голова списка выгружается. Сами
        # вкладки не закрываются - они остаются в lazy-состоянии.
        self._lru_loaded = []

        self.setup_ui()
        self.apply_theme(self.current_theme_name)

        # Flag to prevent recursive updates when syncing UI
        self.updating_ui = False

        # F1 - открыть справку (стандартный шорткат)
        sc_help = QShortcut(QKeySequence(Qt.Key.Key_F1), self)
        sc_help.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_help.activated.connect(self.open_help)

        # Применяем стартовые UI-фичи (для chk_group в главном окне)
        self.chk_group.setVisible(self.ui_features.get("group_dupes", True))

        # --- Busy overlay для долгих операций ---
        # Создаём с parent=self чтобы overlay тянулся под весь центральный
        # виджет. Геометрия обновляется в resizeEvent ниже.
        self._busy_overlay = BusyOverlay(self)
        self._busy_overlay.hide()

        # --- RAM-монитор в строке статуса ---
        # psutil для текущего RSS процесса; tracemalloc для snapshot top-N
        # аллокаций (Ctrl+Shift+M). Если psutil не установлен (например при
        # запуске из исходников без `pip install psutil`), монитор просто
        # покажет «—», но snapshot всё равно сработает.
        self.lbl_ram = QLabel("RAM: —")
        self.lbl_ram.setToolTip(
            "Текущее потребление памяти процессом + entries по табам.\n"
            "Ctrl+Shift+M — сохранить tracemalloc-snapshot топ-30 аллокаций "
            "в файл рядом с приложением."
        )
        self.statusBar().addPermanentWidget(self.lbl_ram)
        self._ram_timer = QTimer(self)
        self._ram_timer.setInterval(2000)  # обновлять раз в 2 сек
        self._ram_timer.timeout.connect(self._update_ram_indicator)
        self._ram_timer.start()
        self._update_ram_indicator()

        # Сохраняем ссылку на хоткей чтобы потом включать/выключать через
        # настройку «Режим разработчика» (debug_panel).
        self._sc_snapshot = QShortcut(QKeySequence("Ctrl+Shift+M"), self)
        self._sc_snapshot.setContext(Qt.ShortcutContext.WindowShortcut)
        self._sc_snapshot.activated.connect(self._take_memory_snapshot)

        # Стартовая видимость debug-панели согласно настройкам
        self._apply_debug_panel_visibility()

        # Restore session
        self.restore_session()

    def setup_ui(self):
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.root_layout = QVBoxLayout(self.central_widget)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)

        self.widget_holder = QWidget(self.central_widget)
        self.widget_holder.setVisible(False)

        self.create_widgets()

        # Main content is now the SplitManager - с конфигами групп
        # (имя + цвет + collapsed) из settings.json.
        self.split_manager = SplitManager(
            group_configs=self.settings.get("group_configs"),
        )
        self.split_manager.activeTabChanged.connect(self.on_active_tab_changed)
        self.split_manager.groupConfigChanged.connect(self.save_current_settings)
        self.split_manager.filesDroppedOnGroup.connect(self._on_files_dropped_on_group)
        # Применяем сохранённый режим Stack/Splitter
        if self.settings.get("group_layout_mode") == "stack":
            self.split_manager.set_stack_mode(True)

    def create_widgets(self):
        self.btn_open = QPushButton("Открыть файл")
        self.btn_open.clicked.connect(self.open_file_dialog)
        self.btn_settings = QPushButton("Настройки")
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_help = QPushButton("Справка")
        self.btn_help.setToolTip("Открыть окно со справкой по функционалу (F1)")
        self.btn_help.clicked.connect(self.open_help)

        # Кнопка «🗂 Группы» - открывает диалог управления всеми группами
        # (показать/скрыть, переименовать, цвет, удалить + список файлов
        # выбранной группы).
        self.btn_groups = QPushButton("🗂 Группы")
        self.btn_groups.setToolTip(
            "Управление группами: показать/скрыть, переименовать, цвет, "
            "удалить + список файлов выбранной группы."
        )
        self.btn_groups.clicked.connect(self.open_groups_manager)

        self.chk_info = QCheckBox("INFO")
        self.chk_info.setChecked(True)
        self.chk_info.stateChanged.connect(self.on_global_filter_changed)
        self.chk_debug = QCheckBox("DEBUG")
        self.chk_debug.setChecked(True)
        self.chk_debug.stateChanged.connect(self.on_global_filter_changed)
        self.chk_warn = QCheckBox("WARN")
        self.chk_warn.setChecked(True)
        self.chk_warn.stateChanged.connect(self.on_global_filter_changed)
        self.chk_error = QCheckBox("ERROR")
        self.chk_error.setChecked(True)
        self.chk_error.stateChanged.connect(self.on_global_filter_changed)

        self.chk_group = QCheckBox("Свернуть дубли")
        self.chk_group.setToolTip(
            "Схлопывать подряд идущие одинаковые сообщения в одну строку с префиксом [×N]")
        self.chk_group.setChecked(False)
        self.chk_group.stateChanged.connect(self.on_global_filter_changed)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(150)
        self.progress_bar.setVisible(False)

    def detach_widgets(self):
        widgets = [
            self.btn_open, self.btn_settings, self.btn_help, self.btn_groups,
            self.chk_info, self.chk_debug, self.chk_warn, self.chk_error,
            self.chk_group,
            self.progress_bar,
            self.split_manager
        ]
        for w in widgets:
            w.setParent(self.widget_holder)

        self.btn_open.setObjectName("")
        self.btn_settings.setObjectName("")

    def apply_theme(self, theme_name):
        # Если в settings.json лежит имя удалённой темы (например после обновления
        # приложения, когда тема переименовали или убрали) - молча скатываемся
        # на Default, иначе KeyError при запуске.
        if theme_name not in THEMES:
            theme_name = "Default"
        self.current_theme_name = theme_name
        t = THEMES[theme_name]
        layout_type = t["layout"]

        self.detach_widgets()
        QApplication.processEvents()

        while self.root_layout.count():
            item = self.root_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if layout_type == "top":
            self.build_top_layout(t)
        else:
            self.build_side_layout(t)

        self.apply_stylesheet(t)
        self.update_fonts()

    def update_fonts(self):
        # Update all open viewers
        for group in self.split_manager.iter_groups():
            for i in range(group.count()):
                viewer = group.widget(i)
                if isinstance(viewer, LogViewerWidget):
                    viewer.apply_theme(self.current_theme_name, self.current_font_size)

    def build_top_layout(self, t):
        toolbar = QFrame()
        toolbar.setObjectName("Panel")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(10, 5, 10, 5)

        if self.current_theme_name == "Default":
            self.btn_open.setIcon(
                self.style().standardIcon(
                    QStyle.StandardPixmap.SP_DialogOpenButton))
            self.btn_settings.setIcon(self.style().standardIcon(
                QStyle.StandardPixmap.SP_FileDialogDetailedView))
        else:
            self.btn_open.setIcon(QIcon())
            self.btn_settings.setIcon(QIcon())

        tb_layout.addWidget(self.btn_open)
        tb_layout.addWidget(self.btn_settings)
        tb_layout.addWidget(self.btn_help)
        tb_layout.addWidget(self.btn_groups)
        tb_layout.addSpacing(20)
        tb_layout.addWidget(self.chk_info)
        tb_layout.addWidget(self.chk_debug)
        tb_layout.addWidget(self.chk_warn)
        tb_layout.addWidget(self.chk_error)
        tb_layout.addSpacing(15)
        tb_layout.addWidget(self.chk_group)
        tb_layout.addStretch()
        tb_layout.addWidget(self.progress_bar)

        self.root_layout.addWidget(toolbar)
        self.root_layout.addWidget(self.split_manager, 1)

    def build_side_layout(self, t):
        h_layout = QHBoxLayout()
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Panel")
        sidebar.setFixedWidth(220)
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(15, 15, 15, 15)
        sb_layout.setSpacing(10)

        self.btn_open.setObjectName("OpenBtn")
        self.btn_open.setIcon(QIcon())
        self.btn_settings.setIcon(QIcon())

        sb_layout.addWidget(QLabel("LOG ANALYZER"))
        sb_layout.addSpacing(10)
        sb_layout.addWidget(self.btn_open)
        sb_layout.addWidget(self.btn_settings)
        sb_layout.addWidget(self.btn_help)
        sb_layout.addWidget(self.btn_groups)
        sb_layout.addSpacing(10)
        sb_layout.addWidget(QLabel("ФИЛЬТРЫ"))
        sb_layout.addWidget(self.chk_info)
        sb_layout.addWidget(self.chk_debug)
        sb_layout.addWidget(self.chk_warn)
        sb_layout.addWidget(self.chk_error)
        sb_layout.addSpacing(10)
        sb_layout.addWidget(self.chk_group)
        sb_layout.addStretch()
        sb_layout.addWidget(self.progress_bar)

        h_layout.addWidget(sidebar)
        h_layout.addWidget(self.split_manager, 1)

        container = QWidget()
        container.setLayout(h_layout)
        self.root_layout.addWidget(container)

    def apply_stylesheet(self, t):
        # Base styles
        qss = f"""
            QMainWindow, QWidget {{
                background-color: {t['bg_main']}; color: {t['text_main']};
                font-family: '{t['font_family']}';
            }}
            QFrame#Panel {{ background-color: {t['bg_panel']}; border: 1px solid {t['border']}; }}
            QLineEdit {{
                background-color: {t['bg_main']}; border: 1px solid {t['border']};
                padding: 6px; color: {t['text_main']};
            }}
            QListView {{ background-color: {t['bg_main']}; border: none; }}
            QListView::item {{ padding: 4px; border-bottom: 1px solid {t['border']}; }}
            QListView::item:selected {{ background-color: {t['selection']}; color: {t['text_main']}; }}
            QTextEdit {{
                background-color: {t['bg_panel']}; border-top: 1px solid {t['border']};
                color: {t['text_main']}; font-family: '{t['mono_font']}';
            }}
            QSplitter::handle {{ background-color: {t['border']}; }}
            QTabWidget::pane {{ border: 1px solid {t['border']}; top: -1px; }}
            QTabBar::tab {{
                background: {t['bg_panel']};
                color: {t['text_muted']};
                padding: 8px 15px;
                border: 1px solid {t['border']};
                border-bottom: none;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background: {t['bg_main']};
                color: {t['text_main']};
                border-bottom: 1px solid {t['bg_main']};
            }}
            QTabBar::tab:!selected:hover {{ background: {t['selection']}; }}
            QTabBar::close-button {{ subcontrol-position: right; }}
        """

        if self.current_theme_name == "Default":
            qss += f"""
                QPushButton {{
                    background-color: #353535;
                    color: {t['text_main']};
                    border: 1px solid {t['border']};
                    padding: 6px 12px;
                    border-radius: 3px;
                }}
                QPushButton:hover {{ background-color: #454545; }}
                QCheckBox {{ color: {t['text_main']}; font-weight: bold; }}
            """
        else:
            qss += f"""
                QPushButton {{
                    background-color: {t['bg_main']};
                    color: {t['accent']};
                    border: 1px solid {t['accent']};
                    padding: 6px 12px;
                    font-weight: bold;
                }}
                QPushButton:hover {{ background-color: {t['accent']}; color: {t['bg_main']}; }}
            """

        self.setStyleSheet(qss)

        if self.current_theme_name == "Default":
            self.chk_info.setStyleSheet(f"color: {t['info']}; font-weight: bold;")
            self.chk_debug.setStyleSheet(f"color: {t['debug']}; font-weight: bold;")
            self.chk_warn.setStyleSheet(f"color: {t['warn']}; font-weight: bold;")
            self.chk_error.setStyleSheet(f"color: {t['error']}; font-weight: bold;")
        else:
            self.chk_info.setStyleSheet("")
            self.chk_debug.setStyleSheet("")
            self.chk_warn.setStyleSheet("")
            self.chk_error.setStyleSheet("")

    def open_settings(self):
        # Сохраняем исходные настройки на случай отмены - тема применяется live,
        # и при Cancel надо вернуться к ним без сохранения.
        original_theme = self.current_theme_name
        original_font_size = self.current_font_size

        current_group_layout = self.settings.get("group_layout_mode", "splitter")
        dlg = SettingsDialog(
            self.current_theme_name, self.current_font_size, self.ui_features,
            current_group_layout=current_group_layout, parent=self,
        )
        dlg.previewChanged.connect(self._preview_appearance)

        result = dlg.exec()
        # Останавливаем debounce-таймер - иначе запланированное pending-
        # превью может сработать ПОСЛЕ apply_theme ниже и затереть результат.
        if hasattr(self, '_preview_timer') and self._preview_timer is not None:
            self._preview_timer.stop()

        if result:
            theme, size, features, group_layout = dlg.get_settings()
            self.current_font_size = size
            self.ui_features = features
            self.apply_theme(theme)
            self._apply_ui_features_everywhere()
            # Применяем режим расположения групп
            self.split_manager.set_stack_mode(group_layout == 'stack')
            self.settings["group_layout_mode"] = group_layout
            self.save_current_settings()
        else:
            # Откатываем live-превью на исходные настройки (без сохранения).
            # apply_theme зовём только если что-то реально менялось.
            if (self.current_theme_name != original_theme
                    or self.current_font_size != original_font_size):
                self.current_font_size = original_font_size
                self.apply_theme(original_theme)

    def _preview_appearance(self, theme_name, font_size):
        """Слот live-превью из SettingsDialog: применяет тему и размер шрифта
        прямо во время выбора, без сохранения в settings.json.

        ДЕБАУНС: apply_theme - тяжёлая операция (перестраивает layouts окна
        и зовёт apply_theme у всех viewer'ов с пересборкой стилей). Если
        юзер крутит спин шрифта 8→9→10→11, нельзя пересчитывать тему 4
        раза. Запоминаем последний выбор и применяем через 200мс после
        паузы."""
        self._pending_preview_theme = theme_name
        self._pending_preview_font_size = font_size
        if not hasattr(self, '_preview_timer') or self._preview_timer is None:
            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.setInterval(200)
            self._preview_timer.timeout.connect(self._apply_pending_preview)
        self._preview_timer.start()

    def _apply_pending_preview(self):
        theme_name = getattr(self, '_pending_preview_theme', None)
        font_size = getattr(self, '_pending_preview_font_size', None)
        if theme_name is None or font_size is None:
            return
        self.current_font_size = font_size
        self.apply_theme(theme_name)

    def _apply_ui_features_everywhere(self):
        """Распространяет настройки видимости на главное окно и все открытые вьюверы."""
        # chk_group в главном тулбаре - часть фичи "group_dupes"
        self.chk_group.setVisible(self.ui_features.get("group_dupes", True))
        # При скрытии глобально снимаем галочку, иначе фильтр продолжит группировать
        if not self.ui_features.get("group_dupes", True) and self.chk_group.isChecked():
            self.chk_group.setChecked(False)
        # Режим разработчика
        self._apply_debug_panel_visibility()

    def _apply_debug_panel_visibility(self):
        """Показывает/скрывает RAM-индикатор и хоткей Ctrl+Shift+M согласно
        ui_features['debug_panel']. По умолчанию выключено."""
        on = bool(self.ui_features.get("debug_panel", False))
        if hasattr(self, 'lbl_ram'):
            self.lbl_ram.setVisible(on)
        if hasattr(self, '_ram_timer'):
            if on:
                if not self._ram_timer.isActive():
                    self._ram_timer.start()
                self._update_ram_indicator()
            else:
                self._ram_timer.stop()
        if hasattr(self, '_sc_snapshot'):
            self._sc_snapshot.setEnabled(on)

        for group in self.split_manager.iter_groups():
            for i in range(group.count()):
                viewer = group.widget(i)
                if isinstance(viewer, LogViewerWidget):
                    viewer.apply_ui_features(self.ui_features)

    def _on_files_dropped_on_group(self, panel_index, paths):
        """Юзер бросил файл(ы) на плашку группы #panel_index.
        Делаем эту группу активной и грузим файл туда."""
        try:
            target_panel = self.split_manager.panels[panel_index]
        except (IndexError, AttributeError):
            return
        self.split_manager.active_group = target_panel.tabs
        for p in paths:
            if os.path.exists(p):
                self.load_file(p, side="active")

    def open_groups_manager(self):
        """Открывает диалог управления группами."""
        from gui.groups_dialog import GroupsManagerDialog
        dlg = GroupsManagerDialog(self, self)
        dlg.exec()
        # После закрытия диалога сохраняем состояние - могли быть изменения
        self.save_current_settings()

    def open_help(self):
        dlg = HelpDialog(self.current_theme_name, self)
        dlg.exec()

    def on_zoom_request(self, delta):
        if delta > 0:
            self.current_font_size = min(24, self.current_font_size + 1)
        else:
            self.current_font_size = max(6, self.current_font_size - 1)
        self.update_fonts()
        self.save_current_settings()

    def open_file_dialog(self):
        file_names, _ = QFileDialog.getOpenFileNames(
            self, "Открыть лог-файл", "",
            "Лог-файлы (*.log *.txt *.gz *.zip *.7z *.rar);;Все файлы (*)"
        )
        # Только ПЕРВЫЙ файл загружается сразу - иначе при выборе 46 логов
        # все 46 LogLoader-потоков стартуют параллельно и съедают всю RAM.
        # Остальные - lazy: юзер сам кликнет таб, когда захочет его открыть.
        for i, file_name in enumerate(file_names):
            self.load_file(file_name, lazy=(i > 0))

    # Жёсткий лимит на число одновременно загруженных в RAM табов.
    # Свыше - самые давно неактивные выгружаются через viewer.unload().
    MAX_LOADED_VIEWERS = 5
    # Мягкий лимит по RAM (мегабайты). Когда RSS процесса превышает порог,
    # старые табы выгружаются дополнительно, пока RSS не уйдёт ниже или пока
    # не останется единственный (активный) viewer. Работает только если
    # psutil доступен; иначе остаётся только лимит по числу.
    RAM_LIMIT_MB = 3072  # 3 GB

    def load_file(self, file_path, side="active", lazy=False):
        _log.info("load_file: path=%r side=%s lazy=%s", file_path, side, lazy)
        # Достаём сохранённые закладки для этого файла (если были)
        bookmarks = self.settings.get("bookmarks", {}).get(file_path, [])
        viewer = LogViewerWidget(
            file_path, self.current_theme_name, self.current_font_size,
            bookmarks=bookmarks,
            ui_features=self.ui_features,
            lazy=lazy,
        )
        viewer.progressChanged.connect(self.progress_bar.setValue)
        viewer.loadingFinished.connect(self.on_loading_finished)

        # Apply current global filters to new viewer
        viewer.set_global_filters(
            self.chk_info.isChecked(),
            self.chk_debug.isChecked(),
            self.chk_warn.isChecked(),
            self.chk_error.isChecked(),
            self.chk_group.isChecked(),
        )

        # silent=True для lazy: иначе activeTabChanged сработает на каждом
        # добавленном табе при restore_session, и lazy перестанет работать.
        self.split_manager.add_tab(
            viewer, os.path.basename(file_path), side, silent=lazy)
        if not lazy:
            self.progress_bar.setVisible(True)
            self.btn_open.setEnabled(False)
            self._lru_touch(viewer)
            # Busy-оверлей с понятным текстом, чтобы юзер видел что
            # приложение не зависло, а парсит файл.
            self.show_busy(
                f"Загрузка {os.path.basename(file_path)} ...\n"
                f"Большие логи могут парситься несколько секунд."
            )

    def on_loading_finished(self):
        self.progress_bar.setVisible(False)
        self.btn_open.setEnabled(True)
        self.hide_busy()

    def on_active_tab_changed(self, viewer):
        # Показываем полный путь активного файла в заголовке окна (стиль Notepad++)
        if viewer is not None and hasattr(viewer, 'file_path'):
            self.setWindowTitle(f"{viewer.file_path} - Log Analyzer v{APP_VERSION}")
        else:
            self.setWindowTitle(f"Log Analyzer v{APP_VERSION}")

        # Lazy-load: если активировали виджет, который не загружен (после
        # restore_session или после LRU-выгрузки) - грузим его сейчас.
        # Здесь обязательно показываем busy: ensure_loaded() запускает парсинг
        # ассинхронно в QThread, но UI всё равно может казаться зависшим
        # (особенно если сразу после показывается прогресс-бар и тулбар).
        if isinstance(viewer, LogViewerWidget) and not viewer._loaded:
            self.show_busy(
                f"Загрузка {os.path.basename(viewer.file_path)} ...\n"
                f"Большие логи могут парситься несколько секунд."
            )
            viewer.ensure_loaded()
            self.progress_bar.setVisible(True)
            self.btn_open.setEnabled(False)

        if isinstance(viewer, LogViewerWidget) and viewer._loaded:
            self._lru_touch(viewer)

        # Освобождаем тяжёлые кэши (индекс «История кода») у НЕактивных
        # viewer'ов. На больших логах clear() дерева с десятками тысяч
        # QTreeWidgetItem занимает заметное время + само освобождение dict
        # с миллионом ссылок не мгновенно - под busy чтобы окно не выглядело
        # «не отвечает». Сначала определим, есть ли что чистить.
        needs_release = False
        for group in self.split_manager.iter_groups():
            for i in range(group.count()):
                w = group.widget(i)
                if w is viewer or not isinstance(w, LogViewerWidget):
                    continue
                if getattr(w, '_code_history_index', None) is not None:
                    needs_release = True
                    break
            if needs_release:
                break

        if needs_release:
            self.show_busy("Освобождение памяти от неактивных вкладок ...")
        try:
            for group in self.split_manager.iter_groups():
                for i in range(group.count()):
                    w = group.widget(i)
                    if w is viewer:
                        continue
                    if isinstance(w, LogViewerWidget):
                        w.release_heavy_caches()
        finally:
            if needs_release:
                # Если в этом тике ещё не запускается ensure_loaded (viewer уже
                # был loaded) - скрываем busy здесь. Иначе busy скроется в
                # on_loading_finished для нового активного.
                if isinstance(viewer, LogViewerWidget) and viewer._loaded:
                    self.hide_busy()

    def _lru_touch(self, viewer):
        """Делает viewer самым «свежим» в LRU; запускает eviction, если
        количество загруженных табов превысило MAX_LOADED_VIEWERS."""
        if viewer in self._lru_loaded:
            self._lru_loaded.remove(viewer)
        self._lru_loaded.append(viewer)
        self._enforce_loaded_lru(keep_active=viewer)

    # ----- RAM-монитор -----

    def _format_count(self, n):
        """473000 → '473K', 985000 → '985K', 1500000 → '1.5M'."""
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n // 1_000}K"
        return str(n)

    def _update_ram_indicator(self):
        """Обновляет lbl_ram: текущий RSS процесса + entries по каждой
        открытой вкладке (или 'lazy', если таб не загружен)."""
        if _HAS_PSUTIL:
            try:
                rss_mb = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
                rss_text = f"RAM: {rss_mb:,.0f} MB"
            except Exception:
                rss_text = "RAM: ?"
        else:
            rss_text = "RAM: (psutil не установлен)"

        counts = []
        if hasattr(self, 'split_manager'):
            for group in self.split_manager.iter_groups():
                for i in range(group.count()):
                    w = group.widget(i)
                    if isinstance(w, LogViewerWidget):
                        if getattr(w, '_loaded', False):
                            counts.append(
                                self._format_count(len(w.model._entries))
                            )
                        else:
                            counts.append("lazy")
        if counts:
            self.lbl_ram.setText(f"{rss_text}  |  Entries: {' + '.join(counts)}")
        else:
            self.lbl_ram.setText(rss_text)

    # ----- Busy overlay -----

    def show_busy(self, text):
        """Показать полупрозрачный оверлей «занято» с текстом. Покажется
        поверх всего окна, мышь под ним перехватывается чтобы юзер не
        кликнул случайно во время блокирующей операции."""
        self._busy_overlay.show_with(text)
        # processEvents - чтобы кадр оверлея отрисовался ДО того, как
        # начнётся блокирующая операция.
        QApplication.processEvents()

    def set_busy_text(self, text):
        """Обновить текст без скрытия/показа (для прогресса 'X / Y')."""
        self._busy_overlay.set_text(text)
        QApplication.processEvents()

    def hide_busy(self):
        self._busy_overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_busy_overlay') and self._busy_overlay is not None:
            self._busy_overlay.resize(self.size())

    def _take_memory_snapshot(self):
        """Сохраняет tracemalloc-snapshot в файл рядом с .exe (или с main.py
        при запуске из исходников). Показывает QMessageBox с топ-10."""
        if not tracemalloc.is_tracing():
            QMessageBox.warning(
                self, "Snapshot недоступен",
                "tracemalloc не запущен - возможно, приложение запустилось "
                "не из main.py."
            )
            return

        try:
            snap = tracemalloc.take_snapshot()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось взять snapshot:\n{e}")
            return

        top = snap.statistics('lineno')[:30]

        # Куда сохранять: рядом с exe (frozen) или с main.py (dev)
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(sys.argv[0])) or '.'
        ts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        path = os.path.join(base_dir, f'memory_snapshot_{ts}.txt')

        if _HAS_PSUTIL:
            rss_mb = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
            header_rss = f"Process RSS: {rss_mb:,.1f} MB"
        else:
            header_rss = "Process RSS: (psutil не установлен)"

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f"Memory snapshot @ {ts}\n")
                f.write(f"{header_rss}\n")
                # Сводка по табам - по каждой группе. После Stack-only
                # рефакторинга header переехал из panel в chip в bar'е;
                # имя берём прямо из chip (или fallback через _group_name).
                sm = self.split_manager
                for panel in sm.iter_panels():
                    group = panel.tabs
                    group_name = getattr(panel, '_group_name', None) or "?"
                    for g in sm._groups:
                        if g['panel'] is panel:
                            group_name = g['chip'].name
                            break
                    for i in range(group.count()):
                        w = group.widget(i)
                        if isinstance(w, LogViewerWidget):
                            state = ("loaded" if getattr(w, '_loaded', False)
                                     else "lazy")
                            cnt = (len(w.model._entries)
                                   if getattr(w, '_loaded', False) else 0)
                            f.write(f"  [{group_name}] {w.file_path}: "
                                    f"{state}, entries={cnt}\n")
                f.write("\nTop 30 allocations by size:\n")
                for i, stat in enumerate(top, 1):
                    f.write(f"#{i:2d}: {stat}\n")
        except Exception as e:
            QMessageBox.critical(
                self, "Ошибка", f"Не удалось записать snapshot:\n{e}")
            return

        # Показываем компактный preview в диалоге
        preview = "\n".join(
            f"#{i}: {stat}" for i, stat in enumerate(top[:10], 1)
        )
        QMessageBox.information(
            self, "Memory snapshot",
            f"{header_rss}\n\n"
            f"Сохранено: {path}\n\n"
            f"Top-10 аллокаций:\n{preview}"
        )

    def _enforce_loaded_lru(self, keep_active=None):
        """Применяет два лимита подряд:
        1) Жёсткий по числу: пока loaded > MAX_LOADED_VIEWERS - выгружаем
           самые старые (кроме keep_active).
        2) Мягкий по RAM: если psutil.RSS > RAM_LIMIT_MB - выгружаем
           старейших пока не уйдём ниже порога или пока не останется только
           один активный viewer.

        Текущий активный никогда не выгружается. Если psutil недоступен -
        работает только лимит по числу."""
        # Сначала чистим тех, кто уже разгрузился (не должен висеть в LRU)
        self._lru_loaded = [v for v in self._lru_loaded if getattr(v, '_loaded', False)]

        def pick_victim():
            for v in self._lru_loaded:
                if v is not keep_active:
                    return v
            return None

        # 1) Жёсткий лимит по числу
        while len(self._lru_loaded) > self.MAX_LOADED_VIEWERS:
            victim = pick_victim()
            if victim is None:
                break
            _log.info("LRU evict (by count): %s", victim.file_path)
            victim.unload()
            self._lru_loaded.remove(victim)

        # 2) Мягкий лимит по RAM. Требует psutil; без него пропускаем.
        if not _HAS_PSUTIL:
            return

        def rss_mb():
            try:
                return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
            except Exception:
                return 0

        import gc
        # Запускаем мини-цикл: если RAM превышен и есть кого выгрузить (>=2
        # загруженных, чтобы не убить активный), evict один таб, форсируем GC
        # и проверяем снова. Лимит итераций = MAX_LOADED_VIEWERS, чтобы не
        # уйти в бесконечный цикл если RSS не освобождается (Python не всегда
        # сразу возвращает память ОС).
        rss = rss_mb()
        iters = 0
        while (rss > self.RAM_LIMIT_MB
               and len(self._lru_loaded) > 1
               and iters < self.MAX_LOADED_VIEWERS):
            victim = pick_victim()
            if victim is None:
                break
            _log.info(
                "LRU evict (by RAM, rss=%.0fMB > %dMB): %s",
                rss, self.RAM_LIMIT_MB, victim.file_path,
            )
            victim.unload()
            self._lru_loaded.remove(victim)
            gc.collect()
            new_rss = rss_mb()
            # Если RSS не падает (Python не возвращает память ОС) - дальше
            # выгружать бессмысленно, прекращаем.
            if new_rss >= rss - 10:
                _log.info(
                    "LRU evict: rss не освобождается (%.0f → %.0f MB), стоп",
                    rss, new_rss,
                )
                break
            rss = new_rss
            iters += 1

    def on_global_filter_changed(self):
        if self.updating_ui:
            return

        # Apply to ALL viewers
        info = self.chk_info.isChecked()
        debug = self.chk_debug.isChecked()
        warn = self.chk_warn.isChecked()
        error = self.chk_error.isChecked()
        group_dupes = self.chk_group.isChecked()

        for group in self.split_manager.iter_groups():
            for i in range(group.count()):
                viewer = group.widget(i)
                if isinstance(viewer, LogViewerWidget):
                    viewer.set_global_filters(info, debug, warn, error, group_dupes)

    def keyPressEvent(self, event):
        viewer = self.split_manager.get_current_viewer()
        if viewer:
            viewer.keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        # Проверяем, содержит ли перетаскиваемый объект ссылки (файлы)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            # Drop в общую область окна = в активную группу (она и так одна
            # видимая в Stack-режиме). Если юзер хочет в конкретную группу -
            # тащит на её плашку, там отдельный обработчик в GroupHeader
            # → filesDroppedOnGroup → _on_files_dropped_on_group.
            # Раньше тут была попытка угадать side по геометрии left/right
            # панелей - в Stack-режиме это давало случайные результаты
            # (например drop при активной «Мал Ком Тест» отправлял файл в
            # «Русхим», потому что у того панель тоже занимала ту же область).
            urls = [u for u in event.mimeData().urls() if u.isLocalFile()]
            for i, url in enumerate(urls):
                file_path = url.toLocalFile()
                # Первый файл - eager, остальные lazy (как в open_file_dialog).
                self.load_file(file_path, side="active", lazy=(i > 0))
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def restore_session(self):
        """Восстанавливает табы из settings.json в lazy-режиме.
        Источник истины - files_per_group (список списков по группам).
        Legacy files_left / files_right поддерживается как fallback для
        старых settings.json без полей per-group."""
        per_group = self.settings.get("files_per_group")
        if not per_group:
            # Legacy: собираем из files_left / files_right
            files_left = self.settings.get("files_left", [])
            files_right = self.settings.get("files_right", [])
            per_group = [files_left, files_right]

        # Количество существующих файлов всего - для busy-оверлея
        total = sum(
            len([f for f in group_files if os.path.exists(f)])
            for group_files in per_group
        )
        if total > 0:
            self.show_busy(
                f"Восстановление сессии: {total} файл(ов) ...\n"
                f"Загружается только активная вкладка, остальные ждут клика."
            )

        # Дозагружаем недостающие группы в SplitManager если в settings
        # сохранено больше групп чем сейчас есть в UI.
        while len(self.split_manager.panels) < len(per_group):
            self.split_manager.add_group()

        # Раскладываем файлы по группам по индексу (group_index = индекс
        # панели в split_manager.panels).
        for group_index, group_files in enumerate(per_group):
            for f in group_files:
                if not os.path.exists(f):
                    continue
                # side="left" → группа 0, "right" → группа 1, для остальных
                # пока нет специального side: используем active с заранее
                # выставленной активной группой.
                if group_index == 0:
                    self.load_file(f, side="left", lazy=True)
                elif group_index == 1:
                    self.load_file(f, side="right", lazy=True)
                else:
                    # Для групп 3+ - временно делаем нужную панель активной
                    target_panel = self.split_manager.panels[group_index]
                    target_panel.show()
                    self.split_manager.active_group = target_panel.tabs
                    self.load_file(f, side="active", lazy=True)

        # После всех silent-add'ов активируем верхний таб - его юзер увидит
        # первым, его и грузим (один файл, остальные остаются lazy и ждут
        # клика). Берём первый таб из первой видимой непустой группы.
        current = None
        for panel in self.split_manager.iter_panels():
            if panel.isVisible() and panel.tabs.count() > 0:
                current = panel.tabs.currentWidget()
                if current is not None:
                    break
        if isinstance(current, LogViewerWidget):
            self.split_manager.activeTabChanged.emit(current)
        elif total > 0:
            self.hide_busy()

    def save_current_settings(self):
        files_left, files_right = self.split_manager.get_open_files()
        files_per_group = self.split_manager.get_open_files_per_group()

        # Собираем закладки со всех открытых вкладок
        bookmarks = {}
        for group in self.split_manager.iter_groups():
            for i in range(group.count()):
                viewer = group.widget(i)
                if isinstance(viewer, LogViewerWidget):
                    bm = viewer.model.get_bookmarks_sorted()
                    if bm:
                        bookmarks[viewer.file_path] = bm

        # Подмешиваем закладки для НЕ открытых сейчас файлов из старых настроек -
        # чтобы не терять их если файл закрыли и больше не открывали в этой сессии
        old_bm = self.settings.get("bookmarks", {})
        for path, bm in old_bm.items():
            if path not in bookmarks and bm:
                bookmarks[path] = bm

        data = {
            "theme": self.current_theme_name,
            "font_size": self.current_font_size,
            # Legacy для совместимости со старыми версиями приложения - первые
            # две группы записываются в files_left/files_right.
            "files_left": files_left,
            "files_right": files_right,
            # Новый формат - список путей по каждой группе в порядке panels.
            # Если групп больше двух, восстанавливать сессию нужно отсюда.
            "files_per_group": files_per_group,
            "bookmarks": bookmarks,
            "ui_features": self.ui_features,
            # Имена / цвета / collapsed групп - сохраняются при переименовании,
            # смене цвета, свёртывании и при выходе из приложения.
            "group_configs": self.split_manager.get_group_configs(),
            # Режим расположения групп: 'splitter' или 'stack'.
            "group_layout_mode": self.settings.get("group_layout_mode", "splitter"),
        }
        save_settings(data)
        # Обновляем кеш в self.settings, чтобы load_file тут же увидел свежие закладки
        self.settings = data

    def closeEvent(self, event: QCloseEvent):
        self.save_current_settings()
        super().closeEvent(event)
