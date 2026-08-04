"""Thread-safe ring buffer for structured dashboard logs."""

import threading
from collections import deque
from datetime import datetime


class LogBuffer:
    """In-memory ring buffer retaining the most recent N log entries."""

    def __init__(self, maxlen: int = 200):
        self._buffer: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, level: str, message: str, source: str = "") -> None:
        """Append a log entry.

        Args:
            level: 'info', 'warn', or 'error'.
            message: The log message.
            source: Optional source tag (e.g. 'sync', 'api', 'grid').
        """
        with self._lock:
            self._buffer.append({
                "ts": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "source": source,
                "message": message,
            })

    def get_recent(self, n: int = 50) -> list[dict]:
        """Return the most recent N entries (newest last)."""
        with self._lock:
            items = list(self._buffer)
        return items[-n:]

    def info(self, message: str, source: str = "") -> None:
        self.add("info", message, source)

    def warn(self, message: str, source: str = "") -> None:
        self.add("warn", message, source)

    def error(self, message: str, source: str = "") -> None:
        self.add("error", message, source)


# Global singleton for the app
log_buffer = LogBuffer(maxlen=200)
