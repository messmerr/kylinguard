"""自定义策略库：管理员在 UI 维护的黑名单/只读白名单/保护路径（SQLite）。

与内置规则（rules.py 硬编码，代码级基线，UI 只读展示）合并参与判定：
自定义黑名单/保护路径只会收紧；自定义白名单是管理员显式放行决策，
为命令名级（不做 flag 排除），添加时应知晓其含义。
"""
import re
import sqlite3
import threading
import time

from kylinguard.rules import ExtraPolicies

KINDS = ("blacklist", "readonly", "protected")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    pattern TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
"""


class PolicyStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()
        self._cache: ExtraPolicies | None = None

    def add(self, kind: str, pattern: str, note: str = "") -> int:
        if kind not in KINDS:
            raise ValueError(f"kind 须为 {KINDS} 之一")
        pattern = pattern.strip()
        if not pattern:
            raise ValueError("pattern 不能为空")
        if kind == "blacklist":
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"正则不合法：{e}") from e
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO policies(kind, pattern, note, created_at) "
                "VALUES (?,?,?,?)", (kind, pattern, note, time.time()))
            self._conn.commit()
            self._cache = None
            return cur.lastrowid

    def remove(self, policy_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM policies WHERE id=?", (policy_id,))
            self._conn.commit()
            self._cache = None
            return cur.rowcount > 0

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, kind, pattern, note, created_at FROM policies "
            "ORDER BY id").fetchall()
        return [{"id": r[0], "kind": r[1], "pattern": r[2],
                 "note": r[3], "created_at": r[4]} for r in rows]

    def extra(self) -> ExtraPolicies:
        """聚合为规则引擎可直接消费的结构（内存缓存，写操作失效）。"""
        if self._cache is None:
            blacklist, readonly, protected = [], set(), []
            for item in self.list():
                if item["kind"] == "blacklist":
                    blacklist.append(
                        (item["pattern"], item["note"] or "自定义黑名单"))
                elif item["kind"] == "readonly":
                    readonly.add(item["pattern"])
                elif item["kind"] == "protected":
                    protected.append(item["pattern"])
            self._cache = ExtraPolicies(
                blacklist=blacklist, readonly=frozenset(readonly),
                protected=tuple(protected))
        return self._cache

    def close(self) -> None:
        self._conn.close()
