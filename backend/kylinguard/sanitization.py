"""进入 LLM、SSE 与普通审计载荷前的最小敏感信息脱敏。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_SECRET_KEY = re.compile(
    r"(?i)(password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"authorization|client[_-]?secret|private[_-]?key|ssh_auth_sock)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|API_KEY|TOKEN|SECRET|PRIVATE_KEY)"
    r"[A-Z0-9_]*)=([^\s]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/]+=*")
_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


def redact_text(text: str) -> str:
    """保留诊断结构，同时移除常见凭据值。"""
    value = str(text)
    value = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _API_KEY.sub("sk-[REDACTED]", value)


def _content_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode("utf-8", errors="replace")
    return {
        "redacted": True,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def redact_value(value: Any, *, key: str = "") -> Any:
    """递归清洗结构化载荷；文件正文只记录长度与摘要。"""
    lowered = key.lower()
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if lowered in {"content", "replacement", "old_text", "new_text"}:
        return _content_summary(value)
    if isinstance(value, dict):
        return {str(k): redact_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def safe_step(step) -> dict[str, Any]:
    """返回适合审计/展示的 PlanStep，不改变真实执行参数。"""
    raw = step.model_dump() if hasattr(step, "model_dump") else dict(step)
    return redact_value(raw)


def canonical_fingerprint(payload: dict[str, Any]) -> str:
    """为标准化动作生成稳定指纹，供授权绑定与防重复使用。"""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
