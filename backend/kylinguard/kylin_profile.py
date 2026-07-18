"""银河麒麟运行环境识别与部署就绪度采集。

本模块只执行只读命令，并把系统文件中的自由文本收敛为固定字段。调用方可以
把结构化结果安全地放入系统快照、MCP 输出和前端状态卡，而不必让模型自行猜测
发行版、架构或工具可用性。
"""
from __future__ import annotations

import asyncio
import json
import platform
import re
import shlex
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from kylinguard.executor import run_command
from kylinguard.models import ExecResult

CommandRunner = Callable[..., Awaitable[ExecResult]]

_OS_RELEASE_FIELDS = {
    "ID", "ID_LIKE", "NAME", "PRETTY_NAME", "VERSION", "VERSION_ID",
    "VARIANT", "VARIANT_ID",
}
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_VERSION_11 = re.compile(r"(?<!\d)(?:v\s*)?11(?:\D|$)", re.IGNORECASE)

_NATIVE_TOOLS = (
    ("version_query", "系统版本查询", ("nkvers",)),
    ("kernel_alerts", "内核故障预警", ("kalert",)),
    ("kernel_observability", "内核可观测工具集", ("ketones",)),
    ("network_observability", "网络故障分析", ("netmaster", "kynetobser")),
    ("io_diagnosis", "存储 I/O 诊断", ("kylin-iodiag-tools",)),
    ("system_assistant", "系统体检与故障诊断", ("kylin-sysassist",)),
)

_FALLBACK_CAPABILITIES = (
    ("service_diagnosis", "服务诊断", ("systemctl", "journalctl")),
    ("network_diagnosis", "网络诊断", ("ip", "ss")),
    ("io_diagnosis", "I/O 诊断", ("lsblk",), ("iostat", "cat")),
    ("binary_inspection", "二进制兼容检查", ("file", "readelf")),
)

_REQUIRED_COMMANDS = (
    "systemctl", "journalctl", "ps", "free", "df", "ss", "find", "stat",
)
_OPTIONAL_COMMANDS = ("lsof", "iostat", "ip", "lsblk", "file", "readelf")


def _clean_text(value: Any, *, limit: int = 512) -> str:
    """把外部文本压成单行并限制长度，避免控制字符进入提示词。"""
    text = _CONTROL_CHARS.sub(" ", str(value or ""))
    text = " ".join(text.split())
    return text[:limit]


def parse_os_release(raw: str) -> dict[str, str]:
    """解析 freedesktop ``os-release`` 中与平台识别有关的白名单字段。"""
    parsed: dict[str, str] = {}
    for line in str(raw or "").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in _OS_RELEASE_FIELDS:
            continue
        value = value.strip()
        if value[:1] in {"\"", "'"}:
            try:
                words = shlex.split(value, posix=True)
                value = " ".join(words)
            except ValueError:
                value = value.strip("\"'")
        parsed[key] = _clean_text(value, limit=256)
    return parsed


def normalize_architecture(raw: str) -> str:
    value = _clean_text(raw, limit=64).casefold().replace("-", "_")
    aliases = {
        "loong64": "loongarch64",
        "loongarch64": "loongarch64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
        "amd64": "x86_64",
        "x86_64": "x86_64",
    }
    return aliases.get(value, value or "unknown")


async def _capture(
    runner: CommandRunner,
    argv: list[str],
    *,
    timeout: int = 8,
    max_output: int = 4096,
) -> dict[str, Any]:
    try:
        result = await runner(argv, timeout=timeout, max_output=max_output)
    except Exception as exc:  # 单项采集失败不得阻断整个平台画像
        return {
            "ok": False,
            "exit_code": 127,
            "stdout": "",
            "stderr": _clean_text(exc),
        }
    return {
        "ok": result.exit_code == 0 and not result.timed_out,
        "exit_code": result.exit_code,
        "stdout": _clean_text(result.stdout, limit=max_output),
        "stderr": _clean_text(result.stderr, limit=512),
    }


def _edition(combined: str) -> str:
    if "高级服务器" in combined or "server" in combined:
        return "server"
    if "桌面" in combined or "desktop" in combined:
        return "desktop"
    return "unknown"


def _contest_status(
    *,
    kylin: bool,
    loongarch: bool,
    version_v11: bool | None,
    server_edition: bool | None,
) -> str:
    known = (kylin, loongarch, version_v11, server_edition)
    if any(value is False for value in known):
        return "mismatch"
    if all(value is True for value in known):
        return "matched"
    return "partial"


