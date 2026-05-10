import re

from PyQt6.QtWidgets import (QListView, QTextEdit, QScrollBar, QStyle,
                             QStyleOptionSlider, QPlainTextEdit, QWidget)
from PyQt6.QtCore import pyqtSignal, Qt, QRect, QSize
from PyQt6.QtGui import (QWheelEvent, QPainter, QColor, QSyntaxHighlighter,
                         QTextCharFormat, QFont, QMouseEvent)

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


# --- JSON syntax highlighter (Monokai-ish) ---
class JsonSyntaxHighlighter(QSyntaxHighlighter):
    """Подсветка JSON-токенов: ключи, строки, числа, true/false/null.
    Работает на любой строке - если в строке нет JSON, ничего не подсветит."""

    def __init__(self, document):
        super().__init__(document)

        f_key = QTextCharFormat()
        f_key.setForeground(QColor("#9CDCFE"))      # светло-синий
        f_key.setFontWeight(QFont.Weight.Bold)

        f_str = QTextCharFormat()
        f_str.setForeground(QColor("#CE9178"))      # оранжевый

        f_num = QTextCharFormat()
        f_num.setForeground(QColor("#B5CEA8"))      # светло-зелёный

        f_kw = QTextCharFormat()
        f_kw.setForeground(QColor("#569CD6"))       # синий
        f_kw.setFontWeight(QFont.Weight.Bold)

        f_brk = QTextCharFormat()
        f_brk.setForeground(QColor("#FFD700"))      # жёлтый, скобки заметнее

        # Порядок имеет значение: ключ (со взглядом вперёд на :) раньше обычной строки.
        self._rules = [
            (re.compile(r'"(?:[^"\\]|\\.)*"\s*(?=:)'), f_key),
            (re.compile(r'"(?:[^"\\]|\\.)*"'), f_str),
            (re.compile(r'-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b'), f_num),
            (re.compile(r'\b(?:true|false|null)\b'), f_kw),
            (re.compile(r'[{}\[\]]'), f_brk),
        ]

    def highlightBlock(self, text):
        for regex, fmt in self._rules:
            for m in regex.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


