"""Компактный исходный журнал: блоки UTF-8 и смещения строк вместо миллионов str."""
from array import array
from bisect import bisect_right
from collections.abc import Sequence
import re


class RawLogBuffer(Sequence):
    """Неизменяемый после чтения список строк с декодированием по обращению."""

    BLOCK_SIZE = 1024 * 1024
    _NEWLINE = re.compile(b'\n')
    _LEVELS = (re.compile(b'ERROR'), re.compile(b'WARN'))

    def __init__(self):
        self._blocks = []
        self._starts = []
        self._ends = array('Q')
        self.byte_count = 0

    @classmethod
    def read(cls, stream, cancelled, progress, preview=None):
        """Читает двоичный поток, сохраняя universal-newline и UTF-8 replace семантику."""
        result = cls()
        pending_cr = b''
        consumed = 0
        while True:
            if cancelled():
                raise InterruptedError
            block = stream.read(cls.BLOCK_SIZE)
            consumed += len(block)
            if not block:
                if pending_cr:
                    result._append(b'\n')
                break
            block = pending_cr + block
            pending_cr = b'\r' if block.endswith(b'\r') else b''
            if pending_cr:
                block = block[:-1]
            result._append(block.replace(b'\r\n', b'\n').replace(b'\r', b'\n'))
            if preview is not None and len(result) >= 256:
                # Передаём отдельный маленький снимок один раз, без очереди порций в GUI.
                preview(tuple(result[index] for index in range(256)))
                preview = None
            progress(consumed)
        if result.byte_count and (not result._ends or result._ends[-1] != result.byte_count):
            result._ends.append(result.byte_count)
        return result

    def _append(self, block):
        if not block:
            return
        start = self.byte_count
        self._blocks.append(block)
        self._starts.append(start)
        self._ends.extend(start + match.end() for match in self._NEWLINE.finditer(block))
        self.byte_count += len(block)

    def __len__(self):
        return len(self._ends)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[row] for row in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        start = self._ends[index - 1] if index else 0
        return self._bytes(start, self._ends[index]).decode('utf-8', errors='replace')

    def _bytes(self, start, end):
        first = bisect_right(self._starts, start) - 1
        last = bisect_right(self._starts, end - 1) - 1
        if first == last:
            return self._blocks[first][start - self._starts[first]:end - self._starts[first]]
        parts = [self._blocks[first][start - self._starts[first]:]]
        parts.extend(self._blocks[first + 1:last])
        parts.append(self._blocks[last][:end - self._starts[last]])
        return b''.join(parts)

    def markers(self, cancelled):
        """Ищет уровни в байтах; декодирует только заголовки строк с ERROR/WARN."""
        levels = {}
        tail = b''
        for start, block in zip(self._starts, self._blocks):
            if cancelled():
                raise InterruptedError
            search = tail + block
            base = start - len(tail)
            matches = (match for pattern in self._LEVELS for match in pattern.finditer(search))
            for count, match in enumerate(matches):
                if count % 2048 == 0 and cancelled():
                    raise InterruptedError
                if base + match.end() <= start:
                    continue
                position = base + match.start()
                row = bisect_right(self._ends, position)
                line_start = self._ends[row - 1] if row else 0
                # 30 символов UTF-8 занимают не более 120 байт.
                if position - line_start >= 120 or row in levels:
                    continue
                head = self._bytes(line_start, min(line_start + 120, self._ends[row]))
                head = head.decode('utf-8', errors='replace')[:30]
                if '[' in head:
                    if 'ERROR' in head:
                        levels[row] = 'ERROR'
                    elif 'WARN' in head:
                        levels[row] = 'WARN'
            tail = search[-4:]
        return [(row + 1, levels[row]) for row in sorted(levels)]
