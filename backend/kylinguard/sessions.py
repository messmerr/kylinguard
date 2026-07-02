"""会话元数据存储：侧栏历史列表的数据层（SQLite，与审计链同库文件）。

对话消息本体不在这里：活跃会话的完整 conversation 由 Pipeline 内存维护，
服务重启后从审计链摘要重建；历史回放直接读审计事件。
"""
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

_TITLE_MAX = 30


class SessionStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def create(self, session_id: str, title: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id, title, created_at, updated_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
                (session_id, title.strip()[:_TITLE_MAX] or "新会话", now, now),
            )
            self._conn.commit()

    def touch(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (time.time(), session_id),
            )
            self._conn.commit()

    def exists(self, session_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone()
        return row is not None

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions "
            "ORDER BY updated_at DESC"
        ).fetchall()
        return [{"id": r[0], "title": r[1],
                 "created_at": r[2], "updated_at": r[3]} for r in rows]

    def close(self) -> None:
        self._conn.close()
