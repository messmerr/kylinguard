"""子进程环境最小化。

Agent 的控制面通常持有 LLM 密钥、管理员口令和代理凭据。工具服务器与
被执行命令不需要这些能力，因此仅向下传递进程启动、区域设置和临时目录
所需的少量变量。环境采用允许列表而非敏感变量黑名单，避免遗漏新密钥。
"""
from __future__ import annotations

import os
from collections.abc import Mapping


_PASSTHROUGH_KEYS = (
    # 可执行文件发现与基本用户目录。
    "PATH",
    "HOME",
    "USERPROFILE",
    # 区域和时区；不保留代理、认证代理或桌面会话凭据。
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    # 临时目录（Windows 与 POSIX 名称）。
    "TMPDIR",
    "TMP",
    "TEMP",
    # Windows 创建进程与查找系统组件需要的非秘密变量。
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
)


def safe_subprocess_env(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """返回供不可信子进程使用的最小环境。

    特意不传递任何 ``KG_*``、``*_API_KEY``、代理变量、
    ``SSH_AUTH_SOCK``、云凭据或认证令牌。Python 工具服务器强制使用
    UTF-8，以保证 MCP stdio 协议在中文系统上也稳定。
    """
    source = os.environ if source is None else source
    env = {
        key: value
        for key in _PASSTHROUGH_KEYS
        if (value := source.get(key))
    }
    env.setdefault("PATH", os.defpath)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env
