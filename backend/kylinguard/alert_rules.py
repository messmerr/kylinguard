"""告警规则、推送渠道、告警历史的 SQLite 持久化存储。

数据模型：
- AlertRule  ：用户配置的评估规则（指标 + 阈值 + 沉默期 + 关联渠道）
- AlertChannel：推送目标（webhook / email）
- AlertHistory：每次触发的历史记录

AlertRuleStore 实例通过 api.py 注入到 SnapshotCache，不持有 HTTP/IO 操作。
"""
import json
import sqlite3
import threading
import time
from dataclasses import dataclass

# ---------- 数据类 ----------

@dataclass
class AlertRule:
    id: int
    name: str
    metric: str          # memory_pct | cpu_pct | disk_pct | failed_services
    operator: str        # >= | >
    threshold: float     # 数值型阈值（failed_services 忽略此字段）
    severity: str        # warning | critical
    silence_minutes: int # 同条规则重复触发的冷却分钟数
    channel_ids: list[int]
    enabled: bool
    created_at: float


@dataclass
class AlertChannel:
    id: int
    name: str
    type: str            # webhook | email
    config: dict         # webhook: {url, method?, headers?}  email: {host,port,user,password,to}
    enabled: bool
    created_at: float


@dataclass
class AlertHistoryEntry:
    id: int
    rule_id: int | None
    rule_name: str
    metric: str
    metric_value: str
    severity: str
    message: str
    channels_notified: list[str]
    fired_at: float
    acknowledged_at: float | None


# ---------- 存储 ----------

_DDL = """
CREATE TABLE IF NOT EXISTS alert_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    metric          TEXT    NOT NULL,
    operator        TEXT    NOT NULL DEFAULT '>=',
    threshold       REAL    NOT NULL DEFAULT 0,
    severity        TEXT    NOT NULL DEFAULT 'warning',
    silence_minutes INTEGER NOT NULL DEFAULT 10,
    channel_ids     TEXT    NOT NULL DEFAULT '[]',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_channels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    type       TEXT    NOT NULL,
    config     TEXT    NOT NULL DEFAULT '{}',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id            INTEGER,
    rule_name          TEXT    NOT NULL,
    metric             TEXT    NOT NULL,
    metric_value       TEXT    NOT NULL,
    severity           TEXT    NOT NULL,
    message            TEXT    NOT NULL,
    channels_notified  TEXT    NOT NULL DEFAULT '[]',
    fired_at           REAL    NOT NULL,
    acknowledged_at    REAL
);
CREATE TABLE IF NOT EXISTS alert_last_fired (
    rule_id   INTEGER PRIMARY KEY,
    fired_at  REAL    NOT NULL
);
"""


