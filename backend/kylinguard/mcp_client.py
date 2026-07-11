"""MCP stdio 客户端管理器：拉起插件服务器子进程并复用会话。

标准 MCP 协议——第三方 MCP 工具同样可挂载（未注册工具按最高危门控）。
"""
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kylinguard.registry import get_meta

SERVERS: dict[str, str] = {
    "sysinfo": "kylinguard.plugins.sysinfo",
    "services": "kylinguard.plugins.services",
    "logs": "kylinguard.plugins.logs",
    "network": "kylinguard.plugins.network",
    "disk": "kylinguard.plugins.disk",
    "security": "kylinguard.plugins.security",
    "run_command": "kylinguard.plugins.run_command",
}


class ToolCallError(RuntimeError):
    """MCP 工具明确返回失败，供流水线区分成功结果与错误结果。"""


def split_qualified(qualified: str) -> tuple[str, str]:
    server, sep, tool = qualified.partition(".")
    if not sep or not server or not tool:
        raise ValueError(f"工具名须为 server.tool 限定名：{qualified!r}")
    return server, tool


class ToolManager:
    def __init__(self):
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._catalog: str = ""

    async def start(self):
        lines: list[str] = []
        for name, module in SERVERS.items():
            params = StdioServerParameters(
                command=sys.executable, args=["-m", module]
            )
            read, write = await self._stack.enter_async_context(
                stdio_client(params)
            )
            session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await session.initialize()
            self._sessions[name] = session
            tools = await session.list_tools()
            for t in tools.tools:
                meta = get_meta(name, t.name)
                schema = t.inputSchema.get("properties", {})
                args = ", ".join(schema.keys()) or "无参数"
                lines.append(
                    f"- {name}.{t.name}({args}) [risk={meta.risk.value}"
                    f"{', 需提权' if meta.needs_sudo else ''}]: "
                    f"{t.description or meta.description}"
                )
        self._catalog = "\n".join(lines)

    async def stop(self):
        await self._stack.aclose()
        self._sessions.clear()

    def describe(self) -> str:
        return self._catalog

    async def call(self, server: str, tool: str, arguments: dict) -> str:
        if server not in self._sessions:
            raise ToolCallError(f"未知工具服务器：{server}")
        result = await self._sessions[server].call_tool(tool, arguments)
        texts = [c.text for c in result.content
                 if getattr(c, "text", None) is not None]
        output = "\n".join(texts) if texts else "(无文本输出)"
        if getattr(result, "isError", False):
            raise ToolCallError(output)
        return output
