"""磁盘清理插件（MCP stdio 服务器）：扫描只读，清理为高危变更。"""
import posixpath

from mcp.server.fastmcp import FastMCP

from kylinguard.config import get_settings
from kylinguard.executor import run_command
from kylinguard.plugins._result import (
    format_exec_result,
    reject,
    require_success,
)

mcp = FastMCP("disk")

# 扫描起点须为绝对路径，且不允许伪文件系统
_SCAN_FORBIDDEN = ("/proc", "/sys", "/dev", "/run")
# 清理白名单：仅允许删除这些目录下的普通文件（安全模型的一部分，
# 即便三道闸放行，插件自身也不越界）
_CLEAN_PREFIXES = ("/tmp/", "/var/tmp/", "/var/cache/", "/var/log/")


def _bad_scan_path(path: str) -> bool:
    p = posixpath.normpath(path)
    return not p.startswith("/") or any(
        p == f or p.startswith(f + "/") for f in _SCAN_FORBIDDEN)


@mcp.tool()
async def disk_hotspots(path: str = "/", depth: int = 2) -> str:
    """按目录汇总磁盘占用，找出空间热点。path 为绝对路径，depth 取 1-4（只读）。"""
    if _bad_scan_path(path) or not (1 <= depth <= 4):
        reject("参数不合法：path 须为绝对路径（不含 /proc、/sys 等），depth 取 1-4")
    p = posixpath.normpath(path)
    r = await run_command(f"du -x -d {depth} {p}", timeout=60,
                          max_output=65536)
    require_success(r, "磁盘热点扫描")
    lines = []
    for line in r.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0].isdigit():
            lines.append((int(parts[0]), parts[1]))
    lines.sort(reverse=True)
    top = [f"{kb / 1024:8.1f} MB  {name}" for kb, name in lines[:15]]
    return "占用最高的目录（前 15）：\n" + "\n".join(top) if top else "(无数据)"


@mcp.tool()
async def io_stats() -> str:
    """采集磁盘 I/O 统计。优先使用 iostat，不可用时降级读取 /proc/diskstats（只读）。"""
    r = await run_command("iostat -xz 1 1", timeout=20, max_output=32768)
    if r.exit_code == 0 and r.stdout.strip():
        return r.stdout
    fallback = await run_command("cat /proc/diskstats", timeout=10,
                                 max_output=32768)
    if fallback.exit_code == 0 and fallback.stdout.strip():
        lines = fallback.stdout.splitlines()[:40]
        return "[iostat 不可用，已降级为 /proc/diskstats 前 40 行]\n" + "\n".join(lines)
    reject(
        "磁盘 I/O 统计采集失败；主命令与降级命令均不可用。\n"
        f"iostat:\n{format_exec_result(r)}\n"
        f"/proc/diskstats:\n{format_exec_result(fallback)}"
    )


@mcp.tool()
async def large_files(path: str = "/var", min_mb: int = 100) -> str:
    """列出指定目录下超过 min_mb 的大文件。min_mb 取 10-10240（只读）。"""
    if _bad_scan_path(path) or not (10 <= min_mb <= 10240):
        reject("参数不合法：path 须为绝对路径，min_mb 取 10-10240")
    p = posixpath.normpath(path)
    r = await run_command(f"find {p} -xdev -type f -size +{min_mb}M",
                          timeout=60, max_output=32768)
    require_success(r, "大文件扫描")
    body = r.stdout.strip()
    return body or f"{p} 下没有超过 {min_mb}MB 的文件"


@mcp.tool()
async def clean_file(path: str) -> str:
    """删除指定文件释放空间（高危，需管理员二次确认）。
    仅允许清理 /tmp、/var/tmp、/var/cache、/var/log 下的文件。"""
    p = posixpath.normpath(path)
    if not p.startswith(_CLEAN_PREFIXES):
        reject("拒绝：仅允许清理 /tmp、/var/tmp、/var/cache、/var/log "
               f"下的文件，{p!r} 不在白名单内")
    settings = get_settings()
    if settings.privileged_helper:
        r = await run_command(
            f"sudo -n {settings.privileged_helper} clean-file {p}",
            timeout=15,
        )
    else:
        r = await run_command(f"rm -f {p}", timeout=15,
                              run_as=settings.exec_user)
    require_success(r, f"删除文件 {p}")
    return f"已删除 {p}"


if __name__ == "__main__":
    mcp.run()
