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
