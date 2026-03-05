import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QSplitter, QAbstractItemView, 
                             QMessageBox, QApplication)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QKeySequence

from core.models import LogModel
from core.workers import LogLoader
from gui.custom_widgets import ScalableListView, ScalableTextEdit
from config import THEMES

class LogViewerWidget(QWidget):
    # Signals to notify MainWindow about state changes
    statsChanged = pyqtSignal(dict)
    progressChanged = pyqtSignal(int)
    loadingFinished = pyqtSignal()
    
    def __init__(self, file_path, theme_name, font_size, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.current_theme_name = theme_name
        self.current_font_size = font_size
        
        self.stats = {}
        self.preserved_real_index = None
        
        # Filter states (local to this tab)
        self.filter_state = {
            "info": True,
            "debug": True,
            "warn": True,
            "error": True,
            "search": ""
        }

        self.setup_ui()
        self.apply_theme(theme_name, font_size)
        
        # Load the file immediately
        self.load_file()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Vertical)

        # Log List
        self.model = LogModel()
        self.log_view = ScalableListView()
        self.log_view.setUniformItemSizes(True)
        self.log_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.log_view.setModel(self.model)
        
        # Details View
        self.details_view = ScalableTextEdit()
        self.details_view.setReadOnly(True)

        self.splitter.addWidget(self.log_view)
        self.splitter.addWidget(self.details_view)
        self.splitter.setSizes([600, 250])

        layout.addWidget(self.splitter)

        # Connections
        self.log_view.selectionModel().selectionChanged.connect(self.on_selection_changed)
        self.log_view.zoomRequest.connect(self.on_zoom_request)
        self.details_view.zoomRequest.connect(self.on_zoom_request)
        self.model.filterFinished.connect(self.on_filter_finished_scroll)

    def load_file(self):
        self.loader = LogLoader(self.file_path)
        self.loader.progress.connect(self.progressChanged.emit)
        self.loader.finished.connect(self.on_load_finished)
        self.loader.start()

    def on_load_finished(self, entries, stats, error_msg):
        if error_msg:
            QMessageBox.critical(self, "Error", f"Failed to load file:\n{error_msg}")
            self.loadingFinished.emit()
            return

        self.model.set_entries(entries)
        self.stats = stats
        self.statsChanged.emit(stats)
        self.loadingFinished.emit()

        if self.model.rowCount() > 0:
            self.log_view.scrollToBottom()
            
        # Apply initial filters
        self.refresh_view()

    def apply_theme(self, theme_name, font_size):
        self.current_theme_name = theme_name
        self.current_font_size = font_size
        t = THEMES[theme_name]
        
        # Apply font to details view
        font = QFont(t['mono_font'], font_size)
        self.details_view.setFont(font)
        
        # Update model theme
        self.model.set_theme(theme_name, font_size)
        
        # We don't set stylesheet here, MainWindow handles global stylesheet, 
        # but specific widget styles might need updates if we add them later.

    def on_zoom_request(self, delta):
        # Propagate zoom to parent (MainWindow) to handle global setting save
        # But also update locally immediately
        if delta > 0:
            self.current_font_size = min(24, self.current_font_size + 1)
        else:
            self.current_font_size = max(6, self.current_font_size - 1)
        
        self.apply_theme(self.current_theme_name, self.current_font_size)
        
        # Notify parent if possible (optional, for saving settings)
        window = self.window()
        if hasattr(window, 'on_zoom_request'):
            window.on_zoom_request(delta)

    def set_filters(self, info, debug, warn, error, search_text):
        self.filter_state["info"] = info
        self.filter_state["debug"] = debug
        self.filter_state["warn"] = warn
        self.filter_state["error"] = error
        self.filter_state["search"] = search_text
        self.refresh_view()

    def refresh_view(self):
        # Capture selection
        current_index = self.log_view.currentIndex()
        if current_index.isValid():
            self.preserved_real_index = self.model.get_real_index(current_index.row())
        else:
            self.preserved_real_index = None

        self.model.update_filters(
            self.filter_state["info"],
            self.filter_state["debug"],
            self.filter_state["error"],
            self.filter_state["warn"],
            self.filter_state["search"]
        )

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
