import re

from PyQt6.QtWidgets import (QListView, QTextEdit, QScrollBar, QStyle,
                             QStyleOptionSlider, QPlainTextEdit, QWidget)
from PyQt6.QtCore import pyqtSignal, Qt, QRect, QSize, QTimer
from PyQt6.QtGui import (QWheelEvent, QPainter, QColor, QSyntaxHighlighter,
                         QTextCharFormat, QFont, QMouseEvent)


# --- Busy overlay ---
class BusyOverlay(QWidget):
    """Полупрозрачный затеняющий слой поверх главного окна с текстом и
    анимированным spinner-кружком в центре. Показывается при долгих
    операциях (парсинг файла, построение индекса истории кода, наполнение
    больших деревьев), чтобы юзер видел что приложение работает - а не
    зависло намертво.

    Использование:
      overlay = BusyOverlay(main_window)
      overlay.show_with("Парсинг application.log ...")
      ...  # длинная операция
      overlay.hide()

    Геометрия автоматически растягивается на родительский виджет (см.
    parent.resizeEvent → overlay.resize)."""

    def __init__(self, parent):
        super().__init__(parent)
        self._text = ""
        self._angle = 0
        # Перехватываем клики на оверлее - чтобы юзер случайно ничего не нажал
        # под полупрозрачным слоем во время блокирующей операции.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        # Spinner крутится по таймеру; запускается в show_with(), останавливается
        # в hide() - чтобы не тратить CPU когда оверлей скрыт.
        self._timer = QTimer(self)
        self._timer.setInterval(40)  # ~25 fps - плавно и недорого
        self._timer.timeout.connect(self._tick)
        self.hide()

    def show_with(self, text):
        """Показывает оверлей с указанным текстом. Растягивает по родителю."""
        self._text = text or ""
        if self.parent() is not None:
            self.resize(self.parent().size())
        self.raise_()
        self.show()
        self._angle = 0
        self._timer.start()
        # Принудительно обновляем frame: операция, которая последует, может
        # надолго заблокировать UI-поток - покажем хотя бы первый кадр.
        self.repaint()

    def set_text(self, text):
        """Обновить только текст (например прогресс «X / Y» в долгом цикле)."""
        if text == self._text:
            return
        self._text = text or ""
        self.update()

    def hide(self):
        self._timer.stop()
        super().hide()

    def _tick(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        # Полупрозрачная подложка - почти весь интерфейс позади виден,
        # но кликнуть нельзя.
        p.fillRect(self.rect(), QColor(0, 0, 0, 130))

        # Центр - белый круг spinner'а
        cx = self.width() // 2
        cy = self.height() // 2 - 16
        radius = 22
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen_bg = p.pen()
        pen_bg.setColor(QColor(255, 255, 255, 60))
        pen_bg.setWidth(4)
        p.setPen(pen_bg)
        p.drawEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

        # Активная дуга вращается
        pen_fg = p.pen()
        pen_fg.setColor(QColor("#2A82DA"))
        pen_fg.setWidth(4)
        p.setPen(pen_fg)
        # drawArc принимает start_angle и span в 1/16-х долях градуса
        p.drawArc(cx - radius, cy - radius, radius * 2, radius * 2,
                  -self._angle * 16, -90 * 16)

        # Текст подсказки под spinner'ом
        if self._text:
            p.setPen(QColor(255, 255, 255))
            font = p.font()
            font.setPointSize(11)
            p.setFont(font)
            rect = QRect(0, cy + radius + 10, self.width(), 40)
            p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self._text)
        p.end()


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


# --- JSON syntax highlighter ---
# Дефолтная палитра нужна, если highlighter создан до того, как тема применилась
# (например в момент конструирования FoldableJsonTextEdit).
_DEFAULT_HIGHLIGHTER_PALETTE = {
    "json_key": "#9CDCFE",
    "json_string": "#CE9178",
    "json_number": "#B5CEA8",
    "json_keyword": "#569CD6",
    "json_bracket": "#FFD700",
}