class AlertRuleStore:
    def __init__(self, db_path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        for stmt in _DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                self._conn.execute(s)
        history_columns = {
            row[1] for row in self._conn.execute(
                "PRAGMA table_info(alert_history)").fetchall()
        }
        if "acknowledged_at" not in history_columns:
            self._conn.execute(
                "ALTER TABLE alert_history ADD COLUMN acknowledged_at REAL")
            # Existing history predates the pending queue and must not become unread.
            self._conn.execute(
                "UPDATE alert_history SET acknowledged_at=fired_at")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_history_pending "
            "ON alert_history(acknowledged_at, fired_at DESC)")
        self._prune_missing_channel_ids_locked()
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- 规则 ----

    def list_rules(self) -> list[AlertRule]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alert_rules ORDER BY id").fetchall()
        return [self._row_to_rule(r) for r in rows]

    def get_rule(self, rule_id: int) -> AlertRule | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
        return self._row_to_rule(r) if r else None

    def add_rule(self, name: str, metric: str, operator: str,
                 threshold: float, severity: str, silence_minutes: int,
                 channel_ids: list[int], enabled: bool = True) -> int:
        with self._lock:
            channel_ids = self._valid_channel_ids_locked(channel_ids)
            cur = self._conn.execute(
                "INSERT INTO alert_rules(name,metric,operator,threshold,severity,"
                "silence_minutes,channel_ids,enabled,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (name, metric, operator, threshold, severity, silence_minutes,
                 json.dumps(channel_ids), int(enabled), time.time()))
            self._conn.commit()
            return cur.lastrowid

    def update_rule(self, rule_id: int, **kwargs) -> bool:
        allowed = {"name", "metric", "operator", "threshold", "severity",
                   "silence_minutes", "channel_ids", "enabled"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return False
        if "enabled" in fields:
            fields["enabled"] = int(fields["enabled"])
        with self._lock:
            if "channel_ids" in fields:
                fields["channel_ids"] = json.dumps(
                    self._valid_channel_ids_locked(fields["channel_ids"]))
            sets = ", ".join(f"{k}=?" for k in fields)
            cur = self._conn.execute(
                f"UPDATE alert_rules SET {sets} WHERE id=?",
                [*fields.values(), rule_id])
            self._conn.commit()
        return cur.rowcount > 0

    def delete_rule(self, rule_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM alert_rules WHERE id=?", (rule_id,))
            self._conn.execute(
                "DELETE FROM alert_last_fired WHERE rule_id=?", (rule_id,))
            self._conn.commit()
        return cur.rowcount > 0

    # ---- 渠道 ----

    def list_channels(self) -> list[AlertChannel]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alert_channels ORDER BY id").fetchall()
        return [self._row_to_channel(r) for r in rows]

    def get_channel(self, ch_id: int) -> AlertChannel | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM alert_channels WHERE id=?", (ch_id,)).fetchone()
        return self._row_to_channel(r) if r else None

    def add_channel(self, name: str, type_: str, config: dict,
                    enabled: bool = True) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO alert_channels(name,type,config,enabled,created_at)"
                " VALUES(?,?,?,?,?)",
                (name, type_, json.dumps(config), int(enabled), time.time()))
            self._conn.commit()
            return cur.lastrowid

    def update_channel(self, ch_id: int, **kwargs) -> bool:
        allowed = {"name", "type", "config", "enabled"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return False
        if "config" in fields:
            fields["config"] = json.dumps(fields["config"])
        if "enabled" in fields:
            fields["enabled"] = int(fields["enabled"])
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE alert_channels SET {sets} WHERE id=?",
                [*fields.values(), ch_id])
            self._conn.commit()
        return cur.rowcount > 0

    def delete_channel(self, ch_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM alert_channels WHERE id=?", (ch_id,))
            if cur.rowcount > 0:
                self._prune_missing_channel_ids_locked()
            self._conn.commit()
        return cur.rowcount > 0

    # ---- 历史 ----

    def add_history(self, rule_id: int | None, rule_name: str, metric: str,
                    metric_value: str, severity: str, message: str,
                    channels_notified: list[str]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO alert_history(rule_id,rule_name,metric,metric_value,"
                "severity,message,channels_notified,fired_at) VALUES(?,?,?,?,?,?,?,?)",
                (rule_id, rule_name, metric, metric_value, severity, message,
                 json.dumps(channels_notified), time.time()))
            self._conn.commit()
            return cur.lastrowid

    def record_trigger(self, rule_id: int, rule_name: str, metric: str,
                       metric_value: str, severity: str, message: str) -> int:
        """Persist a new unread rule alert and its cooldown timestamp atomically."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO alert_history(rule_id,rule_name,metric,metric_value,"
                "severity,message,channels_notified,fired_at,acknowledged_at) "
                "VALUES(?,?,?,?,?,?,?,?,NULL)",
                (rule_id, rule_name, metric, metric_value, severity, message,
                 "[]", now))
            self._conn.execute(
                "INSERT INTO alert_last_fired(rule_id,fired_at) VALUES(?,?)"
                " ON CONFLICT(rule_id) DO UPDATE SET fired_at=excluded.fired_at",
                (rule_id, now))
            self._conn.commit()
            return cur.lastrowid

    def update_history_channels(self, history_id: int,
                                channels_notified: list[str]) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE alert_history SET channels_notified=? WHERE id=?",
                (json.dumps(channels_notified), history_id))
            self._conn.commit()
        return cur.rowcount > 0

    def list_history(self, limit: int = 200) -> list[AlertHistoryEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alert_history ORDER BY fired_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [self._row_to_history(r) for r in rows]

    def list_pending(self) -> list[AlertHistoryEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alert_history WHERE acknowledged_at IS NULL "
                "ORDER BY fired_at DESC").fetchall()
        return [self._row_to_history(r) for r in rows]

    def acknowledge_history(self, history_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE alert_history "
                "SET acknowledged_at=COALESCE(acknowledged_at, ?) WHERE id=?",
                (time.time(), history_id))
            self._conn.commit()
        return cur.rowcount > 0

    def acknowledge_all_pending(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM alert_history WHERE acknowledged_at IS NULL"
            ).fetchall()
            history_ids = [row["id"] for row in rows]
            if history_ids:
                self._conn.execute(
                    "UPDATE alert_history SET acknowledged_at=? "
                    "WHERE acknowledged_at IS NULL",
                    (time.time(),))
                self._conn.commit()
        return history_ids

    def clear_history(self) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM alert_history WHERE acknowledged_at IS NOT NULL")
            self._conn.commit()

    # ---- 冷却时间 ----

    def get_last_fired(self, rule_id: int) -> float:
        with self._lock:
            r = self._conn.execute(
                "SELECT fired_at FROM alert_last_fired WHERE rule_id=?",
                (rule_id,)).fetchone()
        return r["fired_at"] if r else 0.0

    def update_last_fired(self, rule_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO alert_last_fired(rule_id,fired_at) VALUES(?,?)"
                " ON CONFLICT(rule_id) DO UPDATE SET fired_at=excluded.fired_at",
                (rule_id, time.time()))
            self._conn.commit()

    def _valid_channel_ids_locked(self, channel_ids: list[int]) -> list[int]:
        requested = list(dict.fromkeys(int(item) for item in channel_ids))
        if not requested:
            return []
        placeholders = ",".join("?" for _ in requested)
        existing = {
            row[0] for row in self._conn.execute(
                f"SELECT id FROM alert_channels WHERE id IN ({placeholders})",
                requested).fetchall()
        }
        return [channel_id for channel_id in requested if channel_id in existing]

    def _prune_missing_channel_ids_locked(self) -> None:
        valid_ids = {
            row[0] for row in self._conn.execute(
                "SELECT id FROM alert_channels").fetchall()
        }
        rows = self._conn.execute(
            "SELECT id, channel_ids FROM alert_rules").fetchall()
        for row in rows:
            invalid_json = False
            try:
                channel_ids = json.loads(row["channel_ids"])
            except (TypeError, ValueError):
                channel_ids = []
                invalid_json = True
            filtered = [
                channel_id for channel_id in channel_ids
                if channel_id in valid_ids
            ]
            if invalid_json or filtered != channel_ids:
                self._conn.execute(
                    "UPDATE alert_rules SET channel_ids=? WHERE id=?",
                    (json.dumps(filtered), row["id"]))

    # ---- 内部转换 ----

    @staticmethod
    def _row_to_rule(r: sqlite3.Row) -> AlertRule:
        return AlertRule(
            id=r["id"], name=r["name"], metric=r["metric"],
            operator=r["operator"], threshold=r["threshold"],
            severity=r["severity"], silence_minutes=r["silence_minutes"],
            channel_ids=json.loads(r["channel_ids"]),
            enabled=bool(r["enabled"]), created_at=r["created_at"],
        )

    @staticmethod
    def _row_to_channel(r: sqlite3.Row) -> AlertChannel:
        return AlertChannel(
            id=r["id"], name=r["name"], type=r["type"],
            config=json.loads(r["config"]),
            enabled=bool(r["enabled"]), created_at=r["created_at"],
        )

    @staticmethod
    def _row_to_history(r: sqlite3.Row) -> AlertHistoryEntry:
        return AlertHistoryEntry(
            id=r["id"], rule_id=r["rule_id"], rule_name=r["rule_name"],
            metric=r["metric"], metric_value=r["metric_value"],
            severity=r["severity"], message=r["message"],
            channels_notified=json.loads(r["channels_notified"]),
            fired_at=r["fired_at"],
            acknowledged_at=r["acknowledged_at"],
        )
