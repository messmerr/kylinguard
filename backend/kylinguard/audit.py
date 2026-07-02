"""审计事件链：SQLite（WAL）+ SHA-256 哈希链，防篡改、可回放。

写入失败抛 AuditError —— 流水线必须立即中止（不允许"干了但没记录"）。
"""
import hashlib
import json
import os
import sqlite3
import threading
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
        self._lock = threading.Lock()

    def append(self, session_id: str, event_type: str, payload: dict) -> str:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT seq, hash FROM audit_events "
                    "WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
                seq = row[0] + 1 if row else 0
                prev_hash = row[1] if row else self.GENESIS
                ts = datetime.now(timezone.utc).isoformat()
                h = _digest(prev_hash, session_id, seq, ts, event_type, payload_json)
                self._conn.execute(
                    "INSERT INTO audit_events"
                    "(session_id, seq, ts, event_type, payload, prev_hash, hash) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (session_id, seq, ts, event_type, payload_json, prev_hash, h),
                )
                self._conn.commit()
            except sqlite3.Error as e:
                raise AuditError(f"审计写入失败：{e}") from e
            return h

    def events(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT seq, ts, event_type, payload, prev_hash, hash "
            "FROM audit_events WHERE session_id=? ORDER BY seq",
            (session_id,),
        ).fetchall()
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

    def close(self):
        self._conn.close()
