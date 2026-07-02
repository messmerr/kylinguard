"""日志分析插件（MCP stdio 服务器）：全只读，路径限定 /var/log。"""
import posixpath
import re

from mcp.server.fastmcp import FastMCP

from kylinguard.executor import run_command

mcp = FastMCP("logs")

_PRIORITIES = {"", "emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"}
_UNIT_RE = re.compile(r"^[\w.@:-]*$")


@mcp.tool()
async def journal_search(unit: str = "", priority: str = "",
                         lines: int = 100) -> str:
    """检索 systemd 日志。unit 为服务名（可空），priority 为 err/warning 等级别（可空），lines 取 1-500。"""
    if priority not in _PRIORITIES or not (1 <= lines <= 500) \
            or not _UNIT_RE.fullmatch(unit):
        return "参数不合法：priority 须为 systemd 级别，lines 取 1-500，unit 为服务名"
    cmd = f"journalctl -n {lines} --no-pager"
    if unit:
        cmd += f" -u {unit}"
    if priority:
        cmd += f" -p {priority}"
    r = await run_command(cmd, timeout=20, max_output=16384)
    return r.stdout if r.exit_code == 0 else f"[执行失败] {r.stderr or r.stdout}"


@mcp.tool()
async def tail_file(path: str, lines: int = 100) -> str:
    """查看日志文件尾部内容。仅允许 /var/log 下的文件，lines 取 1-500。"""
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/var/log/") or not (1 <= lines <= 500):
        return "仅允许查看 /var/log/ 下的日志文件，lines 取 1-500"
    r = await run_command(f"tail -n {lines} {normalized}",
                          timeout=15, max_output=16384)
    return r.stdout if r.exit_code == 0 else f"[执行失败] {r.stderr or r.stdout}"


if __name__ == "__main__":
    mcp.run()
