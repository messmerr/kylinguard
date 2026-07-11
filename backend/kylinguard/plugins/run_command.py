"""通用命令工具（MCP stdio 服务器）。

模型可自由拟定命令以覆盖长尾运维需求；安全性由核心三道闸保证
（规则引擎在核心进程内先行判定，插件进程只负责受限执行）。
"""
from mcp.server.fastmcp import FastMCP

from kylinguard.config import get_settings
from kylinguard.executor import run_command as run_command_exec
from kylinguard.plugins._result import format_exec_result, require_success

mcp = FastMCP("run_command")


@mcp.tool()
async def run_command(command: str) -> str:
    """执行一条 shell 命令（不经 shell 解释，不支持管道/重定向/命令串联）。"""
    settings = get_settings()
    r = await run_command_exec(
        command,
        timeout=settings.command_timeout,
        max_output=settings.output_max_bytes,
        run_as=settings.exec_user,
    )
    require_success(r, "自由命令执行")
    return format_exec_result(r)


if __name__ == "__main__":
    mcp.run()
