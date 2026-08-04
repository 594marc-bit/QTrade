"""SQLite-backed dashboard log — safe to call from any process."""

from src.data.storage import get_connection


def log(level: str, message: str, source: str = "") -> None:
    """Write a log entry to the dashboard_logs table.

    Thread/process safe via SQLite WAL mode. Callable from CLI,
    server, wizard, or any module.
    """
    try:
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                level TEXT NOT NULL DEFAULT 'info',
                source TEXT DEFAULT '',
                message TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO dashboard_logs (level, source, message) VALUES (?, ?, ?)",
            (level, source, message),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Best-effort; never crash on log failure


def info(message: str, source: str = "") -> None:
    log("info", message, source)


def warn(message: str, source: str = "") -> None:
    log("warn", message, source)


def error(message: str, source: str = "") -> None:
    log("error", message, source)