# --- Folding gutter for FoldableJsonTextEdit ---
class _FoldingGutter(QWidget):
    """Узкая колонка слева от текстового редактора. Рисует ▼ для развёрнутых
    fold-блоков и ▶ для свёрнутых; клик переключает состояние."""

    WIDTH = 16

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor
        self.setFixedWidth(self.WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def sizeHint(self):
        return QSize(self.WIDTH, 0)

    def paintEvent(self, event):
        editor = self._editor
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#1E1E1E"))

        block = editor.firstVisibleBlock()
        offset = editor.contentOffset()
        viewport_bottom = editor.viewport().rect().bottom()

        font = painter.font()
        font.setPointSize(max(8, font.pointSize() - 1))
        painter.setFont(font)

        while block.isValid():
            geom = editor.blockBoundingGeometry(block).translated(offset)
            top = int(geom.top())
            height = int(editor.blockBoundingRect(block).height())
            if top > viewport_bottom:
                break
            if block.isVisible() and height > 0:
                bn = block.blockNumber()
                info = editor.fold_regions.get(bn)
                if info is not None:
                    glyph = "▶" if info['folded'] else "▼"
                    color = QColor("#D4D4D4") if info['folded'] else QColor("#777777")
                    painter.setPen(color)
                    painter.drawText(QRect(0, top, self.WIDTH, height),
                                     int(Qt.AlignmentFlag.AlignCenter),
                                     glyph)
            block = block.next()
        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        editor = self._editor
        offset = editor.contentOffset()
        click_y = event.position().y()
        block = editor.firstVisibleBlock()
        while block.isValid():
            geom = editor.blockBoundingGeometry(block).translated(offset)
            if geom.top() <= click_y <= geom.bottom():
                if block.blockNumber() in editor.fold_regions:
                    editor.toggle_fold(block.blockNumber())
                break
            if geom.top() > click_y:
                break
            block = block.next()


class FoldableJsonTextEdit(QPlainTextEdit):
    """QPlainTextEdit с code folding для пар { } и [ ] (по строкам отформатированного
    JSON) и подсветкой синтаксиса. Совместим по API с обычным QPlainTextEdit:
    setPlainText/clear/setExtraSelections и т.д. работают как раньше.

    Folding включается через setPlainTextWithFolding(text). При обычном setPlainText
    маркеры сворачивания не показываются."""

    zoomRequest = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        # block_number стартовой строки fold-региона -> {'end': N, 'folded': bool}
        self.fold_regions = {}

        self._gutter = _FoldingGutter(self)
        self._highlighter = JsonSyntaxHighlighter(self.document())

        self.setViewportMargins(_FoldingGutter.WIDTH, 0, 0, 0)
        self.updateRequest.connect(self._on_update_request)
        self.blockCountChanged.connect(lambda _: self._gutter.update())

    # ----- Zoom (Ctrl+Wheel) -----
    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoomRequest.emit(event.angleDelta().y())
            event.accept()
        else:
            super().wheelEvent(event)

    # ----- Layout -----
    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(cr.left(), cr.top(),
                                 _FoldingGutter.WIDTH, cr.height())

    def _on_update_request(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), _FoldingGutter.WIDTH, rect.height())
        if rect.contains(self.viewport().rect()):
            self.setViewportMargins(_FoldingGutter.WIDTH, 0, 0, 0)

    # ----- Public API -----
    def setPlainTextWithFolding(self, text):
        """Кладёт текст и вычисляет fold-регионы для парных {/} и [/]."""
        # Перед перенаполнением раскрываем все блоки старого текста
        self._unfold_all()
        self.setPlainText(text)
        self.fold_regions = self._compute_fold_regions(text)
        self._gutter.update()

    def clear(self):
        self.fold_regions = {}
        super().clear()
        self._gutter.update()

    # ----- Folding logic -----
    @staticmethod
    def _compute_fold_regions(text):
        """Сканирует строки text и находит парные { ... } и [ ... ] на разных строках.
        Простое правило (надёжное на выводе json.dumps(indent=...)):
        - строка-открыватель заканчивается на { или [
        - строка-закрыватель начинается (после whitespace) с } или ]
        Всё что между ними - содержимое fold-региона."""
        regions = {}
        stack = []  # [(start_block_num, char)]
        for i, line in enumerate(text.split('\n')):
            stripped = line.rstrip()
            if not stripped:
                continue
            ls = stripped.lstrip()
            # Сначала проверяем закрытие - строка может ОДНОВРЕМЕННО быть закрытием
            # одного блока и открытием следующего: "}, {"
            if ls and ls[0] in '}]':
                if stack:
                    start, _ = stack.pop()
                    if i > start:
                        regions[start] = {'end': i, 'folded': False}
            # И отдельно проверяем открытие
            if stripped[-1] in '{[':
                stack.append((i, stripped[-1]))
        return regions

    def toggle_fold(self, block_num):
        info = self.fold_regions.get(block_num)
        if not info:
            return
        info['folded'] = not info['folded']
        doc = self.document()
        for n in range(block_num + 1, info['end'] + 1):
            blk = doc.findBlockByNumber(n)
            if not blk.isValid():
                continue
            blk.setVisible(not info['folded'])
        # Принудительная перекомпановка - иначе скрытые блоки могут оставить пустоту
        doc.markContentsDirty(0, doc.characterCount())
        self.viewport().update()
        self._gutter.update()

    def _unfold_all(self):
        doc = self.document()
        for i in range(doc.blockCount()):
            blk = doc.findBlockByNumber(i)
            if blk.isValid():
                blk.setVisible(True)
        self.fold_regions = {}


class MarkerScrollBar(QScrollBar):
    """Вертикальный скроллбар, который рисует поверх полоски-метки в заданных
    относительных позициях. Используется для подсветки ERROR/WARN в логе."""

    def __init__(self, orientation=Qt.Orientation.Vertical, parent=None):
        super().__init__(orientation, parent)
        # Список (rel_pos в [0,1], QColor)
        self._markers = []

    def set_markers(self, markers):
        self._markers = list(markers)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._markers:
            return

        # Берём реальный rect "желоба" (без стрелок), чтобы метки не
        # попадали на кнопки прокрутки и совпадали с позицией ползунка.
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_ScrollBar, opt,
            QStyle.SubControl.SC_ScrollBarGroove, self,
        )
        if groove.height() <= 0 or groove.width() <= 0:
            return

        painter = QPainter(self)
        for rel_pos, color in self._markers:
            y = groove.top() + int(rel_pos * (groove.height() - 2))
            painter.fillRect(groove.left(), y, groove.width(), 2, color)
        painter.end()
