import sys
import re
import os
import time
import json
import urllib.request
import subprocess
from datetime import datetime

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLineEdit, QLabel,
                             QFileDialog, QListView, QProgressBar,
                             QSplitter, QMessageBox, QAbstractItemView, QTextEdit, QStyle, QFrame, QCheckBox, QDialog,
                             QComboBox, QSpinBox, QFormLayout)
from PyQt6.QtCore import (Qt, QAbstractListModel, QModelIndex, QThread,
                          pyqtSignal, QSize, QRect, QTimer, QEvent)
from PyQt6.QtGui import QColor, QFont, QPainter, QBrush, QPen, QKeySequence, QWheelEvent, QIcon

# --- Configuration ---
APP_VERSION = "1.0.0"
# TODO: CHANGE THIS TO YOUR GITHUB REPO "username/repository"
GITHUB_REPO = "your_username/your_repo_name"

# --- Theme Definitions ---
THEMES = {
    "Default": {
        "layout": "top",
        "bg_main": "#353535",
        "bg_panel": "#353535",
        "border": "#252525",
        "text_main": "#FFFFFF",
        "text_muted": "#D4D4D4",
        "accent": "#2A82DA",
        "selection": "#264F78",
        "font_family": "Segoe UI",
        "mono_font": "Consolas",
        "info": "#2E8B57", "debug": "#4682B4", "warn": "#FFA500", "error": "#CD5C5C"
    },
    "Minimalist Black": {
        "layout": "top",
        "bg_main": "#1E1E1E",
        "bg_panel": "#252526",
        "border": "#3E3E42",
        "text_main": "#CCCCCC",
        "text_muted": "#858585",
        "accent": "#007ACC",
        "selection": "#37373D",
        "font_family": "Segoe UI",
        "mono_font": "Consolas",
        "info": "#4EC9B0", "debug": "#569CD6", "warn": "#DCDCAA", "error": "#F44747"
    },
    "Minimalist White": {
        "layout": "top",
        "bg_main": "#FFFFFF",
        "bg_panel": "#F3F3F3",
        "border": "#E0E0E0",
        "text_main": "#333333",
        "text_muted": "#666666",
        "accent": "#0078D7",
        "selection": "#E8E8E8",
        "font_family": "Segoe UI",
        "mono_font": "Consolas",
        "info": "#008000", "debug": "#0000FF", "warn": "#FFA500", "error": "#FF0000"
    },
    "Windows 95": {
        "layout": "top",
        "bg_main": "#C0C0C0",
        "bg_panel": "#C0C0C0",
        "border": "#808080",
        "text_main": "#000000",
        "text_muted": "#404040",
        "accent": "#000080",
        "selection": "#FFFFFF",
        "font_family": "MS Sans Serif",
        "mono_font": "Courier New",
        "info": "#008000", "debug": "#000080", "warn": "#808000", "error": "#800000"
    },
    "Hacker": {
        "layout": "side",
        "bg_main": "#0A0A0A",
        "bg_panel": "#111111",
        "border": "#444444",
        "text_main": "#E0E0E0",
        "text_muted": "#666666",
        "accent": "#FFFFFF",
        "selection": "#333333",
        "font_family": "Consolas",
        "mono_font": "Consolas",
        "info": "#00FF00", "debug": "#00FFFF", "warn": "#FFFF00", "error": "#FF0000"
    }
}


# --- Custom Widgets for Zooming ---
class ScalableListView(QListView):
    zoomRequest = pyqtSignal(int)

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            self.zoomRequest.emit(delta)
            event.accept()
        else:
            super().wheelEvent(event)


class ScalableTextEdit(QTextEdit):
    zoomRequest = pyqtSignal(int)

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            self.zoomRequest.emit(delta)
            event.accept()
        else:
            super().wheelEvent(event)


# --- Data Structure ---
class LogEntry:
    __slots__ = ('timestamp', 'level', 'message', 'preview', 'full_line')

    def __init__(self, timestamp, level, message, full_line):
        self.timestamp = timestamp
        self.level = level
        self.message = message
        self.full_line = full_line
        first_line = message.split('\n', 1)[0]
        self.preview = first_line[:250] + "..." if len(first_line) > 250 else first_line


