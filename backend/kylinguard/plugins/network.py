"""网络诊断插件（MCP stdio 服务器）：只读为主，ping 为低危外发探测。"""
import re

from mcp.server.fastmcp import FastMCP

from kylinguard.executor import run_command
from kylinguard.plugins._result import (
    format_exec_result,
    reject,
    require_success,
)

mcp = FastMCP("network")

_HOST_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9.-]{0,252}[a-zA-Z0-9])?$")


@mcp.tool()
async def listening_ports() -> str:
    """列出所有监听中的 TCP/UDP 端口及对应进程（只读）。"""
    r = await run_command("ss -tulnp", timeout=10, max_output=16384)
    return require_success(r, "监听端口采集")


@mcp.tool()
async def connection_stats() -> str:
    """查看连接数统计概览（只读）。"""
    r = await run_command("ss -s", timeout=10, max_output=8192)
    return require_success(r, "网络连接统计")


@mcp.tool()
async def lsof_listening() -> str:
    """用 lsof 列出监听 TCP 端口及进程；lsof 不可用时降级为 ss（只读）。"""
    r = await run_command("lsof -nP -iTCP -sTCP:LISTEN",
                          timeout=10, max_output=16384)
    if r.exit_code == 0 and r.stdout.strip():
        return r.stdout
    fallback = await run_command("ss -tulnp", timeout=10, max_output=16384)
    if fallback.exit_code == 0:
        return "[lsof 不可用，已降级为 ss]\n" + fallback.stdout
    reject(
        "监听端口采集失败；主命令与降级命令均不可用。\n"
        f"lsof:\n{format_exec_result(r)}\n"
        f"ss:\n{format_exec_result(fallback)}"
    )


@mcp.tool()
async def firewall_status() -> str:
    """查看防火墙当前规则（只读，firewalld）。"""
    r = await run_command("firewall-cmd --list-all", timeout=10,
                          max_output=16384)
    return require_success(r, "防火墙状态采集")


@mcp.tool()
async def ping_host(host: str, count: int = 4) -> str:
    """探测到目标主机的连通性。host 为域名或 IP，count 取 1-10。"""
    if not _HOST_RE.fullmatch(host) or not (1 <= count <= 10):
        reject("参数不合法：host 须为域名或 IP（不含特殊字符），count 取 1-10")
    r = await run_command(f"ping -c {count} -W 2 {host}", timeout=30,
                          max_output=8192)
    return require_success(r, f"主机 {host} 连通性探测")


if __name__ == "__main__":
    mcp.run()
