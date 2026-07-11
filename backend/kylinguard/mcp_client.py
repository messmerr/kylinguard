"""MCP stdio 客户端管理器：拉起插件服务器子进程并复用会话。

标准 MCP 协议——第三方 MCP 工具同样可挂载（未注册工具按最高危门控）。
"""
import json
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


def _json_literal(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _schema_type(schema: dict) -> tuple[str, bool]:
    """把常见 JSON Schema 类型压缩成适合放进提示词的一行文本。"""
    nullable = bool(schema.get("nullable", False))
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        nullable = nullable or "null" in raw_type
        types = [value for value in raw_type if value != "null"]
    elif isinstance(raw_type, str):
        nullable = nullable or raw_type == "null"
        types = [] if raw_type == "null" else [raw_type]
    else:
        types = []

    variants = schema.get("anyOf") or schema.get("oneOf") or []
    if variants:
        types = []
        for variant in variants:
            variant_type, variant_nullable = _schema_type(variant)
            nullable = nullable or variant_nullable
            if variant_type != "null" and variant_type not in types:
                types.append(variant_type)

    if not types:
        if "enum" in schema:
            inferred = {
                "boolean" if isinstance(value, bool)
                else "integer" if isinstance(value, int)
                else "number" if isinstance(value, float)
                else "string" if isinstance(value, str)
                else "null" if value is None
                else "object" if isinstance(value, dict)
                else "array" if isinstance(value, list)
                else "unknown"
                for value in schema["enum"]
            }
            nullable = nullable or "null" in inferred
            types = sorted(inferred - {"null"})
        elif nullable:
            return "null", True
        else:
            types = ["any"]

    rendered: list[str] = []
    for value in types:
        if value == "array":
            item_type, item_nullable = _schema_type(schema.get("items", {}))
            if item_nullable:
                item_type += "|null"
            rendered.append(f"array<{item_type}>")
        else:
            rendered.append(value)
    return " | ".join(rendered), nullable


def format_input_schema(schema: dict) -> str:
    """完整但紧凑地呈现工具参数契约，供规划模型直接遵循。"""
    properties = schema.get("properties", {})
    if not properties:
        return "无参数"

    required = set(schema.get("required", []))
    constraint_labels = (
        ("enum", "可选值"),
        ("const", "固定值"),
        ("minimum", "最小值"),
        ("exclusiveMinimum", "严格大于"),
        ("maximum", "最大值"),
        ("exclusiveMaximum", "严格小于"),
        ("multipleOf", "倍数"),
        ("minLength", "最短长度"),
        ("maxLength", "最长长度"),
        ("pattern", "格式正则"),
        ("minItems", "最少项数"),
        ("maxItems", "最多项数"),
        ("uniqueItems", "元素唯一"),
        ("format", "格式"),
    )
    arguments: list[str] = []
    for name, property_schema in properties.items():
        type_name, nullable = _schema_type(property_schema)
        attributes = [
            "必填" if name in required else "可省略",
            "可为 null" if nullable else "不可为 null",
        ]
        if "default" in property_schema:
            attributes.append(f"默认={_json_literal(property_schema['default'])}")
        for key, label in constraint_labels:
            if key in property_schema:
                attributes.append(
                    f"{label}={_json_literal(property_schema[key])}"
                )
        rendered = f"{name}: {type_name} ({', '.join(attributes)})"
        description = " ".join(
            str(property_schema.get("description", "")).split()
        )
        if description:
            rendered += f" — {description}"
        arguments.append(rendered)
    return "; ".join(arguments)


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
        self._tool_names: set[str] = set()
        self._catalog: str = ""
        self._exec_user = exec_user

    async def start(self):
        lines: list[str] = []
        self._tool_names.clear()
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
                qualified = f"{name}.{t.name}"
                self._tool_names.add(qualified)
                lines.append(
                    f"- {qualified} [risk={meta.risk.value}"
                    f"{', 需提权' if meta.needs_sudo else ''}]: "
                    f"{t.description or meta.description}\n"
                    f"  参数: {format_input_schema(t.inputSchema)}"
                )
        self._catalog = "\n".join(lines)

    async def stop(self):
        await self._stack.aclose()
        self._sessions.clear()
        self._tool_names.clear()

    def describe(self) -> str:
        return self._catalog

    def has_tool(self, qualified: str) -> bool:
        """工具名必须与启动时 MCP 清单中的限定名完全一致。"""
        return qualified in self._tool_names

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