# --- Worker Thread for Loading Files ---
class LogLoader(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(list, dict, str)

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def run(self):
        entries = []
        stats = {"INFO": 0, "DEBUG": 0, "ERROR": 0, "WARN": 0}
        log_pattern = re.compile(r'^(\d{2}:\d{2}:\d{2}\.\d{3})\s+\[\s*(INFO|DEBUG|ERROR|WARN)\s*\]')

        try:
            file_size = os.path.getsize(self.file_path)
            bytes_read = 0
            last_emit_time = 0

            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                current_entry = None
                for line in f:
                    line_len = len(line.encode('utf-8'))
                    bytes_read += line_len

                    current_time = time.time()
                    if current_time - last_emit_time > 0.1:
                        progress_pct = int((bytes_read / file_size) * 100)
                        self.progress.emit(progress_pct)
                        last_emit_time = current_time

                    match = log_pattern.match(line)
                    if match:
                        if current_entry:
                            entries.append(current_entry)
                        timestamp_str = match.group(1)
                        level_str = match.group(2)
                        if level_str in stats:
                            stats[level_str] += 1
                        current_entry = LogEntry(timestamp_str, level_str, line.strip(), line)
                    else:
                        if current_entry:
                            if len(current_entry.message) < 50000:
                                current_entry.message += "\n" + line.strip()
                            current_entry.full_line += line
                        else:
                            current_entry = LogEntry("", "UNKNOWN", line.strip(), line)

                if current_entry:
                    entries.append(current_entry)

            self.progress.emit(100)
            self.finished.emit(entries, stats, "")
        except Exception as e:
            self.finished.emit([], {}, str(e))


# --- Worker Thread for Filtering ---
class FilterWorker(QThread):
    finished = pyqtSignal(list)

    def __init__(self, entries, show_info, show_debug, show_error, show_warn, search_text):
        super().__init__()
        self.entries = entries
        self.show_info = show_info
        self.show_debug = show_debug
        self.show_error = show_error
        self.show_warn = show_warn
        self.search_text = search_text
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        active_levels = set()
        if self.show_info: active_levels.add("INFO")
        if self.show_debug: active_levels.add("DEBUG")
        if self.show_error: active_levels.add("ERROR")
        if self.show_warn: active_levels.add("WARN")

        search_text = self.search_text
        entries = self.entries

        if not search_text:
            new_indices = [
                i for i, e in enumerate(entries)
                if e.level in active_levels or e.level == "UNKNOWN"
            ]
        else:
            search_regex = None
            try:
                search_regex = re.compile(search_text, re.IGNORECASE)
            except re.error:
                search_regex = None

            if search_regex:
                match = search_regex.search
                new_indices = [
                    i for i, e in enumerate(entries)
                    if (e.level in active_levels or e.level == "UNKNOWN") and match(e.full_line)
                ]
            else:
                search_lower = search_text.lower()
                new_indices = [
                    i for i, e in enumerate(entries)
                    if (e.level in active_levels or e.level == "UNKNOWN") and search_lower in e.full_line.lower()
                ]

        if not self._is_cancelled:
            self.finished.emit(new_indices)


# --- Update Worker ---
class UpdateWorker(QThread):
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, str)  # success, download_url, version

    def run(self):
        try:
            self.status_signal.emit("Checking for updates...")
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

            # Create request with User-Agent to avoid 403 Forbidden
            req = urllib.request.Request(url, headers={'User-Agent': 'LogAnalyzer-Updater'})

            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())

            latest_version = data['tag_name'].lstrip('v')

            # Simple version compare (assuming semantic versioning)
            if latest_version != APP_VERSION:
                # Find the .exe asset
                download_url = None
                for asset in data['assets']:
                    if asset['name'].endswith('.exe'):
                        download_url = asset['browser_download_url']
                        break

                if download_url:
                    self.finished_signal.emit(True, download_url, latest_version)
                else:
                    self.finished_signal.emit(False, "", "No executable found in release.")
            else:
                self.finished_signal.emit(False, "", "You are using the latest version.")

        except Exception as e:
            self.finished_signal.emit(False, "", str(e))


# --- Model ---
class LogModel(QAbstractListModel):
    filterFinished = pyqtSignal()

    def __init__(self, entries=None):
        super().__init__()
        self._entries = entries or []
        self._filtered_indices = list(range(len(self._entries)))
        self._show_info = True
        self._show_debug = True
        self._show_error = True
        self._show_warn = True
        self._search_text = ""

        self.current_theme = THEMES["Default"]
        self.font_size = 10
        self.update_colors()

        self.filter_worker = None

    def update_colors(self):
        self.color_info = QColor(self.current_theme["info"])
        self.color_debug = QColor(self.current_theme["debug"])
        self.color_error = QColor(self.current_theme["error"])
        self.color_warn = QColor(self.current_theme["warn"])
        self.color_default = QColor(self.current_theme["text_main"])
        self.font_mono = QFont(self.current_theme["mono_font"], self.font_size)

    def set_theme(self, theme_name, font_size):
        self.current_theme = THEMES[theme_name]
        self.font_size = font_size
        self.update_colors()
        self.layoutChanged.emit()

    def rowCount(self, parent=QModelIndex()):
        return len(self._filtered_indices)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._filtered_indices):
            return None

        real_index = self._filtered_indices[index.row()]
        entry = self._entries[real_index]

        if role == Qt.ItemDataRole.DisplayRole:
            return entry.preview
        if role == Qt.ItemDataRole.UserRole:
            return entry.message
        if role == Qt.ItemDataRole.ForegroundRole:
            if entry.level == "INFO": return self.color_info
            if entry.level == "DEBUG": return self.color_debug
            if entry.level == "ERROR": return self.color_error
            if entry.level == "WARN": return self.color_warn
            return self.color_default
        if role == Qt.ItemDataRole.FontRole:
            return self.font_mono
        return None

    def set_entries(self, entries):
        self.beginResetModel()
        self._entries = entries
        self._filtered_indices = list(range(len(entries)))
        self.endResetModel()
        self.apply_filters_async()

    def update_filters(self, show_info, show_debug, show_error, show_warn, search_text):
        self._show_info = show_info
        self._show_debug = show_debug
        self._show_error = show_error
        self._show_warn = show_warn
        self._search_text = search_text
        self.apply_filters_async()

    def apply_filters_async(self):
        if self.filter_worker and self.filter_worker.isRunning():
            self.filter_worker.cancel()
            self.filter_worker.wait()

        self.filter_worker = FilterWorker(
            self._entries,
            self._show_info, self._show_debug, self._show_error, self._show_warn,
            self._search_text
        )
        self.filter_worker.finished.connect(self.on_filter_finished)
        self.filter_worker.start()

    def on_filter_finished(self, new_indices):
        self.beginResetModel()
        self._filtered_indices = new_indices
        self.endResetModel()
        self.filterFinished.emit()

    def get_real_index(self, row):
        if 0 <= row < len(self._filtered_indices):
            return self._filtered_indices[row]
        return None

    def find_row_by_real_index(self, real_index):
        try:
            return self._filtered_indices.index(real_index)
        except ValueError:
            return -1