async def collect_kylin_identity(
    *,
    runner: CommandRunner | None = None,
    platform_name: str | None = None,
) -> dict[str, Any]:
    """采集发行版、版本、架构和 systemd 证据，返回稳定结构。"""
    runner = runner or run_command
    current_platform = platform_name or sys.platform
    if not current_platform.startswith("linux"):
        arch_raw = platform.machine() or "unknown"
        return {
            "schema_version": 1,
            "platform": current_platform,
            "kylin": {
                "detected": False,
                "version": "",
                "edition": "unknown",
                "nkvers": "",
            },
            "os": {"id": "", "name": platform.system(), "pretty_name": platform.platform(),
                   "version_id": ""},
            "architecture": {
                "raw": arch_raw,
                "normalized": normalize_architecture(arch_raw),
                "loongarch": False,
            },
            "kernel": {"release": platform.release()},
            "runtime": {"init_system": "unknown", "systemd_version": "", "glibc": ""},
            "contest_target": {
                "kylin": False,
                "version_v11": None,
                "server_edition": None,
                "loongarch": False,
                "status": "mismatch",
            },
            "evidence_sources": [],
            "warnings": ["当前不是 Linux，无法确认银河麒麟运行环境。"],
        }

    commands = {
        "os_release": ["cat", "/etc/os-release"],
        "kylin_release": ["cat", "/etc/kylin-release"],
        "nkvers": ["nkvers"],
        "architecture": ["uname", "-m"],
        "kernel": ["uname", "-r"],
        "systemd": ["systemd", "--version"],
        "glibc": ["getconf", "GNU_LIBC_VERSION"],
    }
    captured_values = await asyncio.gather(*(
        _capture(runner, argv) for argv in commands.values()
    ))
    captured = dict(zip(commands, captured_values, strict=True))

    os_fields = parse_os_release(captured["os_release"]["stdout"])
    nkvers = _clean_text(captured["nkvers"]["stdout"], limit=512)
    kylin_release = _clean_text(captured["kylin_release"]["stdout"], limit=512)
    identity_text = " ".join((
        os_fields.get("ID", ""), os_fields.get("ID_LIKE", ""),
        os_fields.get("NAME", ""), os_fields.get("PRETTY_NAME", ""),
        os_fields.get("VERSION", ""), nkvers, kylin_release,
    ))
    identity_folded = identity_text.casefold()
    detected = "kylin" in identity_folded or "麒麟" in identity_text
    edition = _edition(identity_folded)

    version_text = " ".join((
        os_fields.get("VERSION_ID", ""), os_fields.get("VERSION", ""),
        os_fields.get("PRETTY_NAME", ""), nkvers, kylin_release,
    ))
    version_v11 = bool(_VERSION_11.search(version_text)) if version_text.strip() else None
    server_edition = (
        True if edition == "server" else False if edition == "desktop" else None
    )
    arch_raw = captured["architecture"]["stdout"] or platform.machine() or "unknown"
    arch = normalize_architecture(arch_raw)
    loongarch = arch == "loongarch64"
    status = _contest_status(
        kylin=detected,
        loongarch=loongarch,
        version_v11=version_v11,
        server_edition=server_edition,
    )

    warnings: list[str] = []
    if not detected:
        warnings.append("未从 nkvers、/etc/kylin-release 或 os-release 确认银河麒麟。")
    if not loongarch:
        warnings.append(f"当前架构为 {arch}，不是赛题指定的 LoongArch64。")
    if version_v11 is None:
        warnings.append("未取得足够证据确认银河麒麟 V11。")
    elif not version_v11:
        warnings.append("已识别的系统版本不是赛题指定的 V11。")
    if server_edition is None:
        warnings.append("未取得足够证据确认高级服务器版。")
    elif not server_edition:
        warnings.append("已识别的系统不是高级服务器版。")

    sources = [name for name, item in captured.items() if item["ok"]]
    systemd_line = captured["systemd"]["stdout"].split(" ", 2)
    systemd_version = systemd_line[1] if len(systemd_line) > 1 else ""
    version_match = re.search(r"(?<!\d)(V?11(?:\s+\d{4})?)(?:\D|$)",
                              version_text, re.IGNORECASE)
    detected_version = (
        os_fields.get("VERSION_ID", "")
        or (version_match.group(1) if version_match else "")
    )
    return {
        "schema_version": 1,
        "platform": "linux",
        "kylin": {
            "detected": detected,
            "version": detected_version,
            "edition": edition,
            "nkvers": nkvers,
        },
        "os": {
            "id": os_fields.get("ID", ""),
            "name": os_fields.get("NAME", ""),
            "pretty_name": os_fields.get("PRETTY_NAME", ""),
            "version_id": os_fields.get("VERSION_ID", ""),
        },
        "architecture": {"raw": arch_raw, "normalized": arch, "loongarch": loongarch},
        "kernel": {"release": captured["kernel"]["stdout"]},
        "runtime": {
            "init_system": "systemd" if captured["systemd"]["ok"] else "unknown",
            "systemd_version": systemd_version,
            "glibc": captured["glibc"]["stdout"],
        },
        "contest_target": {
            "kylin": detected,
            "version_v11": version_v11,
            "server_edition": server_edition,
            "loongarch": loongarch,
            "status": status,
        },
        "evidence_sources": sources,
        "warnings": warnings,
    }


