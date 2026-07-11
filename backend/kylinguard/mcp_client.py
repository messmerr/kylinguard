"""MCP stdio 客户端管理器：拉起插件服务器子进程并复用会话。

标准 MCP 协议——第三方 MCP 工具同样可挂载（未注册工具按最高危门控）。
"""
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kylinguard.config import get_settings
from kylinguard.registry import get_meta
from kylinguard.subprocess_env import safe_subprocess_env

SERVERS: dict[str, str] = {
    "sysinfo": "kylinguard.plugins.sysinfo",
    "services": "kylinguard.plugins.services",
    "logs": "kylinguard.plugins.logs",
    "network": "kylinguard.plugins.network",
    "disk": "kylinguard.plugins.disk",
    "security": "kylinguard.plugins.security",
    "run_command": "kylinguard.plugins.run_command",
    "files": "kylinguard.plugins.files",
}


class ToolCallError(RuntimeError):
    """MCP 工具明确返回失败，供流水线区分成功结果与错误结果。"""


def split_qualified(qualified: str) -> tuple[str, str]:
    server, sep, tool = qualified.partition(".")
    if not sep or not server or not tool:
        raise ValueError(f"工具名须为 server.tool 限定名：{qualified!r}")
    return server, tool


def server_parameters(
    name: str,
    module: str,
    *,
    exec_user: str = "",
    command_timeout: int = 30,
    output_max_bytes: int = 65536,
    privileged_helper: str = "",
) -> StdioServerParameters:
    """构造最小环境的 MCP 进程参数。

    files 插件直接进行 Python 文件 IO，必须整体降权运行；其他插件内部的
    executor 已按每条命令应用 exec_user，不在这里重复 sudo。
    """
    command = sys.executable
    args = ["-m", module]
    if name == "files" and exec_user:
        command = "sudo"
        args = ["-n", "-H", "-u", exec_user, "--", sys.executable, *args]
    env = safe_subprocess_env()
    # 插件只接收执行所需的非秘密配置。不能传整个 os.environ，否则 LLM
    # Key、管理员口令和代理凭据会重新进入工具进程。
    env.update({
        "KG_COMMAND_TIMEOUT": str(command_timeout),
        "KG_OUTPUT_MAX_BYTES": str(output_max_bytes),
        "KG_EXEC_USER": exec_user,
        "KG_PRIVILEGED_HELPER": privileged_helper,
    })
    return StdioServerParameters(
        command=command,
        args=args,
        env=env,
    )


class ToolManager:
    def __init__(self, *, exec_user: str | None = None):
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._catalog: str = ""
        self._exec_user = exec_user

    async def start(self):
        lines: list[str] = []
        settings = get_settings()
        exec_user = settings.exec_user if self._exec_user is None else self._exec_user
        for name, module in SERVERS.items():
            params = server_parameters(
                name,
                module,
                exec_user=exec_user,
                command_timeout=settings.command_timeout,
                output_max_bytes=settings.output_max_bytes,
                privileged_helper=settings.privileged_helper,
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
