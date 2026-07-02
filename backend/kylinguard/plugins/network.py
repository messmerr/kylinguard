"""网络诊断插件（MCP stdio 服务器）：只读为主，ping 为低危外发探测。"""
import re

from mcp.server.fastmcp import FastMCP

from kylinguard.executor import run_command

mcp = FastMCP("network")

_HOST_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9.-]{0,252}[a-zA-Z0-9])?$")


@mcp.tool()
async def listening_ports() -> str:
    """列出所有监听中的 TCP/UDP 端口及对应进程（只读）。"""
    r = await run_command("ss -tulnp", timeout=10, max_output=16384)
    return r.stdout if r.exit_code == 0 else f"[执行失败] {r.stderr or r.stdout}"


@mcp.tool()
async def connection_stats() -> str:
    """查看连接数统计概览（只读）。"""
    r = await run_command("ss -s", timeout=10, max_output=8192)
    return r.stdout if r.exit_code == 0 else f"[执行失败] {r.stderr or r.stdout}"


@mcp.tool()
async def firewall_status() -> str:
    """查看防火墙当前规则（只读，firewalld）。"""
    r = await run_command("firewall-cmd --list-all", timeout=10,
                          max_output=16384)
    if r.exit_code == 0:
        return r.stdout
    return f"[防火墙状态不可用] {r.stderr or r.stdout}（firewalld 可能未运行）"


@mcp.tool()
async def ping_host(host: str, count: int = 4) -> str:
    """探测到目标主机的连通性。host 为域名或 IP，count 取 1-10。"""
    if not _HOST_RE.fullmatch(host) or not (1 <= count <= 10):
        return "参数不合法：host 须为域名或 IP（不含特殊字符），count 取 1-10"
    r = await run_command(f"ping -c {count} -W 2 {host}", timeout=30,
                          max_output=8192)
    body = r.stdout or r.stderr
    return f"exit_code={r.exit_code}\n{body}"


if __name__ == "__main__":
    mcp.run()
