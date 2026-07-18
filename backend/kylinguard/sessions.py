"""会话元数据存储：侧栏历史列表的数据层（SQLite，与审计链同库文件）。

对话消息本体不在这里：活跃会话的完整 conversation 由 Pipeline 内存维护，
服务重启后从审计链摘要重建；历史回放直接读审计事件。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from kylinguard.models import (
    PermissionGrant,
    PermissionGrantScope,
    PermissionMode,
    PermissionContext,
)
from kylinguard.permissions import (
    PermissionError,
    PermissionVersionConflict,
    normalize_auto_review_roots,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    draft INTEGER NOT NULL DEFAULT 0,
    workspace_root TEXT NOT NULL DEFAULT '',
    full_access_enabled INTEGER NOT NULL DEFAULT 0,
    permission_version INTEGER NOT NULL DEFAULT 1,
    permission_updated_at REAL NOT NULL DEFAULT 0,
    permission_updated_by TEXT NOT NULL DEFAULT '',
    full_access_execution_profile TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS permission_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    mode TEXT NOT NULL DEFAULT 'ask',
    auto_review_roots TEXT NOT NULL DEFAULT '[]',
    full_access_visible INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    execution_profile TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS permission_grants (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    action_fingerprint TEXT NOT NULL,
    capability TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    context_version INTEGER NOT NULL,
    granted_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    expiry_observed_at REAL,
    consumed_at REAL,
    revoked_at REAL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_permission_grants_session
    ON permission_grants(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_permission_grants_action
    ON permission_grants(session_id, action_fingerprint, capability);
"""

_TITLE_MAX = 30


def _migrate_permission_schema(connection: sqlite3.Connection) -> None:
    """补齐开发中间版本缺失的列；CREATE TABLE IF NOT EXISTS 不会补列。"""
    expected = {
        "sessions": {
            "draft": "INTEGER NOT NULL DEFAULT 0",
            "workspace_root": "TEXT NOT NULL DEFAULT ''",
            "full_access_enabled": "INTEGER NOT NULL DEFAULT 0",
            "permission_version": "INTEGER NOT NULL DEFAULT 1",
            "permission_updated_at": "REAL NOT NULL DEFAULT 0",
            "permission_updated_by": "TEXT NOT NULL DEFAULT ''",
            "full_access_execution_profile": "TEXT NOT NULL DEFAULT ''",
        },
        "permission_settings": {
            "auto_review_roots": "TEXT NOT NULL DEFAULT '[]'",
            "full_access_visible": "INTEGER NOT NULL DEFAULT 0",
            "version": "INTEGER NOT NULL DEFAULT 1",
            "updated_at": "REAL NOT NULL DEFAULT 0",
            "updated_by": "TEXT NOT NULL DEFAULT ''",
            "execution_profile": "TEXT NOT NULL DEFAULT ''",
        },
        "permission_grants": {
            "scope": "TEXT NOT NULL DEFAULT 'once'",
            "action_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "capability": "TEXT NOT NULL DEFAULT ''",
            "resource": "TEXT NOT NULL DEFAULT ''",
            "context_version": "INTEGER NOT NULL DEFAULT 1",
            "granted_by": "TEXT NOT NULL DEFAULT ''",
            "created_at": "REAL NOT NULL DEFAULT 0",
            "expires_at": "REAL",
            "expiry_observed_at": "REAL",
            "consumed_at": "REAL",
            "revoked_at": "REAL",
        },
    }
    for table, columns in expected.items():
        existing = {
            row[1] for row in connection.execute(
                f"PRAGMA table_info({table})").fetchall()
        }
        if not existing:
            continue
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    connection.commit()


class SessionStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        _migrate_permission_schema(self._conn)
        self._conn.execute(
            "INSERT OR IGNORE INTO permission_settings"
            "(singleton, mode, auto_review_roots, full_access_visible, "
            "version, updated_at, updated_by, execution_profile) "
            "VALUES (1,'ask','[]',0,1,?,'','')",
            (time.time(),),
        )
        self._conn.commit()
        self._conn.executescript(_INDEXES)
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self):
        """暴露受锁保护的显式事务，供权限状态与审计事件原子提交。"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def create(
        self,
        session_id: str,
        title: str,
        *,
        draft: bool = False,
        workspace_root: str = "",
        strict: bool = False,
        commit: bool = True,
    ) -> None:
        now = time.time()
        with self._lock:
            insert_session = (
                "INSERT INTO sessions(id, title, created_at, updated_at, draft, "
                "workspace_root) VALUES (?,?,?,?,?,?)"
            )
            if not strict:
                insert_session += (
                    " ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at"
                )
            try:
                self._conn.execute(
                    insert_session,
                    (
                        session_id,
                        title.strip()[:_TITLE_MAX] or "新会话",
                        now,
                        now,
                        int(draft),
                        workspace_root,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if strict:
                    raise PermissionError(
                        "session_already_exists", "会话标识已存在。"
                    ) from exc
                raise
            if commit:
                self._conn.commit()

    def touch(self, session_id: str, *, first_message: str | None = None) -> None:
        """更新会话时间；首条消息会可靠地把预创建草稿转为正式会话。"""
        with self._lock:
            now = time.time()
            if first_message is None:
                self._conn.execute(
                    "UPDATE sessions SET updated_at=? WHERE id=?",
                    (now, session_id),
                )
            else:
                title = first_message.strip()[:_TITLE_MAX] or "新会话"
                self._conn.execute(
                    "UPDATE sessions SET updated_at=?, "
                    "title=CASE WHEN draft=1 THEN ? ELSE title END, "
                    "draft=CASE WHEN draft=1 THEN 0 ELSE draft END "
                    "WHERE id=?",
                    (now, title, session_id),
                )
            self._conn.commit()

    def exists(self, session_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone()
        return row is not None

    def get_workspace_root(self, session_id: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT workspace_root FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            return str(row[0]) if row and row[0] else ""

    def list(self, *, include_drafts: bool = True) -> list[dict]:
        with self._lock:
            query = (
                "SELECT id, title, created_at, updated_at, draft, workspace_root "
                "FROM sessions "
            )
            if not include_drafts:
                query += "WHERE draft=0 "
            query += "ORDER BY updated_at DESC"
            rows = self._conn.execute(query).fetchall()
            result = []
            for row in rows:
                result.append({
                    "id": row[0], "title": row[1],
                    "created_at": row[2], "updated_at": row[3],
                    "draft": bool(row[4]),
                    "workspace_root": row[5],
                })
            return result

    @staticmethod
    def _validate_permission_values(
        mode: PermissionMode,
        auto_review_roots: list[str],
    ) -> list[str]:
        try:
            mode = PermissionMode(mode)
        except ValueError as exc:
            raise PermissionError("invalid_permission_mode", "未知的权限模式。") from exc
        return normalize_auto_review_roots(auto_review_roots)

    def _get_permission_settings_locked(self) -> PermissionContext:
        row = self._conn.execute(
            "SELECT mode, auto_review_roots, full_access_visible, version, "
            "updated_at, updated_by, execution_profile "
            "FROM permission_settings WHERE singleton=1",
        ).fetchone()
        assert row is not None
        return PermissionContext(
            mode=PermissionMode(row[0]),
            auto_review_roots=json.loads(row[1]),
            full_access_visible=bool(row[2]),
            version=row[3],
            updated_at=row[4],
            updated_by=row[5],
            execution_profile=row[6],
        )

    def get_permission_settings(self) -> PermissionContext:
        with self._lock:
            return self._get_permission_settings_locked()

    def _get_permissions_locked(self, session_id: str) -> PermissionContext | None:
        row = self._conn.execute(
            "SELECT full_access_enabled, permission_version, "
            "permission_updated_at, permission_updated_by, "
            "full_access_execution_profile FROM sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        base = self._get_permission_settings_locked()
        if not bool(row[0]):
            return base.model_copy(update={"session_id": session_id})
        return base.model_copy(update={
            "session_id": session_id,
            "mode": PermissionMode.FULL_ACCESS,
            "version": row[1],
            "updated_at": row[2],
            "updated_by": row[3],
            "execution_profile": row[4],
        })

    def get_permissions(self, session_id: str) -> PermissionContext | None:
        with self._lock:
            return self._get_permissions_locked(session_id)

    # 供流水线使用的语义化别名。
    get_permission_context = get_permissions

    def full_access_session_ids(self) -> list[str]:
        with self._lock:
            return [
                str(row[0]) for row in self._conn.execute(
                    "SELECT id FROM sessions WHERE full_access_enabled=1"
                ).fetchall()
            ]

    def set_session_full_access(
        self,
        session_id: str,
        *,
        enabled: bool,
        expected_version: int,
        updated_by: str,
        execution_profile: str = "",
        commit: bool = True,
    ) -> PermissionContext:
        """Enable or revoke full access for exactly one task."""
        now = time.time()
        enabled = bool(enabled)
        with self._lock:
            current = self._get_permissions_locked(session_id)
            if current is None:
                raise PermissionError("session_not_found", "会话不存在。")
            if current.version != expected_version:
                raise PermissionVersionConflict()
            row = self._conn.execute(
                "SELECT full_access_enabled, permission_version FROM sessions "
                "WHERE id=?", (session_id,),
            ).fetchone()
            assert row is not None
            if bool(row[0]) == enabled:
                return current
            next_version = max(int(row[1]), expected_version) + 1
            self._conn.execute(
                "UPDATE sessions SET full_access_enabled=?, permission_version=?, "
                "permission_updated_at=?, permission_updated_by=?, "
                "full_access_execution_profile=? WHERE id=?",
                (
                    int(enabled), next_version, now, updated_by,
                    execution_profile if enabled else "", session_id,
                ),
            )
            self._conn.execute(
                "UPDATE permission_grants SET revoked_at=? WHERE session_id=? "
                "AND revoked_at IS NULL AND consumed_at IS NULL",
                (now, session_id),
            )
            if commit:
                self._conn.commit()
            context = self._get_permissions_locked(session_id)
            assert context is not None
            return context

    def set_permission_settings(
        self,
        *,
        mode: PermissionMode,
        auto_review_roots: list[str] | None,
        expected_version: int,
        updated_by: str,
        execution_profile: str = "",
        commit: bool = True,
    ) -> PermissionContext:
        now = time.time()
        mode = PermissionMode(mode)
        if mode == PermissionMode.FULL_ACCESS:
            raise PermissionError(
                "full_access_session_required",
                "完全访问只能为具体任务开启，不能写入全局权限设置。",
            )
        roots = self._validate_permission_values(mode, auto_review_roots or [])
        with self._lock:
            row = self._conn.execute(
                "SELECT version FROM permission_settings WHERE singleton=1"
            ).fetchone()
            assert row is not None
            if row[0] != expected_version:
                raise PermissionVersionConflict()
            next_version = expected_version + 1
            self._conn.execute(
                "UPDATE permission_settings SET mode=?, auto_review_roots=?, "
                "version=?, updated_at=?, updated_by=?, execution_profile=? "
                "WHERE singleton=1 AND version=?",
                (mode.value, json.dumps(roots, ensure_ascii=False), next_version,
                 now, updated_by, execution_profile,
                 expected_version),
            )
            # 全局权限上下文改变后，所有会话中旧版本签发的授权全部失效。
            self._conn.execute(
                "UPDATE permission_grants SET revoked_at=? "
                "WHERE revoked_at IS NULL AND consumed_at IS NULL",
                (now,),
            )
            if commit:
                self._conn.commit()
            return self._get_permission_settings_locked()

    def set_full_access_visibility(
        self,
        *,
        visible: bool,
        expected_version: int,
        updated_by: str,
        commit: bool = True,
    ) -> PermissionContext:
        """单独揭示或隐藏完全访问入口。

        隐藏入口时若完全访问正在生效，会在同一事务中立即回落到 ASK。普通
        揭示/隐藏不改变执行授权版本，也不会使既有会话动作授权失效；只有
        隐藏正在生效的完全访问时才递增权限版本并收回授权。
        """
        now = time.time()
        visible = bool(visible)
        with self._lock:
            row = self._conn.execute(
                "SELECT mode, version FROM permission_settings WHERE singleton=1"
            ).fetchone()
            assert row is not None
            if row[1] != expected_version:
                raise PermissionVersionConflict()
            revoke_full_access = not visible and row[0] == PermissionMode.FULL_ACCESS.value
            if revoke_full_access:
                next_version = expected_version + 1
                self._conn.execute(
                    "UPDATE permission_settings SET full_access_visible=0, "
                    "mode='ask', execution_profile='', version=?, "
                    "updated_at=?, updated_by=? "
                    "WHERE singleton=1 AND version=?",
                    (next_version, now, updated_by, expected_version),
                )
            else:
                self._conn.execute(
                    "UPDATE permission_settings SET full_access_visible=?, "
                    "updated_at=?, updated_by=? "
                    "WHERE singleton=1 AND version=?",
                    (int(visible), now, updated_by, expected_version),
                )
            if revoke_full_access:
                self._conn.execute(
                    "UPDATE permission_grants SET revoked_at=? "
                    "WHERE revoked_at IS NULL AND consumed_at IS NULL",
                    (now,),
                )
            if commit:
                self._conn.commit()
            return self._get_permission_settings_locked()

    def add_grant(
        self,
        session_id: str,
        *,
        scope: PermissionGrantScope,
        action_fingerprint: str,
        capability: str,
        resource: str,
        context_version: int,
        granted_by: str,
        expires_at: float | None,
        commit: bool = True,
    ) -> PermissionGrant:
        if not action_fingerprint or not capability:
            raise PermissionError(
                "invalid_permission_grant", "授权缺少动作指纹或能力名称。"
            )
        now = time.time()
        if expires_at is not None and expires_at <= now:
            raise PermissionError("invalid_permission_ttl", "授权有效期已经结束。")
        scope = PermissionGrantScope(scope)
        with self._lock:
            context = self._get_permissions_locked(session_id)
            if context is None:
                raise PermissionError("session_not_found", "会话不存在。")
            if context.version != context_version:
                raise PermissionVersionConflict()
            grant = PermissionGrant(
                id=uuid.uuid4().hex,
                session_id=session_id,
                scope=scope,
                action_fingerprint=action_fingerprint,
                capability=capability,
                resource=resource,
                context_version=context_version,
                granted_by=granted_by,
                created_at=now,
                expires_at=expires_at,
            )
            self._conn.execute(
                "INSERT INTO permission_grants"
                "(id, session_id, scope, action_fingerprint, capability, "
                "resource, context_version, granted_by, created_at, expires_at, "
                "expiry_observed_at, consumed_at, revoked_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)",
                (grant.id, grant.session_id, grant.scope.value,
                 grant.action_fingerprint, grant.capability, grant.resource,
                 grant.context_version, grant.granted_by, grant.created_at,
                 grant.expires_at),
            )
            if commit:
                self._conn.commit()
            return grant

    @staticmethod
    def _grant_from_row(row: tuple) -> PermissionGrant:
        return PermissionGrant(
            id=row[0], session_id=row[1], scope=row[2],
            action_fingerprint=row[3], capability=row[4], resource=row[5],
            context_version=row[6], granted_by=row[7], created_at=row[8],
            expires_at=row[9], expiry_observed_at=row[10],
            consumed_at=row[11], revoked_at=row[12],
        )

    def list_grants(
        self, session_id: str, *, active_only: bool = True,
        now: float | None = None,
    ) -> list[PermissionGrant]:
        at = time.time() if now is None else now
        with self._lock:
            owned_transaction = not self._conn.in_transaction
            self._conn.execute(
                "UPDATE permission_grants SET expiry_observed_at=? "
                "WHERE session_id=? AND expiry_observed_at IS NULL "
                "AND expires_at IS NOT NULL AND expires_at<=?",
                (at, session_id, at),
            )
            if owned_transaction:
                self._conn.commit()
            rows = self._conn.execute(
                "SELECT id, session_id, scope, action_fingerprint, capability, "
                "resource, context_version, granted_by, created_at, expires_at, "
                "expiry_observed_at, consumed_at, revoked_at FROM permission_grants "
                "WHERE session_id=? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        grants = [self._grant_from_row(row) for row in rows]
        if not active_only:
            return grants
        context = self.get_permissions(session_id)
        if context is None:
            return []
        return [
            grant for grant in grants
            if grant.context_version == context.version
            and grant.revoked_at is None
            and grant.consumed_at is None
            and grant.expiry_observed_at is None
            and (grant.expires_at is None or grant.expires_at > at)
        ]

    def consume_matching_grant(
        self,
        session_id: str,
        *,
        action_fingerprint: str,
        capability: str,
        resource: str = "",
        grant_id: str | None = None,
        now: float | None = None,
        consume_once: bool = True,
        commit: bool = True,
    ) -> PermissionGrant | None:
        at = time.time() if now is None else now
        with self._lock:
            candidates = self.list_grants(session_id, active_only=True, now=at)
            grant = next((
                item for item in candidates
                if item.capability == capability
                and item.resource == resource
                and item.action_fingerprint == action_fingerprint
                and (
                    (item.scope == PermissionGrantScope.SESSION
                     and (grant_id is None or item.id == grant_id))
                    or (item.scope == PermissionGrantScope.ONCE
                        and grant_id is not None and item.id == grant_id)
                )
            ), None)
            if grant is None:
                return None
            if grant.scope == PermissionGrantScope.ONCE and consume_once:
                cursor = self._conn.execute(
                    "UPDATE permission_grants SET consumed_at=? WHERE id=? "
                    "AND consumed_at IS NULL AND revoked_at IS NULL",
                    (at, grant.id),
                )
                if cursor.rowcount != 1:
                    return None
                if commit:
                    self._conn.commit()
                grant = grant.model_copy(update={"consumed_at": at})
            return grant

    def find_matching_grant(
        self,
        session_id: str,
        *,
        action_fingerprint: str,
        capability: str,
        resource: str = "",
        grant_id: str | None = None,
        now: float | None = None,
    ) -> PermissionGrant | None:
        """只检查授权，不提前消费一次性授权；真正执行前再原子消费。"""
        return self.consume_matching_grant(
            session_id,
            action_fingerprint=action_fingerprint,
            capability=capability,
            resource=resource,
            grant_id=grant_id,
            now=now,
            consume_once=False,
            commit=False,
        )

    def revoke_grants(
        self,
        session_id: str,
        grant_id: str | None = None,
        *,
        commit: bool = True,
    ) -> int:
        now = time.time()
        with self._lock:
            if grant_id:
                cursor = self._conn.execute(
                    "UPDATE permission_grants SET revoked_at=? "
                    "WHERE session_id=? AND id=? AND revoked_at IS NULL "
                    "AND consumed_at IS NULL",
                    (now, session_id, grant_id),
                )
            else:
                cursor = self._conn.execute(
                    "UPDATE permission_grants SET revoked_at=? "
                    "WHERE session_id=? AND revoked_at IS NULL "
                    "AND consumed_at IS NULL",
                    (now, session_id),
                )
            if commit:
                self._conn.commit()
            return cursor.rowcount

    def close(self) -> None:
        self._conn.close()
