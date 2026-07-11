"""审计事件链：SQLite（WAL）+ SHA-256 哈希链，防篡改、可回放。

写入失败抛 AuditError —— 流水线必须立即中止（不允许"干了但没记录"）。
"""
import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_events(session_id, seq);
"""


class AuditError(RuntimeError):
    """审计落盘失败——致命错误，任务必须中止。"""


def _digest(prev_hash: str, session_id: str, seq: int, ts: str,
            event_type: str, payload_json: str) -> str:
    raw = f"{prev_hash}|{session_id}|{seq}|{ts}|{event_type}|{payload_json}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AuditLog:
    GENESIS = "0" * 64

    def __init__(self, db_path: str):
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.RLock()

    @contextmanager
    def serialized(self):
        """与权限状态事务统一锁序：先审计锁，再会话存储锁。"""
        with self._lock:
            yield

    def append(
        self,
        session_id: str,
        event_type: str,
        payload: dict,
        *,
        connection: sqlite3.Connection | None = None,
        commit: bool = True,
        lock_held: bool = False,
    ) -> str:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        def write() -> str:
            conn = connection or self._conn
            try:
                row = conn.execute(
                    "SELECT seq, hash FROM audit_events "
                    "WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
                seq = row[0] + 1 if row else 0
                prev_hash = row[1] if row else self.GENESIS
                ts = datetime.now(timezone.utc).isoformat()
                h = _digest(prev_hash, session_id, seq, ts, event_type, payload_json)
                conn.execute(
                    "INSERT INTO audit_events"
                    "(session_id, seq, ts, event_type, payload, prev_hash, hash) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (session_id, seq, ts, event_type, payload_json, prev_hash, h),
                )
                if commit:
                    conn.commit()
            except sqlite3.Error as e:
                raise AuditError(f"审计写入失败：{e}") from e
            return h
        if lock_held:
            return write()
        with self._lock:
            return write()

    def events(self, session_id: str) -> list[dict]:
        try:
            rows = self._conn.execute(
                "SELECT seq, ts, event_type, payload, prev_hash, hash "
                "FROM audit_events WHERE session_id=? ORDER BY seq",
                (session_id,),
            ).fetchall()
        except sqlite3.Error as e:
            raise AuditError(f"审计读取失败：{e}") from e
        return [
            {"seq": r[0], "ts": r[1], "event_type": r[2],
             "payload": json.loads(r[3]), "prev_hash": r[4], "hash": r[5]}
            for r in rows
        ]

    def verify_chain(self, session_id: str) -> bool:
        rows = self._conn.execute(
            "SELECT seq, ts, event_type, payload, prev_hash, hash "
            "FROM audit_events WHERE session_id=? ORDER BY seq",
            (session_id,),
        ).fetchall()
        prev = self.GENESIS
        for seq, ts, event_type, payload_json, prev_hash, h in rows:
            if prev_hash != prev:
                return False
            if _digest(prev_hash, session_id, seq, ts, event_type, payload_json) != h:
                return False
            prev = h
        return True

    def stats(self) -> dict:
        """全局安全统计（仪表盘用）：事件量、拦截数、确认批准/拒绝数。"""
        try:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM audit_events").fetchone()[0]
            by_type = dict(self._conn.execute(
                "SELECT event_type, COUNT(*) FROM audit_events "
                "GROUP BY event_type").fetchall())
            denied = self._conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE "
                "event_type='verification' AND "
                "json_extract(payload, '$.decision.action')='deny'"
            ).fetchone()[0]
            approved = self._conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE "
                "event_type IN ('confirm_result','permission_result') AND "
                "json_extract(payload, '$.approved')"
            ).fetchone()[0]
            decisions = (by_type.get("confirm_result", 0)
                         + by_type.get("permission_result", 0))
            rejected = decisions - approved
        except sqlite3.Error as e:
            raise AuditError(f"审计读取失败：{e}") from e
        return {"total_events": total, "by_type": by_type, "denied": denied,
                "confirm_approved": approved, "confirm_rejected": rejected}

    def close(self):
        self._conn.close()
