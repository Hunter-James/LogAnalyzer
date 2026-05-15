import os
import sys
import ctypes
import datetime
import tracemalloc

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:  # pragma: no cover
    psutil = None
    _HAS_PSUTIL = False

from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLineEdit, QLabel, QFileDialog, QProgressBar,
                             QMessageBox, QStyle, QFrame, QCheckBox, QApplication)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QIcon, QKeySequence, QCloseEvent, QShortcut

from config import THEMES, APP_VERSION, DEFAULT_UI_FEATURES, load_settings, save_settings
from gui.log_viewer import LogViewerWidget
from gui.tab_manager import SplitManager
from gui.settings import SettingsDialog
from gui.help_dialog import HelpDialog


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

        sc_snapshot = QShortcut(QKeySequence("Ctrl+Shift+M"), self)
        sc_snapshot.setContext(Qt.ShortcutContext.WindowShortcut)
        sc_snapshot.activated.connect(self._take_memory_snapshot)

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

        # Main content is now the SplitManager
        self.split_manager = SplitManager()
        self.split_manager.activeTabChanged.connect(self.on_active_tab_changed)

    def create_widgets(self):
        self.btn_open = QPushButton("Открыть файл")
        self.btn_open.clicked.connect(self.open_file_dialog)
        self.btn_settings = QPushButton("Настройки")
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_help = QPushButton("Справка")
        self.btn_help.setToolTip("Открыть окно со справкой по функционалу (F1)")
        self.btn_help.clicked.connect(self.open_help)

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
            self.btn_open, self.btn_settings, self.btn_help,
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
        for group in [self.split_manager.left_tabs, self.split_manager.right_tabs]:
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

        dlg = SettingsDialog(
            self.current_theme_name, self.current_font_size, self.ui_features, self
        )
        dlg.previewChanged.connect(self._preview_appearance)

        if dlg.exec():
            theme, size, features = dlg.get_settings()
            self.current_font_size = size
            self.ui_features = features
            self.apply_theme(theme)
            self._apply_ui_features_everywhere()
            self.save_current_settings()
        else:
            # Откатываем live-превью на исходные настройки (без сохранения)
            self.current_font_size = original_font_size
            self.apply_theme(original_theme)

    def _preview_appearance(self, theme_name, font_size):
        """Слот live-превью из SettingsDialog: применяет тему и размер шрифта
        прямо во время выбора, без сохранения в settings.json."""
        self.current_font_size = font_size
        self.apply_theme(theme_name)

    def _apply_ui_features_everywhere(self):
        """Распространяет настройки видимости на главное окно и все открытые вьюверы."""
        # chk_group в главном тулбаре - часть фичи "group_dupes"
        self.chk_group.setVisible(self.ui_features.get("group_dupes", True))
        # При скрытии глобально снимаем галочку, иначе фильтр продолжит группировать
        if not self.ui_features.get("group_dupes", True) and self.chk_group.isChecked():
            self.chk_group.setChecked(False)

        for group in [self.split_manager.left_tabs, self.split_manager.right_tabs]:
            for i in range(group.count()):
                viewer = group.widget(i)
                if isinstance(viewer, LogViewerWidget):
                    viewer.apply_ui_features(self.ui_features)

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
        for file_name in file_names:
            self.load_file(file_name)

    # Максимум одновременно загруженных в RAM табов. Свыше - самый давно
    # неактивный выгружается через viewer.unload() (см. _enforce_loaded_lru).
    MAX_LOADED_VIEWERS = 3

    def load_file(self, file_path, side="active", lazy=False):
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

    def on_loading_finished(self):
        self.progress_bar.setVisible(False)
        self.btn_open.setEnabled(True)

    def on_active_tab_changed(self, viewer):
        # Показываем полный путь активного файла в заголовке окна (стиль Notepad++)
        if viewer is not None and hasattr(viewer, 'file_path'):
            self.setWindowTitle(f"{viewer.file_path} - Log Analyzer v{APP_VERSION}")
        else:
            self.setWindowTitle(f"Log Analyzer v{APP_VERSION}")

        # Lazy-load: если активировали виджет, который не загружен (после
        # restore_session или после LRU-выгрузки) - грузим его сейчас.
        if isinstance(viewer, LogViewerWidget) and not viewer._loaded:
            viewer.ensure_loaded()
            self.progress_bar.setVisible(True)
            self.btn_open.setEnabled(False)

        if isinstance(viewer, LogViewerWidget) and viewer._loaded:
            self._lru_touch(viewer)

        # Освобождаем тяжёлые кэши (индекс «История кода») у НЕактивных
        # viewer'ов. Дополнительно к LRU-выгрузке полных модели - дешёвая мера.
        for group in (self.split_manager.left_tabs, self.split_manager.right_tabs):
            for i in range(group.count()):
                w = group.widget(i)
                if w is viewer:
                    continue
                if isinstance(w, LogViewerWidget):
                    w.release_heavy_caches()

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
            for group in (self.split_manager.left_tabs,
                          self.split_manager.right_tabs):
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
                # Сводка по табам
                for group_name, group in (
                    ("left", self.split_manager.left_tabs),
                    ("right", self.split_manager.right_tabs),
                ):
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
        """Пока загруженных табов больше лимита - выгружает самый давно
        неактивный (но никогда не текущий активный). Снимает viewer'ов,
        которые уже unload()-нулись извне."""
        # Сначала чистим тех, кто уже разгрузился (не должен висеть в LRU)
        self._lru_loaded = [v for v in self._lru_loaded if getattr(v, '_loaded', False)]
        while len(self._lru_loaded) > self.MAX_LOADED_VIEWERS:
            # Берём самого старого, но никогда - текущий активный
            victim = None
            for v in self._lru_loaded:
                if v is not keep_active:
                    victim = v
                    break
            if victim is None:
                return
            victim.unload()
            self._lru_loaded.remove(victim)

    def on_global_filter_changed(self):
        if self.updating_ui:
            return

        # Apply to ALL viewers
        info = self.chk_info.isChecked()
        debug = self.chk_debug.isChecked()
        warn = self.chk_warn.isChecked()
        error = self.chk_error.isChecked()
        group_dupes = self.chk_group.isChecked()

        for group in [self.split_manager.left_tabs, self.split_manager.right_tabs]:
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
            drop_pos = event.position().toPoint()

            mapped_pos = self.split_manager.mapFrom(self, drop_pos)

            side = "active"

            if self.split_manager.right_tabs.isVisible():
                if self.split_manager.left_tabs.geometry().contains(mapped_pos):
                    side = "left"
                elif self.split_manager.right_tabs.geometry().contains(mapped_pos):
                    side = "right"

            for url in event.mimeData().urls():
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    self.load_file(file_path, side=side)

            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def restore_session(self):
        """Восстанавливает табы из settings.json. Чтобы не грузить все логи
        сразу при старте (легко съесть RAM при 4+ больших логах), создаём
        их в lazy-режиме - таб виден, имя файла видно, но парсинг не идёт.
        Файл загружается только при первой активации таба
        (см. on_active_tab_changed → viewer.ensure_loaded())."""
        files_left = self.settings.get("files_left", [])
        files_right = self.settings.get("files_right", [])

        for f in files_left:
            if os.path.exists(f):
                self.load_file(f, side="left", lazy=True)

        for f in files_right:
            if os.path.exists(f):
                self.load_file(f, side="right", lazy=True)

        # После всех silent-add'ов активируем верхний таб - его юзер увидит
        # первым, его и грузим (один файл, остальные остаются lazy и ждут
        # клика). Если оба сплита пусты - ничего не делаем.
        current = (self.split_manager.right_tabs.currentWidget()
                   if self.split_manager.right_tabs.isVisible()
                   else None) or self.split_manager.left_tabs.currentWidget()
        if isinstance(current, LogViewerWidget):
            self.split_manager.activeTabChanged.emit(current)

    def save_current_settings(self):
        files_left, files_right = self.split_manager.get_open_files()

        # Собираем закладки со всех открытых вкладок
        bookmarks = {}
        for group in [self.split_manager.left_tabs, self.split_manager.right_tabs]:
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
            "files_left": files_left,
            "files_right": files_right,
            "bookmarks": bookmarks,
            "ui_features": self.ui_features,
        }
        save_settings(data)
        # Обновляем кеш в self.settings, чтобы load_file тут же увидел свежие закладки
        self.settings = data

    def closeEvent(self, event: QCloseEvent):
        self.save_current_settings()
        super().closeEvent(event)
