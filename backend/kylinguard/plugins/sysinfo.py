"""系统观测插件（MCP stdio 服务器）：全只读。"""
from mcp.server.fastmcp import FastMCP

from kylinguard.executor import run_command
from kylinguard.snapshot import collect_snapshot, format_snapshot

mcp = FastMCP("sysinfo")

_SORT_FIELDS = {"cpu": "-%cpu", "mem": "-%mem"}


@mcp.tool()
async def system_snapshot() -> str:
    """采集系统整体快照：负载、内存、磁盘、top 进程、失败服务、近期错误日志。"""
    return format_snapshot(await collect_snapshot())


@mcp.tool()
async def top_processes(sort_by: str = "cpu", limit: int = 10) -> str:
    """列出资源占用最高的进程。sort_by 取 cpu 或 mem，limit 取 1-50。"""
    if sort_by not in _SORT_FIELDS or not (1 <= limit <= 50):
        return "参数不合法：sort_by 只能是 cpu/mem，limit 取 1-50"
    r = await run_command(f"ps aux --sort={_SORT_FIELDS[sort_by]}",
                          timeout=10, max_output=8192)
    if r.exit_code != 0:
        return f"[执行失败] {r.stderr or r.stdout}"
    lines = r.stdout.splitlines()
    return "\n".join(lines[: limit + 1])  # 表头 + limit 行


@mcp.tool()
async def process_tree(limit: int = 80) -> str:
    """查看进程父子关系树。limit 可为 10-300（只读）。"""
    if not (10 <= limit <= 300):
        return "参数不合法：limit 可为 10-300"
    r = await run_command(
        "ps -eo pid,ppid,stat,%cpu,%mem,comm,args --forest",
        timeout=10,
        max_output=32768,
    )
    if r.exit_code != 0:
        return f"[执行失败] {r.stderr or r.stdout}"
    lines = r.stdout.splitlines()
    return "\n".join(lines[: limit + 1])


@mcp.tool()
async def disk_usage() -> str:
    """查看各磁盘分区使用率。"""
    r = await run_command("df -h", timeout=10, max_output=8192)
    return r.stdout if r.exit_code == 0 else f"[执行失败] {r.stderr or r.stdout}"


if __name__ == "__main__":
    mcp.run()  # stdio 传输
