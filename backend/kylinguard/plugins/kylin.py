"""银河麒麟环境与根因诊断 MCP 服务器。

工具全部只读。麒麟原生组件只在能够确认公开调用契约时执行；其余组件只做
能力探测，实际诊断使用稳定的 systemd、iproute2、sysstat 和 binutils 接口，
并在输出中标明证据来源与降级限制。
"""
from __future__ import annotations

import asyncio
import json
import posixpath
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from kylinguard.executor import run_command
from kylinguard.kylin_profile import (
    collect_capability_matrix,
    collect_deployment_readiness,
    collect_kylin_identity,
    identity_json,
    normalize_architecture,
)
from kylinguard.models import ExecResult
from kylinguard.plugins._result import format_exec_result, reject

mcp = FastMCP("kylin")

_UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,255}$")
_INTERFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,63}$")
_DEVICE_RE = re.compile(r"^/dev/[A-Za-z0-9_./:+-]{1,240}$")
_FORBIDDEN_BINARY_PREFIXES = ("/proc", "/sys", "/dev", "/run")
_SERVICE_PROPERTIES = (
    "Id,LoadState,ActiveState,SubState,UnitFileState,Result,"
    "ExecMainCode,ExecMainStatus,FragmentPath,NeedDaemonReload,NRestarts"
)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _evidence(
    result: ExecResult,
    *,
    ok_codes: tuple[int, ...] = (0,),
    limit: int = 32768,
) -> dict[str, Any]:
    return {
        "ok": not result.timed_out and result.exit_code in ok_codes,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "stdout": result.stdout[:limit],
        "stderr": result.stderr[:2048],
        "truncated": result.truncated or len(result.stdout) > limit,
    }


