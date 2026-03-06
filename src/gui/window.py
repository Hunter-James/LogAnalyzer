import os
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLineEdit, QLabel, QFileDialog, QProgressBar,
                             QMessageBox, QStyle, QFrame, QCheckBox, QApplication)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QIcon, QKeySequence

from config import THEMES, APP_VERSION, load_settings, save_settings
from gui.log_viewer import LogViewerWidget
from gui.tab_manager import SplitManager
from gui.settings import SettingsDialog

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Log Analyzer v{APP_VERSION}")
        self.resize(1400, 900)
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))

        # Load settings
        settings = load_settings()
        self.current_theme_name = settings.get("theme", "Default")
        self.current_font_size = settings.get("font_size", 10)

        self.setup_ui()
        self.apply_theme(self.current_theme_name)
        
        # Flag to prevent recursive updates when syncing UI
        self.updating_ui = False

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
        self.btn_open = QPushButton("Open File")
        self.btn_open.clicked.connect(self.open_file_dialog)
        self.btn_settings = QPushButton("Settings")
        self.btn_settings.clicked.connect(self.open_settings)

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

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(150)
        self.progress_bar.setVisible(False)

    def detach_widgets(self):
        widgets = [
            self.btn_open, self.btn_settings,
            self.chk_info, self.chk_debug, self.chk_warn, self.chk_error,
            self.progress_bar,
            self.split_manager
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
        sb_layout.addSpacing(10)
        sb_layout.addWidget(QLabel("FILTERS"))
        sb_layout.addWidget(self.chk_info)
        sb_layout.addWidget(self.chk_debug)
        sb_layout.addWidget(self.chk_warn)
        sb_layout.addWidget(self.chk_error)
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
            QMainWindow, QWidget {{ background-color: {t['bg_main']}; color: {t['text_main']}; font-family: '{t['font_family']}'; }}
            QFrame#Panel {{ background-color: {t['bg_panel']}; border: 1px solid {t['border']}; }}
            QLineEdit {{ background-color: {t['bg_main']}; border: 1px solid {t['border']}; padding: 6px; color: {t['text_main']}; }}
            QListView {{ background-color: {t['bg_main']}; border: none; }}
            QListView::item {{ padding: 4px; border-bottom: 1px solid {t['border']}; }}
            QListView::item:selected {{ background-color: {t['selection']}; color: {t['text_main']}; }}
            QTextEdit {{ background-color: {t['bg_panel']}; border-top: 1px solid {t['border']}; color: {t['text_main']}; font-family: '{t['mono_font']}'; }}
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
        dlg = SettingsDialog(self.current_theme_name, self.current_font_size, self)
        if dlg.exec():
            theme, size = dlg.get_settings()
            self.current_font_size = size
            self.apply_theme(theme)
            save_settings(theme, size)

    def on_zoom_request(self, delta):
        if delta > 0:
            self.current_font_size = min(24, self.current_font_size + 1)
        else:
            self.current_font_size = max(6, self.current_font_size - 1)
        self.update_fonts()
        save_settings(self.current_theme_name, self.current_font_size)

    def open_file_dialog(self):
        file_names, _ = QFileDialog.getOpenFileNames(self, "Open Log File", "", "Log Files (*.log *.txt);;All Files (*)")
        for file_name in file_names:
            self.load_file(file_name)

    def load_file(self, file_path):
        viewer = LogViewerWidget(file_path, self.current_theme_name, self.current_font_size)
        viewer.progressChanged.connect(self.progress_bar.setValue)
        viewer.loadingFinished.connect(self.on_loading_finished)
        
        # Apply current global filters to new viewer
        viewer.set_global_filters(
            self.chk_info.isChecked(),
            self.chk_debug.isChecked(),
            self.chk_warn.isChecked(),
            self.chk_error.isChecked()
        )
        
        self.split_manager.add_tab(viewer, os.path.basename(file_path))
        self.progress_bar.setVisible(True)
        self.btn_open.setEnabled(False)

    def on_loading_finished(self):
        self.progress_bar.setVisible(False)
        self.btn_open.setEnabled(True)

    def on_active_tab_changed(self, viewer):
        # No updates needed here as stats and file name are local/removed
        pass

    def on_global_filter_changed(self):
        if self.updating_ui:
            return
            
        # Apply to ALL viewers
        info = self.chk_info.isChecked()
        debug = self.chk_debug.isChecked()
        warn = self.chk_warn.isChecked()
        error = self.chk_error.isChecked()
        
        for group in [self.split_manager.left_tabs, self.split_manager.right_tabs]:
            for i in range(group.count()):
                viewer = group.widget(i)
                if isinstance(viewer, LogViewerWidget):
                    viewer.set_global_filters(info, debug, warn, error)
            
    def keyPressEvent(self, event):
        viewer = self.split_manager.get_current_viewer()
        if viewer:
            viewer.keyPressEvent(event)
        else:
            super().keyPressEvent(event)
