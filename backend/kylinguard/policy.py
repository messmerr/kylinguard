"""自定义风险策略库（SQLite）。

命令正则和保护路径会提升确认强度，可信命令会参与只读候选复核。任意 shell
无法靠字符串策略构成不可绕过沙箱；完全访问按定义可以覆盖这些风险策略，
真正的控制面隔离应由独立 OS 账户与文件权限完成。
"""
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

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
        self._lock = threading.RLock()
        self._cache: ExtraPolicies | None = None
        self._revision = 0

    @contextmanager
    def transaction(self):
        """供策略变更与哈希审计使用同一 SQLite 事务。"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                self._cache = None
                raise

    def add(
        self, kind: str, pattern: str, note: str = "", *, commit: bool = True,
    ) -> int:
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
        elif kind == "protected":
            path = Path(pattern).expanduser()
            if not path.is_absolute():
                raise ValueError("保护路径必须是绝对路径")
            pattern = str(path.resolve(strict=False))
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO policies(kind, pattern, note, created_at) "
                "VALUES (?,?,?,?)", (kind, pattern, note, time.time()))
            if commit:
                self._conn.commit()
            self._cache = None
            self._revision += 1
            return cur.lastrowid

    def remove(self, policy_id: int, *, commit: bool = True) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM policies WHERE id=?", (policy_id,))
            if commit:
                self._conn.commit()
            removed = cur.rowcount > 0
            if removed:
                self._cache = None
                self._revision += 1
            return removed

    def _list_unlocked(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, kind, pattern, note, created_at FROM policies "
            "ORDER BY id").fetchall()
        return [{"id": r[0], "kind": r[1], "pattern": r[2],
                 "note": r[3], "created_at": r[4]} for r in rows]

    def list(self) -> list[dict]:
        with self._lock:
            return self._list_unlocked()

    def get(self, policy_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, kind, pattern, note, created_at FROM policies "
                "WHERE id=?", (policy_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "id": row[0], "kind": row[1], "pattern": row[2],
                "note": row[3], "created_at": row[4],
            }

    def snapshot(self) -> tuple[int, ExtraPolicies]:
        """原子返回策略修订号与聚合视图，供执行前检测配置变化。"""
        with self._lock:
            if self._cache is None:
                blacklist, readonly, protected = [], set(), []
                for item in self._list_unlocked():
                    if item["kind"] == "blacklist":
                        blacklist.append((
                            item["pattern"], item["note"] or "自定义黑名单",
                        ))
                    elif item["kind"] == "readonly":
                        readonly.add(item["pattern"])
                    elif item["kind"] == "protected":
                        protected.append(item["pattern"])
                self._cache = ExtraPolicies(
                    blacklist=blacklist,
                    readonly=frozenset(readonly),
                    protected=tuple(protected),
                )
            return self._revision, self._cache

    def revision(self) -> int:
        with self._lock:
            return self._revision

    def extra(self) -> ExtraPolicies:
        """聚合为规则引擎可直接消费的结构（内存缓存，写操作失效）。"""
        return self.snapshot()[1]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