class JsonSyntaxHighlighter(QSyntaxHighlighter):
    """Подсветка JSON-токенов: ключи, строки, числа, true/false/null, скобки.
    Работает на любой строке - если в строке нет JSON, ничего не подсветит.
    Цвета настраиваются через apply_palette(palette), чтобы согласовать с темой."""

    # Регексы общие, не зависят от палитры - компилируем один раз на класс.
    _RULE_REGEXES = [
        ('json_key', re.compile(r'"(?:[^"\\]|\\.)*"\s*(?=:)'), True),
        ('json_string', re.compile(r'"(?:[^"\\]|\\.)*"'), False),
        ('json_number', re.compile(r'-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b'), False),
        ('json_keyword', re.compile(r'\b(?:true|false|null)\b'), True),
        ('json_bracket', re.compile(r'[{}\[\]]'), False),
    ]

    def __init__(self, document):
        super().__init__(document)
        self._rules = []
        self.apply_palette(_DEFAULT_HIGHLIGHTER_PALETTE)

    def apply_palette(self, palette):
        """Перестраивает форматы под цвета из palette (см. THEMES[...]['json_palette'])."""
        rules = []
        for key, regex, bold in self._RULE_REGEXES:
            color = palette.get(key, _DEFAULT_HIGHLIGHTER_PALETTE.get(key, "#000000"))
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if bold:
                fmt.setFontWeight(QFont.Weight.Bold)
            rules.append((regex, fmt))
        self._rules = rules
        # Перерисовать всё, что уже подсвечено старыми цветами
        self.rehighlight()

    def highlightBlock(self, text):
        for regex, fmt in self._rules:
            for m in regex.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


# --- Folding gutter for FoldableJsonTextEdit ---
class _FoldingGutter(QWidget):
    """Узкая колонка слева от текстового редактора. Рисует ▼ для развёрнутых
    fold-блоков и ▶ для свёрнутых; клик переключает состояние. Цвета берутся
    из палитры темы (см. apply_palette)."""

    WIDTH = 16

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor
        self.setFixedWidth(self.WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._bg = QColor("#1E1E1E")
        self._glyph_open = QColor("#777777")
        self._glyph_collapsed = QColor("#D4D4D4")

    def apply_palette(self, palette):
        self._bg = QColor(palette.get("gutter_bg", "#1E1E1E"))
        self._glyph_open = QColor(palette.get("gutter_glyph", "#777777"))
        self._glyph_collapsed = QColor(palette.get("gutter_glyph_collapsed", "#D4D4D4"))
        self.update()

    def sizeHint(self):
        return QSize(self.WIDTH, 0)

    def paintEvent(self, event):
        editor = self._editor
        painter = QPainter(self)
        painter.fillRect(event.rect(), self._bg)

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
                    color = self._glyph_collapsed if info['folded'] else self._glyph_open
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

    def apply_theme_palette(self, palette):
        """Прокидывает палитру в gutter (фон + цвета стрелок) и в highlighter
        (цвета JSON-токенов). Вызывается из LogViewerWidget.apply_theme."""
        self._gutter.apply_palette(palette)
        self._highlighter.apply_palette(palette)

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
        """Сканирует строки text и находит парные { ... }, [ ... ] и ( ... )
        на разных строках. Простое правило (надёжно на выводе json.dumps(indent=...)
        и Java-style toString после kv-prettify):
        - строка-открыватель заканчивается на {, [ или (
        - строка-закрыватель начинается (после whitespace) с }, ] или )
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
            if ls and ls[0] in '}])':
                if stack:
                    start, _ = stack.pop()
                    if i > start:
                        regions[start] = {'end': i, 'folded': False}
            # И отдельно проверяем открытие
            if stripped[-1] in '{[(':
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
