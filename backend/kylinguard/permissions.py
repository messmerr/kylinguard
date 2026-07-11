"""会话权限内核：权限模式、可信目录规范化与待决授权请求。

持久化由 :mod:`kylinguard.sessions` 负责；本模块刻意不依赖执行器，因而
``full_access`` 的含义始终是“跳过产品内逐项询问”，而不是获取 root。
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

from kylinguard.models import (
    PermissionDecision,
    PermissionGrant,
    PermissionGrantScope,
    PermissionMode,
    PermissionRequest,
    PermissionResolution,
    SessionPermissionContext,
)

_MAX_TRUSTED_ROOTS = 32
_MAX_PATH_LENGTH = 4096


class PermissionError(RuntimeError):
    """权限状态或请求不合法。``code`` 可安全返回给前端。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class PermissionVersionConflict(PermissionError):
    def __init__(self):
        super().__init__(
            "permission_version_conflict",
            "会话权限已被更新，请刷新当前权限状态后重试。",
        )


def normalize_trusted_root(value: str) -> str:
    """把服务端目录规范化为绝对路径，并拒绝含糊或全盘范围。

    这里只定义授权边界。执行文件操作时仍须重新解析目标路径并防御符号
    链接与 TOCTOU，不能把本函数当成文件系统沙箱。
    """
    if not isinstance(value, str):
        raise PermissionError("invalid_trusted_root", "可信目录必须是路径字符串。")
    raw = value.strip()
    if not raw or "\x00" in raw or len(raw) > _MAX_PATH_LENGTH:
        raise PermissionError("invalid_trusted_root", "可信目录路径为空或格式无效。")
    if raw.startswith("~"):
        raise PermissionError(
            "invalid_trusted_root", "可信目录不能使用 ~，请填写服务端绝对路径。"
        )
    path = Path(raw)
    if not path.is_absolute():
        raise PermissionError(
            "invalid_trusted_root", "可信目录必须是服务端绝对路径。"
        )
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PermissionError("invalid_trusted_root", "可信目录路径无法解析。") from exc
    # 信任文件系统根目录等价于放开全部文件写入，应使用 full_access 并复验。
    if resolved.parent == resolved:
        raise PermissionError(
            "trusted_root_too_broad", "不能把文件系统根目录设为可信目录。"
        )
    return str(resolved)


def normalize_trusted_roots(values: list[str] | tuple[str, ...]) -> list[str]:
    if len(values) > _MAX_TRUSTED_ROOTS:
        raise PermissionError(
            "too_many_trusted_roots", f"单个会话最多信任 {_MAX_TRUSTED_ROOTS} 个目录。"
        )
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        root = normalize_trusted_root(value)
        key = os.path.normcase(root)
        if key not in seen:
            seen.add(key)
            result.append(root)
    return result


def expires_after(ttl_seconds: int, *, now: float | None = None) -> float:
    if ttl_seconds <= 0:
        raise PermissionError("invalid_permission_ttl", "权限有效期必须大于 0 秒。")
    return (time.time() if now is None else now) + ttl_seconds


class PermissionRequests:
    """内存中的待决权限请求，绑定动作指纹与权限上下文版本。

    流水线创建请求后等待 Future；API 只能解析仍待决且版本匹配的请求。
    服务重启会自然丢弃待决请求，但持久化 grant 不受影响。
    """

    def __init__(self):
        self._pending: dict[str, tuple[PermissionRequest, asyncio.Future]] = {}

    def create(
        self,
        session_id: str,
        action_fingerprint: str,
        context_version: int,
        capability: str,
        resource: str = "",
        suggested_path: str = "",
        requires_reauthentication: bool = False,
    ) -> tuple[str, asyncio.Future]:
        if not action_fingerprint or not capability:
            raise PermissionError(
                "invalid_permission_request", "权限请求缺少动作指纹或能力名称。"
            )
        request_id = uuid.uuid4().hex
        request = PermissionRequest(
            id=request_id,
            session_id=session_id,
            action_fingerprint=action_fingerprint,
            context_version=context_version,
            capability=capability,
            resource=resource,
            suggested_path=suggested_path,
            requires_reauthentication=requires_reauthentication,
            created_at=time.time(),
        )
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (request, future)
        return request_id, future

    def get(self, request_id: str) -> PermissionRequest | None:
        entry = self._pending.get(request_id)
        if entry is None or entry[1].done():
            return None
        return entry[0]

    def resolve(self, resolution: PermissionResolution) -> bool:
        entry = self._pending.pop(resolution.request_id, None)
        if entry is None:
            return False
        request, future = entry
        if future.done():
            return False
        if resolution.context_version != request.context_version:
            # 不消费请求，让调用端刷新后仍可显式拒绝；不能错误批准旧动作。
            self._pending[resolution.request_id] = entry
            raise PermissionVersionConflict()
        future.set_result(resolution)
        return True

    def cancel(self, request_id: str, operator: str = "system") -> bool:
        entry = self._pending.pop(request_id, None)
        if entry is None or entry[1].done():
            return False
        request, future = entry
        future.set_result(PermissionResolution(
            request_id=request.id,
            decision=PermissionDecision.DENY,
            operator=operator,
            context_version=request.context_version,
        ))
        return True

    def revoke_session(self, session_id: str, operator: str) -> int:
        request_ids = [
            request_id
            for request_id, (request, _) in self._pending.items()
            if request.session_id == session_id
        ]
        return sum(self.cancel(request_id, operator) for request_id in request_ids)


__all__ = [
    "PermissionDecision",
    "PermissionError",
    "PermissionGrant",
    "PermissionGrantScope",
    "PermissionMode",
    "PermissionRequest",
    "PermissionRequests",
    "PermissionResolution",
    "PermissionVersionConflict",
    "SessionPermissionContext",
    "expires_after",
    "normalize_trusted_root",
    "normalize_trusted_roots",
]
