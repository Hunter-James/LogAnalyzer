import os
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLineEdit, QLabel, QFileDialog, QProgressBar, QSplitter,
                             QMessageBox, QAbstractItemView, QStyle, QFrame, QCheckBox, QApplication)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QIcon, QKeySequence

from config import THEMES, APP_VERSION
from core.models import LogModel
from core.workers import LogLoader
from gui.custom_widgets import ScalableListView, ScalableTextEdit
from gui.settings import SettingsDialog

# --- Main Window ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Log Analyzer v{APP_VERSION}")
        self.resize(1400, 900)
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))

        self.current_theme_name = "Default"
        self.current_font_size = 10

        self.setup_ui()
        self.apply_theme(self.current_theme_name)

        self.model = LogModel()
        self.log_view.setModel(self.model)
        self.log_view.selectionModel().selectionChanged.connect(self.on_selection_changed)

        # Connect Zoom Signals
        self.log_view.zoomRequest.connect(self.on_zoom_request)
        self.details_view.zoomRequest.connect(self.on_zoom_request)

        # Connect Filter Finished Signal for Scroll Restoration
        self.model.filterFinished.connect(self.on_filter_finished_scroll)

        self.current_file = None
        self.stats = {}
        self.preserved_real_index = None

        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(50)
        self.search_timer.timeout.connect(self.trigger_search)

    def setup_ui(self):
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.root_layout = QVBoxLayout(self.central_widget)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)

        self.widget_holder = QWidget(self.central_widget)
        self.widget_holder.setVisible(False)

        self.create_widgets()

        self.content_widget = QWidget()
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Vertical)

        # Use Custom Scalable Widgets
        self.log_view = ScalableListView()
        self.log_view.setUniformItemSizes(True)
        self.log_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.splitter.addWidget(self.log_view)

        self.details_view = ScalableTextEdit()
        self.details_view.setReadOnly(True)
        self.splitter.addWidget(self.details_view)
        self.splitter.setSizes([600, 250])

        content_layout.addWidget(self.splitter)

    def create_widgets(self):
        self.btn_open = QPushButton("Open File")
        self.btn_open.clicked.connect(self.open_file_dialog)
        self.btn_settings = QPushButton("Settings")
        self.btn_settings.clicked.connect(self.open_settings)

        self.chk_info = QCheckBox("INFO")
        self.chk_info.setChecked(True)
        self.chk_info.stateChanged.connect(self.refresh_view)
        self.chk_debug = QCheckBox("DEBUG")
        self.chk_debug.setChecked(True)
        self.chk_debug.stateChanged.connect(self.refresh_view)
        self.chk_warn = QCheckBox("WARN")
        self.chk_warn.setChecked(True)
        self.chk_warn.stateChanged.connect(self.refresh_view)
        self.chk_error = QCheckBox("ERROR")
        self.chk_error.setChecked(True)
        self.chk_error.stateChanged.connect(self.refresh_view)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search...")
        self.search_input.textChanged.connect(self.on_search_text_changed)

        self.lbl_file_name = QLabel("No File")
        self.lbl_stats = QLabel("")
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

    def detach_widgets(self):
        widgets = [
            self.btn_open, self.btn_settings,
            self.chk_info, self.chk_debug, self.chk_warn, self.chk_error,
            self.search_input,
            self.lbl_file_name, self.lbl_stats, self.progress_bar,
            self.content_widget
        ]
        for w in widgets:
            w.setParent(self.widget_holder)

        self.btn_open.setObjectName("")
        self.btn_settings.setObjectName("")

    def apply_theme(self, theme_name):
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

        if hasattr(self, 'model'):
            self.model.set_theme(theme_name, self.current_font_size)
        if hasattr(self, 'details_view'):
            font = QFont(t['mono_font'], self.current_font_size)
            self.details_view.setFont(font)

    def update_fonts(self):
        t = THEMES[self.current_theme_name]
        if hasattr(self, 'model'):
            self.model.set_theme(self.current_theme_name, self.current_font_size)
        if hasattr(self, 'details_view'):
            font = QFont(t['mono_font'], self.current_font_size)
            self.details_view.setFont(font)

    def build_top_layout(self, t):
        toolbar = QFrame()
        toolbar.setObjectName("Panel")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(10, 5, 10, 5)

        if self.current_theme_name == "Default":
            self.btn_open.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
            self.btn_settings.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        else:
            self.btn_open.setIcon(QIcon())
            self.btn_settings.setIcon(QIcon())

        tb_layout.addWidget(self.btn_open)
        tb_layout.addWidget(self.btn_settings)
        tb_layout.addSpacing(20)
        tb_layout.addWidget(self.chk_info)
        tb_layout.addWidget(self.chk_debug)
        tb_layout.addWidget(self.chk_warn)
        tb_layout.addWidget(self.chk_error)
        tb_layout.addSpacing(20)
        tb_layout.addWidget(QLabel("Search:"))
        tb_layout.addWidget(self.search_input)

        self.root_layout.addWidget(toolbar)
        self.root_layout.addWidget(self.content_widget)

        status = QFrame()
        status.setObjectName("Panel")
        st_layout = QHBoxLayout(status)
        st_layout.setContentsMargins(10, 2, 10, 2)
        st_layout.addWidget(self.lbl_file_name)
        st_layout.addStretch()
        st_layout.addWidget(self.lbl_stats)
        st_layout.addWidget(self.progress_bar)

        self.root_layout.addWidget(status)

    def build_side_layout(self, t):
        h_layout = QHBoxLayout()
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Panel")
        sidebar.setFixedWidth(430)
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
        sb_layout.addSpacing(10)
        sb_layout.addWidget(QLabel("FILTERS"))
        sb_layout.addWidget(self.chk_info)
        sb_layout.addWidget(self.chk_debug)
        sb_layout.addWidget(self.chk_warn)
        sb_layout.addWidget(self.chk_error)
        sb_layout.addSpacing(10)
        sb_layout.addWidget(QLabel("SEARCH"))
        sb_layout.addWidget(self.search_input)
        sb_layout.addStretch()
        sb_layout.addWidget(self.lbl_file_name)
        sb_layout.addWidget(self.lbl_stats)
        sb_layout.addWidget(self.progress_bar)

        h_layout.addWidget(sidebar)
        h_layout.addWidget(self.content_widget)

        container = QWidget()
        container.setLayout(h_layout)
        self.root_layout.addWidget(container)

    def apply_stylesheet(self, t):
        qss = f"""
            QMainWindow {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QWidget {{ font-family: '{t['font_family']}', sans-serif; color: {t['text_main']}; }}
            QFrame#Panel {{ background-color: {t['bg_panel']}; border: 1px solid {t['border']}; }}
            QLineEdit {{
                background-color: {t['bg_main']};
                border: 1px solid {t['border']};
                padding: 6px;
                color: {t['text_main']};
            }}
            QListView {{ background-color: {t['bg_main']}; border: none; }}
            QListView::item {{ padding: 4px; border-bottom: 1px solid {t['border']}; }}
            QListView::item:selected {{ background-color: {t['selection']}; color: {t['text_main']}; }}
            QTextEdit {{
                background-color: {t['bg_panel']};
                border-top: 1px solid {t['border']};
                color: {t['text_main']};
                font-family: '{t['mono_font']}';
            }}
            QSplitter::handle {{ background-color: {t['border']}; height: 2px; }}
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
        elif self.current_theme_name == "Brutalist":
            qss += f"""
                QPushButton {{
                    background-color: {t['bg_main']};
                    color: {t['accent']};
                    border: 1px solid {t['accent']};
                    padding: 6px 12px;
                    font-weight: bold;
                }}
                QPushButton:hover {{ background-color: {t['accent']}; color: {t['bg_main']}; }}
                QPushButton#OpenBtn {{
                    padding: 15px;
                    font-weight: 900;
                    font-size: 14px;
                    text-transform: uppercase;
                    letter-spacing: 2px;
                }}
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
        dlg = SettingsDialog(self.current_theme_name, self.current_font_size, self)
        if dlg.exec():
            theme, size = dlg.get_settings()
            self.current_font_size = size
            self.apply_theme(theme)

    def on_zoom_request(self, delta):
        if delta > 0:
            self.current_font_size = min(24, self.current_font_size + 1)
        else:
            self.current_font_size = max(6, self.current_font_size - 1)
        self.update_fonts()

    def open_file_dialog(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Open Log File", "", "Log Files (*.log *.txt);;All Files (*)")
        if file_name:
            self.load_file(file_name)

    def load_file(self, file_path):
        self.current_file = file_path
        self.lbl_file_name.setText(os.path.basename(file_path))
        self.lbl_stats.setText("Loading...")
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.btn_open.setEnabled(False)

        self.loader = LogLoader(file_path)
        self.loader.progress.connect(self.progress_bar.setValue)
        self.loader.finished.connect(self.on_load_finished)
        self.loader.start()

    def on_load_finished(self, entries, stats, error_msg):
        self.progress_bar.setVisible(False)
        self.btn_open.setEnabled(True)

        if error_msg:
            QMessageBox.critical(self, "Error", f"Failed to load file:\n{error_msg}")
            self.lbl_stats.setText("Error")
            return

        self.model.set_entries(entries)
        self.stats = stats
        self.update_stats_display()

        if self.model.rowCount() > 0:
            self.log_view.scrollToBottom()

    def update_stats_display(self):
        total = len(self.model._entries)
        text = f"Total: {total:,} | Info: {self.stats.get('INFO', 0):,} | Error: {self.stats.get('ERROR', 0):,} | Debug: {self.stats.get('DEBUG', 0):,} | Warn: {self.stats.get('WARN', 0):,}"
        self.lbl_stats.setText(text)

    def on_search_text_changed(self, text):
        self.search_timer.start()

    def trigger_search(self):
        self.refresh_view()

    def refresh_view(self):
        # Capture current selection (Real Index)
        current_index = self.log_view.currentIndex()
        if current_index.isValid():
            self.preserved_real_index = self.model.get_real_index(current_index.row())
        else:
            self.preserved_real_index = None

        self.model.update_filters(
            self.chk_info.isChecked(),
            self.chk_debug.isChecked(),
            self.chk_error.isChecked(),
            self.chk_warn.isChecked(),
            self.search_input.text()
        )

    def on_filter_finished_scroll(self):
        # Restore Selection
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

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            if self.details_view.hasFocus():
                self.details_view.copy()
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
