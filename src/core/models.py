from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from config import THEMES
from core.workers import FilterWorker

# --- Model ---
class LogModel(QAbstractListModel):
    filterFinished = pyqtSignal()

    def __init__(self, entries=None):
        super().__init__()
        self._entries = entries or []
        # _raw_filtered_indices - все совпадения после фильтра (без группировки)
        self._raw_filtered_indices = list(range(len(self._entries)))
        # _filtered_indices - то, что реально отображается; при группировке это лидеры групп
        self._filtered_indices = list(self._raw_filtered_indices)
        # _filtered_counts - количество схлопнутых записей в каждой группе; пусто, если группировка выключена
        self._filtered_counts = []
        # _member_to_row - real_index -> row, заполняется только при группировке для быстрого find_row_by_real_index
        self._member_to_row = {}

        self._show_info = True
        self._show_debug = True
        self._show_error = True
        self._show_warn = True
        self._search_text = ""
        self._group_dupes = False
        self._loggers = None  # None = все, set = только из этого набора

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
            if self._filtered_counts:
                count = self._filtered_counts[index.row()]
                if count > 1:
                    return f"[×{count}] {entry.preview}"
            return entry.preview
        if role == Qt.ItemDataRole.UserRole:
            if self._filtered_counts:
                count = self._filtered_counts[index.row()]
                if count > 1:
                    return f"[×{count} свёрнуто] {entry.message}"
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
        self._raw_filtered_indices = list(range(len(entries)))
        self._filtered_indices = list(self._raw_filtered_indices)
        self._filtered_counts = []
        self._member_to_row = {}
        self.endResetModel()
        self.apply_filters_async()

    def update_filters(self, show_info, show_debug, show_error, show_warn,
                       search_text, group_dupes=False, loggers=None):
        self._show_info = show_info
        self._show_debug = show_debug
        self._show_error = show_error
        self._show_warn = show_warn
        self._search_text = search_text
        self._group_dupes = group_dupes
        self._loggers = loggers
        self.apply_filters_async()

    def apply_filters_async(self):
        if self.filter_worker and self.filter_worker.isRunning():
            self.filter_worker.cancel()
            self.filter_worker.wait()

        self.filter_worker = FilterWorker(
            self._entries,
            self._show_info, self._show_debug, self._show_error, self._show_warn,
            self._search_text,
            self._loggers,
        )
        self.filter_worker.finished.connect(self.on_filter_finished)
        self.filter_worker.start()

    def on_filter_finished(self, new_indices):
        self.beginResetModel()
        self._raw_filtered_indices = new_indices
        if self._group_dupes:
            (self._filtered_indices,
             self._filtered_counts,
             self._member_to_row) = self._build_groups(new_indices)
        else:
            self._filtered_indices = new_indices
            self._filtered_counts = []
            self._member_to_row = {}
        self.endResetModel()
        self.filterFinished.emit()

    def _build_groups(self, indices):
        """Схлопывает подряд идущие записи с одинаковым (level, message) в группы."""
        grouped = []
        counts = []
        member_to_row = {}
        prev_key = None
        entries = self._entries
        for idx in indices:
            e = entries[idx]
            key = (e.level, e.message)
            if grouped and key == prev_key:
                counts[-1] += 1
            else:
                grouped.append(idx)
                counts.append(1)
                prev_key = key
            member_to_row[idx] = len(grouped) - 1
        return grouped, counts, member_to_row

    def get_real_index(self, row):
        if 0 <= row < len(self._filtered_indices):
            return self._filtered_indices[row]
        return None

    def find_row_by_real_index(self, real_index):
        if self._member_to_row:
            return self._member_to_row.get(real_index, -1)
        try:
            return self._filtered_indices.index(real_index)
        except ValueError:
            return -1
