"""按能力边界构造子进程环境。

Agent 的控制面通常持有 LLM 密钥和代理凭据。工具服务器与
被执行命令不需要这些能力，因此仅向下传递进程启动、区域设置和临时目录
所需的少量变量。环境采用允许列表而非敏感变量黑名单，避免遗漏新密钥。

通用终端是例外：Git、SSH、代理、虚拟环境和语言工具链依赖用户会话环境。
``agent_subprocess_env`` 因而保留普通用户环境，但始终剥离 ``KG_*`` 控制面
配置；权限模式负责决定命令何时可运行，不能再靠清空开发环境阉割能力。
"""
from __future__ import annotations

import os
from collections.abc import Mapping


_PASSTHROUGH_KEYS = (
    # 基本用户目录。PATH 在下方固定，不能让只读插件被用户目录中的同名
    # ps/cat/ss 或动态加载器环境劫持。
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

_SAFE_POSIX_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


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
    env["PATH"] = (
        _SAFE_POSIX_PATH if os.name == "posix"
        else source.get("PATH", os.defpath)
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def agent_subprocess_env(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """返回通用 Agent 终端使用的用户环境，排除 KylinGuard 控制面配置。

    这会保留 ``SSH_AUTH_SOCK``、代理、虚拟环境、SDK 路径和用户项目变量，
    使获准的终端调用与当前 WSL 用户实际终端具有相同工具链能力。所有
    ``KG_*`` 值（LLM Key、数据库配置等）都不会进入执行面；
    MCP 插件自身需要的少量非秘密 ``KG_*`` 设置由启动器随后逐项补入。
    """
    source = os.environ if source is None else source
    env = {
        key: value
        for key, value in source.items()
        if not key.upper().startswith("KG_")
    }
    env.setdefault(
        "PATH", _SAFE_POSIX_PATH if os.name == "posix" else os.defpath,
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env