def _evidence_status(
    result: ExecResult,
    *,
    ok_codes: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    """结构化正文已单独返回时，只保留执行状态，避免重复放大 MCP 输出。"""
    return {
        "ok": not result.timed_out and result.exit_code in ok_codes,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "stderr": result.stderr[:2048],
        "truncated": result.truncated,
    }


def _parse_properties(raw: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in raw.splitlines():
        key, sep, value = line.partition("=")
        if sep and key:
            properties[key] = value
    return properties


def _safe_json_list(raw: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _native_available(capability_id: str) -> list[str]:
    matrix = collect_capability_matrix()
    return [
        item["executable"] for item in matrix["native_tools"]
        if item["id"] == capability_id and item["available"]
    ]


@mcp.tool()
async def system_identity() -> str:
    """识别银河麒麟版本、服务器版身份、LoongArch 架构、内核与 systemd。"""
    return identity_json(await collect_kylin_identity())


@mcp.tool()
async def capability_matrix() -> str:
    """探测麒麟原生诊断组件和通用 Linux 降级链，不执行未知参数。"""
    return _json(collect_capability_matrix())


@mcp.tool()
async def deployment_readiness() -> str:
    """检查赛题目标环境以及源码构建、运行所需的 Python、Node 和系统命令。"""
    return _json(await collect_deployment_readiness())


@mcp.tool()
async def service_diagnosis(name: str, lines: int = 100) -> str:
    """汇总 systemd 单元状态、退出码和近期日志用于根因分析。lines 取 20-300。"""
    if not _UNIT_RE.fullmatch(name) or not (20 <= lines <= 300):
        reject("参数不合法：服务名只允许字母数字及 ._@:-，lines 取 20-300")

    show_result, journal_result = await asyncio.gather(
        run_command([
            "systemctl", "show", name, "--no-pager",
            f"--property={_SERVICE_PROPERTIES}",
        ], timeout=15, max_output=16384),
        run_command([
            "journalctl", "-u", name, "-n", str(lines), "--no-pager",
            "-o", "short-iso",
        ], timeout=20, max_output=32768),
    )
    show = _evidence(show_result)
    journal = _evidence(journal_result)
    if show_result.exit_code == 127 and journal_result.exit_code == 127:
        reject(
            "systemd 服务诊断不可用：systemctl 与 journalctl 均无法启动。\n"
            f"systemctl:\n{format_exec_result(show_result)}\n"
            f"journalctl:\n{format_exec_result(journal_result)}"
        )

    properties = _parse_properties(show_result.stdout) if show["ok"] else {}
    findings: list[dict[str, str]] = []
    load_state = properties.get("LoadState", "")
    active_state = properties.get("ActiveState", "")
    result = properties.get("Result", "")
    exit_status = properties.get("ExecMainStatus", "")
    restarts = properties.get("NRestarts", "")
    if load_state and load_state != "loaded":
        findings.append({"kind": "unit_load", "value": load_state,
                         "meaning": "单元未正常加载，应先检查名称、单元文件或依赖。"})
    if active_state == "failed":
        findings.append({"kind": "failed_state", "value": active_state,
                         "meaning": "systemd 已将该单元标记为失败。"})
    if result and result not in {"success", "done"}:
        findings.append({"kind": "unit_result", "value": result,
                         "meaning": "最近一次启动或运行结果不是成功。"})
    if exit_status and exit_status != "0":
        findings.append({"kind": "main_exit_status", "value": exit_status,
                         "meaning": "主进程返回了非零状态。"})
    if restarts.isdigit() and int(restarts) > 0:
        findings.append({"kind": "restart_loop", "value": restarts,
                         "meaning": "单元发生过自动重启，应结合最早失败日志定位首因。"})
    if not journal["ok"]:
        findings.append({"kind": "evidence_gap", "value": "journal_unavailable",
                         "meaning": "近期日志未取得，不能仅凭状态字段断言根因。"})

    return _json({
        "schema_version": 1,
        "unit": name,
        "properties": properties,
        "findings": findings,
        "evidence_complete": bool(show["ok"] and journal["ok"]),
        "evidence": {"systemctl_show": show, "journal": journal},
        "limitations": [
            "日志中的相关性不等于因果关系；必须优先解释最早的明确失败。",
            "本工具只读，不会启动、停止、重启或修改单元。",
        ],
    })


def _network_interfaces(addresses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reduced = []
    for item in addresses[:64]:
        address_info = []
        for address in item.get("addr_info", [])[:32]:
            if not isinstance(address, dict):
                continue
            address_info.append({key: address.get(key) for key in (
                "family", "local", "prefixlen", "scope", "label",
            ) if address.get(key) not in {None, ""}})
        reduced.append({
            "ifname": item.get("ifname", ""),
            "operstate": item.get("operstate", ""),
            "mtu": item.get("mtu"),
            "flags": item.get("flags", []),
            "addresses": address_info,
        })
    return reduced


def _network_counters(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reduced = []
    for item in links[:64]:
        stats = item.get("stats64") or item.get("stats") or {}
        rx = stats.get("rx", {}) if isinstance(stats, dict) else {}
        tx = stats.get("tx", {}) if isinstance(stats, dict) else {}
        reduced.append({
            "ifname": item.get("ifname", ""),
            "rx": {key: rx.get(key, 0) for key in ("bytes", "packets", "errors", "dropped")},
            "tx": {key: tx.get(key, 0) for key in ("bytes", "packets", "errors", "dropped")},
        })
    return reduced


@mcp.tool()
async def network_diagnosis(interface: str = "") -> str:
    """采集接口地址、链路计数、路由和连接汇总。interface 留空表示所有接口。"""
    if interface and not _INTERFACE_RE.fullmatch(interface):
        reject("参数不合法：interface 只允许字母数字及 ._:@-")
    suffix = ["dev", interface] if interface else []
    address_cmd = ["ip", "-j", "address", "show", *suffix]
    link_cmd = ["ip", "-j", "-s", "link", "show", *suffix]
    route_cmd = ["ip", "-j", "route", "show"]
    address_result, link_result, route_result, socket_result = await asyncio.gather(
        run_command(address_cmd, timeout=12, max_output=32768),
        run_command(link_cmd, timeout=12, max_output=32768),
        run_command(route_cmd, timeout=12, max_output=32768),
        run_command(["ss", "-s"], timeout=10, max_output=8192),
    )
    evidence = {
        "addresses": _evidence_status(address_result),
        "links": _evidence_status(link_result),
        "routes": _evidence_status(route_result),
        "sockets": _evidence_status(socket_result),
    }
    if not any(item["ok"] for item in evidence.values()):
        reject("网络诊断采集失败：ip 与 ss 均不可用或执行失败。")

    addresses = _safe_json_list(address_result.stdout) if evidence["addresses"]["ok"] else []
    links = _safe_json_list(link_result.stdout) if evidence["links"]["ok"] else []
    routes = _safe_json_list(route_result.stdout) if evidence["routes"]["ok"] else []
    interfaces = _network_interfaces(addresses)
    counters = _network_counters(links)
    findings = []
    for item in interfaces:
        if item["ifname"] != "lo" and item["operstate"] not in {"UP", "UNKNOWN"}:
            findings.append({"kind": "link_not_up", "interface": item["ifname"],
                             "value": item["operstate"]})
    for item in counters:
        error_count = sum(int(item[direction].get(key, 0) or 0)
                          for direction in ("rx", "tx") for key in ("errors", "dropped"))
        if error_count:
            findings.append({"kind": "link_errors_or_drops", "interface": item["ifname"],
                             "value": error_count})

    return _json({
        "schema_version": 1,
        "scope": interface or "all_interfaces",
        "interfaces": interfaces,
        "counters": counters,
        "routes": routes[:100],
        "socket_summary": socket_result.stdout if evidence["sockets"]["ok"] else "",
        "findings": findings,
        "kylin_native_tools_detected": _native_available("network_observability"),
        "evidence": evidence,
        "limitations": [
            "计数器是累计值；没有两个时间点时不能断言错误仍在持续增长。",
            "本工具不主动发包；端到端连通性需在用户给出目标后另行探测。",
        ],
    })


def _device_path(device: str) -> str:
    if not device:
        return ""
    candidate = device if device.startswith("/dev/") else "/dev/" + device
    normalized = posixpath.normpath(candidate)
    segments = normalized.removeprefix("/dev/").split("/")
    if (not _DEVICE_RE.fullmatch(normalized) or not normalized.startswith("/dev/")
            or any(not segment or segment.startswith("-") for segment in segments)):
        reject("参数不合法：device 必须是 /dev 下的块设备路径或设备名")
    return normalized


@mcp.tool()
async def io_diagnosis(device: str = "") -> str:
    """采集块设备拓扑和 I/O 指标；iostat 不可用时降级到 /proc/diskstats。"""
    device_path = _device_path(device)
    lsblk_args = [
        "lsblk", "-J", "-b", "-o",
        "NAME,KNAME,PATH,TYPE,SIZE,FSTYPE,MOUNTPOINTS,RO,MODEL,TRAN",
    ] + ([device_path] if device_path else [])
    iostat_device = posixpath.basename(device_path) if device_path else ""
    iostat_args = ["iostat", "-xz"] + ([iostat_device] if iostat_device else []) + ["1", "1"]
    topology_result, io_result = await asyncio.gather(
        run_command(lsblk_args, timeout=12, max_output=32768),
        run_command(iostat_args, timeout=22, max_output=32768),
    )
    if topology_result.exit_code != 0:
        fallback_args = [
            "lsblk", "-J", "-b", "-o",
            "NAME,KNAME,PATH,TYPE,SIZE,FSTYPE,MOUNTPOINT,RO,MODEL,TRAN",
        ] + ([device_path] if device_path else [])
        topology_result = await run_command(fallback_args, timeout=12, max_output=32768)

    io_source = "iostat"
    io_limitations: list[str] = []
    if io_result.exit_code != 0 or not io_result.stdout.strip():
        io_result = await run_command(["cat", "/proc/diskstats"], timeout=10,
                                      max_output=32768)
        io_source = "proc_diskstats"
        io_limitations.append(
            "/proc/diskstats 是累计计数，单次采样不能计算利用率、延迟或吞吐速率。")
        if io_result.exit_code == 0 and device_path:
            device_name = posixpath.basename(device_path)
            matching = [line for line in io_result.stdout.splitlines()
                        if len(line.split()) >= 3 and line.split()[2] == device_name]
            io_result.stdout = "\n".join(matching)

    if io_result.exit_code != 0 or not io_result.stdout.strip():
        reject("I/O 诊断采集失败：iostat 与 /proc/diskstats 均未提供可用证据。")

    topology: Any = {}
    if topology_result.exit_code == 0:
        try:
            topology = json.loads(topology_result.stdout)
        except ValueError:
            topology = {"raw": topology_result.stdout}

    return _json({
        "schema_version": 1,
        "scope": device_path or "all_block_devices",
        "topology": topology,
        "io_source": io_source,
        "io_evidence": io_result.stdout,
        "kylin_native_tools_detected": _native_available("io_diagnosis"),
        "evidence": {
            "topology": _evidence_status(topology_result),
            "io": _evidence_status(io_result),
        },
        "limitations": io_limitations + [
            "容量不足与 I/O 性能异常是两类问题，不能仅凭磁盘使用率互相替代。",
            "一次 iostat 采样只能描述采样窗口，持续异常需要重复观测。",
        ],
    })


def _binary_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or any(ord(ch) < 32 for ch in path):
        reject("参数不合法：path 必须是无控制字符的绝对路径")
    normalized = posixpath.normpath(path)
    if any(normalized == prefix or normalized.startswith(prefix + "/")
           for prefix in _FORBIDDEN_BINARY_PREFIXES):
        reject("拒绝检查伪文件系统或设备目录中的目标")
    return normalized


def _elf_architecture(machine: str) -> str:
    value = machine.casefold()
    if "loongarch" in value:
        return "loongarch64"
    if "aarch64" in value or "arm64" in value:
        return "aarch64"
    if "x86-64" in value or "x86_64" in value or "advanced micro devices x86" in value:
        return "x86_64"
    return "unknown"


@mcp.tool()
async def binary_compatibility(path: str) -> str:
    """只读检查 ELF 架构、解释器和所属软件包，不使用可能执行目标程序的 ldd。"""
    target = _binary_path(path)
    file_result, header_result, program_result, arch_result = await asyncio.gather(
        run_command(["file", "-Lb", "--", target], timeout=10, max_output=4096),
        run_command(["readelf", "-h", target], timeout=10, max_output=8192),
        run_command(["readelf", "-l", target], timeout=10, max_output=16384),
        run_command(["uname", "-m"], timeout=8, max_output=1024),
    )
    if file_result.exit_code != 0 and header_result.exit_code != 0:
        reject(
            "二进制检查失败，目标不存在、不可读或 file/readelf 不可用。\n"
            + format_exec_result(file_result)
        )

    machine_match = re.search(r"^\s*Machine:\s*(.+)$", header_result.stdout,
                              re.MULTILINE | re.IGNORECASE)
    class_match = re.search(r"^\s*Class:\s*(.+)$", header_result.stdout,
                            re.MULTILINE | re.IGNORECASE)
    data_match = re.search(r"^\s*Data:\s*(.+)$", header_result.stdout,
                           re.MULTILINE | re.IGNORECASE)
    interpreter_match = re.search(
        r"Requesting program interpreter:\s*([^\]]+)\]", program_result.stdout)
    machine = machine_match.group(1).strip() if machine_match else ""
    elf_arch = _elf_architecture(machine)
    host_arch = normalize_architecture(arch_result.stdout)
    is_elf = header_result.exit_code == 0 and bool(machine)
    architecture_match = (
        elf_arch == host_arch if is_elf and elf_arch != "unknown" and host_arch != "unknown"
        else None
    )
    interpreter = interpreter_match.group(1).strip() if interpreter_match else ""

    rpm_result = await run_command(["rpm", "-qf", "--", target], timeout=10,
                                   max_output=4096)
    package_source = "rpm"
    package_result = rpm_result
    if rpm_result.exit_code != 0:
        package_result = await run_command(["dpkg-query", "-S", target], timeout=10,
                                           max_output=4096)
        package_source = "dpkg" if package_result.exit_code == 0 else "unmanaged_or_unknown"

    findings = []
    if not is_elf:
        findings.append({"kind": "not_elf", "meaning": "目标不是可解析的 ELF 文件，可能是脚本、数据文件或已损坏。"})
    elif architecture_match is False:
        findings.append({"kind": "architecture_mismatch",
                         "meaning": f"目标架构 {elf_arch} 与主机架构 {host_arch} 不一致。"})
    elif architecture_match is True:
        findings.append({"kind": "architecture_match",
                         "meaning": f"目标 ELF 架构与主机 {host_arch} 一致。"})
    if interpreter and not Path(interpreter).exists():
        findings.append({"kind": "missing_interpreter",
                         "meaning": f"ELF 请求的解释器 {interpreter} 当前不存在。"})

    return _json({
        "schema_version": 1,
        "path": target,
        "file_description": file_result.stdout.strip(),
        "elf": {
            "is_elf": is_elf,
            "class": class_match.group(1).strip() if class_match else "",
            "data": data_match.group(1).strip() if data_match else "",
            "machine": machine,
            "architecture": elf_arch,
            "interpreter": interpreter,
            "interpreter_exists": Path(interpreter).exists() if interpreter else None,
        },
        "host_architecture": host_arch,
        "architecture_match": architecture_match,
        "package": {
            "source": package_source,
            "managed": package_result.exit_code == 0,
            "owner": package_result.stdout.strip() if package_result.exit_code == 0 else "",
        },
        "findings": findings,
        "evidence": {
            "file": _evidence(file_result),
            "readelf_header": _evidence(header_result),
            "readelf_program_headers": _evidence(program_result),
        },
        "limitations": [
            "为避免执行不可信目标，本工具不调用 ldd；它不能证明所有共享库都能成功解析。",
            "架构匹配只是必要条件，不代表 ABI、内核接口和业务配置一定兼容。",
        ],
    })


if __name__ == "__main__":
    mcp.run()
