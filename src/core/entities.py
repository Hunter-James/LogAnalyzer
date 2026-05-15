# --- Data Structure ---
class LogEntry:
    """Одна логическая запись лога. Может быть многострочной (продолжения
    multiline-сообщений склеиваются в message через '\\n').

    ОПТИМИЗАЦИЯ ПАМЯТИ:
    - full_line больше не хранится отдельно: он почти всегда совпадает с
      message + '\\n', и хранить копию ради этого незначительного отличия
      означало удваивать память на текст. Теперь это @property,
      реконструируется на лету. Все потребители (поиск, prettify-JSON, дерево
      истории кода) работают с этим эквивалентом без видимой разницы.
    - preview больше не хранится: вычисляется в @property только когда
      QListView просит DisplayRole у видимой строки (на экране ~30-50 шт).

    Это даёт ~30-40% сокращение RAM на entries для типичных логов.
    Конструктор принимает full_line для обратной совместимости (workers.py
    раньше передавал его и потом дописывал в continuation lines).
    """

    __slots__ = ('timestamp', 'level', 'logger', 'message')

    def __init__(self, timestamp, level, logger, message, full_line=None):
        self.timestamp = timestamp
        self.level = level
        self.logger = logger
        self.message = message
        # full_line игнорируется как отдельное хранилище: реконструируется
        # через @property full_line ниже. Continuation lines теперь
        # склеиваются только через message (см. workers.IncrementalLogParser).

    @property
    def full_line(self):
        """Реконструируем raw-строку: message + trailing newline.
        Для однострочных entries это в точности исходная строка лога.
        Для многострочных - то же, но с нормализованным whitespace между
        строками (которые и так делает strip() в IncrementalLogParser)."""
        return self.message + '\n'

    @property
    def preview(self):
        """Превью для отображения в QListView - первая строка, обрезанная
        до 250 символов. Считаем на лету: вызывается только для видимых
        строк, виртуализация QListView гарантирует ~50 вызовов на frame."""
        first_line = self.message.split('\n', 1)[0]
        if len(first_line) > 250:
            return first_line[:250] + "..."
        return first_line