# --- Settings Dialog ---
class SettingsDialog(QDialog):
    def __init__(self, current_theme, current_font_size, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(350, 250)

        # Apply Theme to Dialog
        t = THEMES[current_theme]
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t['bg_main']}; color: {t['text_main']}; }}
            QLabel {{ color: {t['text_main']}; }}
            QComboBox {{ 
                background-color: {t['bg_panel']}; 
                color: {t['text_main']}; 
                border: 1px solid {t['border']};
                padding: 4px;
            }}
            QSpinBox {{ 
                background-color: {t['bg_panel']}; 
                color: {t['text_main']}; 
                border: 1px solid {t['border']};
                padding: 4px;
            }}
            QPushButton {{
                background-color: {t['bg_panel']};
                color: {t['text_main']};
                border: 1px solid {t['border']};
                padding: 6px 12px;
            }}
            QPushButton:hover {{ background-color: {t['accent']}; color: white; }}
        """)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(THEMES.keys())
        self.theme_combo.setCurrentText(current_theme)

        self.font_spin = QSpinBox()
        self.font_spin.setRange(8, 24)
        self.font_spin.setValue(current_font_size)

        form.addRow("Theme:", self.theme_combo)
        form.addRow("Font Size:", self.font_spin)

        layout.addLayout(form)

        # Update Section
        update_frame = QFrame()
        update_frame.setFrameShape(QFrame.Shape.StyledPanel)
        update_layout = QVBoxLayout(update_frame)

        self.lbl_version = QLabel(f"Current Version: {APP_VERSION}")
        self.btn_update = QPushButton("Check for Updates")
        self.btn_update.clicked.connect(self.check_for_updates)

        update_layout.addWidget(self.lbl_version)
        update_layout.addWidget(self.btn_update)
        layout.addWidget(update_frame)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

        self.update_worker = None

    def get_settings(self):
        return self.theme_combo.currentText(), self.font_spin.value()

    def check_for_updates(self):
        self.btn_update.setEnabled(False)
        self.update_worker = UpdateWorker()
        self.update_worker.status_signal.connect(self.btn_update.setText)
        self.update_worker.finished_signal.connect(self.on_update_checked)
        self.update_worker.start()

    def on_update_checked(self, success, url, message):
        self.btn_update.setEnabled(True)
        self.btn_update.setText("Check for Updates")

        if success:
            reply = QMessageBox.question(
                self, "Update Available",
                f"New version {message} is available.\nDo you want to download and install it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.perform_update(url)
        else:
            QMessageBox.information(self, "Update Info", message)

    def perform_update(self, url):
        # 1. Download new exe
        try:
            self.btn_update.setText("Downloading...")
            self.btn_update.setEnabled(False)
            QApplication.processEvents()

            new_exe_name = "update.tmp"
            urllib.request.urlretrieve(url, new_exe_name)

            # 2. Create Batch Script
            current_exe = sys.executable
            batch_script = f"""
@echo off
timeout /t 2 /nobreak > NUL
del "{current_exe}"
move "{new_exe_name}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
            """

            with open("update.bat", "w") as f:
                f.write(batch_script)

            # 3. Launch Batch and Exit
            subprocess.Popen("update.bat", shell=True)
            QApplication.quit()

        except Exception as e:
            QMessageBox.critical(self, "Update Failed", str(e))
            self.btn_update.setText("Check for Updates")
            self.btn_update.setEnabled(True)


# --- Main Window ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Log Analyzer Pro v{APP_VERSION}")
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
        sidebar.setFixedWidth(250)
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
        text = f"Total: {total:,} | Info: {self.stats.get('INFO', 0):,} | Error: {self.stats.get('ERROR', 0):,}"
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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())