def collect_capability_matrix(
    *,
    which: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """探测麒麟原生工具及确定的通用降级链，不执行未知 CLI。"""
    which = which or shutil.which
    native = []
    for capability_id, label, candidates in _NATIVE_TOOLS:
        resolved = [(name, which(name)) for name in candidates]
        found = [(name, path) for name, path in resolved if path]
        native.append({
            "id": capability_id,
            "label": label,
            "available": bool(found),
            "executable": found[0][0] if found else "",
            "path": found[0][1] if found else "",
            "candidates_checked": list(candidates),
            "invocation": "identity_verified" if capability_id == "version_query" and found
            else "capability_detected" if found else "unavailable",
        })

    fallbacks = []
    for item in _FALLBACK_CAPABILITIES:
        capability_id, label, required, *optional_groups = item
        required_paths = {name: which(name) or "" for name in required}
        optional = tuple(optional_groups[0]) if optional_groups else ()
        optional_paths = {name: which(name) or "" for name in optional}
        fallbacks.append({
            "id": capability_id,
            "label": label,
            "available": all(required_paths.values()),
            "required": required_paths,
            "optional": optional_paths,
        })

    package_candidates = ("dnf", "yum", "apt-get")
    package_manager = next((name for name in package_candidates if which(name)), "")
    native_count = sum(1 for item in native if item["available"])
    return {
        "schema_version": 1,
        "strategy": "kylin_native_preferred_with_linux_fallback",
        "native_available": native_count,
        "native_total": len(native),
        "native_tools": native,
        "fallback_capabilities": fallbacks,
        "package_manager": package_manager,
    }


def _major_version(raw: str) -> int | None:
    match = re.search(r"(?:^|\s)v?(\d+)(?:\.\d+)?", str(raw or ""))
    return int(match.group(1)) if match else None


async def collect_deployment_readiness(
    *,
    runner: CommandRunner | None = None,
    which: Callable[[str], str | None] | None = None,
    platform_name: str | None = None,
) -> dict[str, Any]:
    """验证源码在赛题目标机上构建、运行所需的关键条件。"""
    runner = runner or run_command
    which = which or shutil.which
    profile = await collect_kylin_identity(
        runner=runner, platform_name=platform_name)
    commands = {}
    for name in (*_REQUIRED_COMMANDS, *_OPTIONAL_COMMANDS, "node", "npm"):
        resolved = which(name) or ""
        commands[name] = {"available": bool(resolved), "path": resolved}

    async def version_of(name: str) -> str:
        if not commands[name]["available"]:
            return ""
        captured = await _capture(runner, [name, "--version"], timeout=8, max_output=1024)
        return captured["stdout"] or captured["stderr"]

    node_version, npm_version = await asyncio.gather(
        version_of("node"), version_of("npm"))
    node_major = _major_version(node_version)
    python_ok = sys.version_info >= (3, 10)
    missing_required = [name for name in _REQUIRED_COMMANDS if not commands[name]["available"]]

    blockers: list[str] = []
    warnings: list[str] = []
    target = profile["contest_target"]
    if profile["platform"] != "linux":
        blockers.append("必须在 Linux/银河麒麟主机上运行后端。")
    if not target["kylin"]:
        blockers.append("未确认银河麒麟操作系统。")
    if not target["loongarch"]:
        blockers.append("当前不是赛题指定的 LoongArch64 架构。")
    if target["version_v11"] is False:
        blockers.append("当前不是赛题指定的银河麒麟 V11。")
    elif target["version_v11"] is None:
        warnings.append("银河麒麟 V11 版本尚未确认。")
    if target["server_edition"] is False:
        blockers.append("当前不是赛题指定的高级服务器版。")
    elif target["server_edition"] is None:
        warnings.append("高级服务器版身份尚未确认。")
    if not python_ok:
        blockers.append("Python 版本低于项目要求的 3.10。")
    if not commands["node"]["available"]:
        blockers.append("未安装 Node.js，无法在目标机从源码构建前端。")
    elif node_major is None:
        warnings.append("无法解析 Node.js 版本。")
    elif node_major < 18:
        blockers.append(f"Node.js 主版本为 {node_major}，低于项目要求的 18。")
    if not commands["npm"]["available"]:
        blockers.append("未安装 npm，无法在目标机从源码构建前端。")
    if missing_required:
        blockers.append("缺少关键系统命令：" + "、".join(missing_required))
    missing_optional = [name for name in _OPTIONAL_COMMANDS if not commands[name]["available"]]
    if missing_optional:
        warnings.append("缺少可选诊断命令，将使用降级路径：" + "、".join(missing_optional))

    return {
        "schema_version": 1,
        "ready": not blockers,
        "status": "ready" if not blockers else "blocked",
        "target_status": target["status"],
        "python": {
            "version": platform.python_version(),
            "meets_minimum": python_ok,
        },
        "node": {
            "available": commands["node"]["available"],
            "version": node_version,
            "meets_minimum": node_major is not None and node_major >= 18,
        },
        "npm": {"available": commands["npm"]["available"], "version": npm_version},
        "commands": commands,
        "blockers": blockers,
        "warnings": warnings,
    }


def identity_json(profile: dict[str, Any]) -> str:
    return json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True)
