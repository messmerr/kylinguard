"""MCP stdio 客户端管理器：拉起插件服务器子进程并复用会话。

标准 MCP 协议——第三方 MCP 工具同样可挂载（未注册工具按最高危门控）。
"""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kylinguard.config import get_settings
from kylinguard.mcp_config import (
    BUILTIN_MCP_SERVER_IDS,
    MCPConfigError,
    MCPConfigStore,
    MCPServerConfig,
    apply_tool_policies,
    make_stdio_server_config,
    normalize_discovered_tools,
    redact_discovered_tool_secrets,
    redact_mcp_error,
)
from kylinguard.models import RiskLevel, ToolMeta
from kylinguard.registry import get_meta
from kylinguard.subprocess_env import agent_subprocess_env, safe_subprocess_env

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

BUILTIN_SERVER_NAMES: dict[str, str] = {
    "sysinfo": "系统状态",
    "services": "服务管理",
    "logs": "日志诊断",
    "network": "网络诊断",
    "disk": "磁盘管理",
    "security": "安全巡检",
    "run_command": "终端命令",
    "files": "文件操作",
}


class ToolCallError(RuntimeError):
    """MCP 工具明确返回失败，供流水线区分成功结果与错误结果。"""


class MCPConnectionError(RuntimeError):
    """已经清理秘密信息、可安全返回控制面的 MCP 连接错误。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


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
    workspace_root: str = "",
    command_shell: str = "/bin/bash",
    command_timeout: int = 30,
    command_max_timeout: int = 900,
    output_max_bytes: int = 65536,
    privileged_helper: str = "",
) -> StdioServerParameters:
    """构造与插件能力匹配的 MCP 进程环境。

    files 插件直接进行 Python 文件 IO，必须整体降权运行；其他插件内部的
    executor 已按每条命令应用 exec_user，不在这里重复 sudo。通用终端需要
    Git/SSH/代理/虚拟环境等用户工具链，因此保留普通环境但剥离 KG_* 控制面；
    其余插件继续使用最小允许列表。
    """
    command = sys.executable
    args = ["-m", module]
    if name == "files" and exec_user:
        command = "sudo"
        args = ["-n", "-H", "-u", exec_user, "--", sys.executable, *args]
    env = (agent_subprocess_env()
           if name == "run_command" else safe_subprocess_env())
    # 任何环境构造器都会剥离 KG_*；这里只把插件运行所需的非秘密配置逐项补回。
    env.update({
        "KG_WORKSPACE_ROOT": workspace_root,
        "KG_COMMAND_SHELL": command_shell,
        "KG_COMMAND_TIMEOUT": str(command_timeout),
        "KG_COMMAND_MAX_TIMEOUT": str(command_max_timeout),
        "KG_OUTPUT_MAX_BYTES": str(output_max_bytes),
        "KG_EXEC_USER": exec_user,
        "KG_PRIVILEGED_HELPER": privileged_helper,
    })
    return StdioServerParameters(
        command=command,
        args=args,
        env=env,
    )


def custom_server_parameters(
    config: MCPServerConfig,
    *,
    exec_user: str = "",
) -> StdioServerParameters:
    """为不可信第三方服务构造最小环境和 argv 启动参数。

    重新构造一次配置是有意的：即使调用方手工实例化 dataclass，仍不能用
    PATH、动态链接器变量或 ``KG_*`` 绕过配置层的约束。命令不会经过 shell。
    """
    checked = make_stdio_server_config(
        server_id=config.id,
        name=config.name,
        command=config.command,
        cwd=config.cwd,
        args=config.args,
        env=config.env,
        secret_env=config.secret_env,
        tool_policies=config.tool_policies,
        enabled=config.enabled,
        version=config.version,
    )
    env = safe_subprocess_env()
    # npx/uvx 等绝对路径启动器仍会在同目录查找 node/python 辅助程序；只加入
    # 管理员已经明确选择的 command 目录，不继承用户会话中的任意 PATH。
    command_dir = str(Path(checked.command).parent)
    env["PATH"] = os.pathsep.join((command_dir, env["PATH"]))
    env.update(checked.process_env())
    command = checked.command
    args = list(checked.args)
    if exec_user:
        # 系统安装使用与控制面分离的 OS 账户。这不是 shell
        # 包装；参数仍作为 argv 传递。环境本身由上方允许列表重建，
        # -E 只用于让目标账户收到该 MCP 显式配置的变量和秘密。
        command = "sudo"
        args = [
            "-n", "-E", "-H", "-u", exec_user, "--",
            checked.command, *checked.args,
        ]
    return StdioServerParameters(
        command=command,
        args=args,
        env=env,
        # 不继承后端工作目录，避免第三方框架自动读取项目根的 .env。
        cwd=checked.cwd,
    )


@dataclass
class _ManagedServer:
    name: str
    session: ClientSession
    tools: list[dict]
    custom: bool
    config_version: int = 0
    tool_risks: dict[str, str] = field(default_factory=dict)
    secret_values: tuple[str, ...] = field(default=(), repr=False)
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    active_calls: int = 0
    idle: asyncio.Event = field(default_factory=asyncio.Event)
    stop_requested: asyncio.Event = field(default_factory=asyncio.Event)
    owner_task: asyncio.Task | None = field(default=None, repr=False)
    retirement_task: asyncio.Task | None = field(default=None, repr=False)
    catalog_stale: bool = False

    def __post_init__(self):
        self.idle.set()

    def invalidate_tool_catalog(self) -> None:
        """使本连接缓存的工具目录、分级和所有既有 identity 立即失效。"""
        if self.catalog_stale:
            return
        self.catalog_stale = True
        self.tool_risks.clear()
        self.instance_id = uuid.uuid4().hex


def _tool_payloads(
    response, secret_values: tuple[str, ...] = (),
) -> list[dict]:
    return redact_discovered_tool_secrets([
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
            "annotations": tool.annotations,
        }
        for tool in response.tools
    ], secret_values)


def _verified_tool_risks(
    tools: list[dict], tool_policies: dict | None,
) -> dict[str, str]:
    """只采纳与本次运行实例实际工具定义摘要匹配的管理员策略。"""
    return {
        tool["name"]: tool["effective_risk"]
        for tool in apply_tool_policies(tools, tool_policies)
        if (isinstance(tool.get("name"), str)
            and tool.get("risk_source") == "administrator")
    }


def _is_tool_catalog_changed(message) -> bool:
    """识别 SDK RootModel 包装后的 tools/list_changed 通知。"""
    notification = getattr(message, "root", message)
    return (getattr(notification, "method", "")
            == "notifications/tools/list_changed")


async def _open_stdio_server(
    name: str,
    params: StdioServerParameters,
    *,
    custom: bool,
    config_version: int = 0,
    tool_policies: dict | None = None,
    secret_values: tuple[str, ...] = (),
    on_tools_changed=None,
    timeout: float = 15.0,
) -> _ManagedServer:
    """在专属任务中拥有 stdio 上下文，允许其他请求任务安全触发关闭。

    AnyIO 的 stdio transport 含 task-local cancel scope，必须由进入它的同一
    task 退出；因此不能把 AsyncExitStack 直接交给热加载请求来关闭。
    """
    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    stop_requested = asyncio.Event()

    async def own_session() -> None:
        stack = AsyncExitStack()
        handle = None
        catalog_changed_during_start = asyncio.Event()

        async def message_handler(message) -> None:
            if not _is_tool_catalog_changed(message):
                # 与 SDK 默认 handler 一样让出调度点；其余通知无需业务处理。
                await asyncio.sleep(0)
                return
            if handle is None:
                catalog_changed_during_start.set()
                return
            handle.invalidate_tool_catalog()
            if on_tools_changed is not None:
                await on_tools_changed(handle)

        try:
            # 第三方进程可能把传入凭据打印到 stderr；控制面不转发该流。
            read, write = await stack.enter_async_context(
                stdio_client(
                    params,
                    errlog=(subprocess.DEVNULL if custom else sys.stderr),
                )
            )
            session = await stack.enter_async_context(ClientSession(
                read, write, message_handler=message_handler,
            ))
            await session.initialize()
            tools = _tool_payloads(
                await session.list_tools(), secret_values,
            )
            if catalog_changed_during_start.is_set():
                raise MCPConnectionError(
                    "MCP 工具目录在发现过程中发生变化，请重新测试。"
                )
            handle = _ManagedServer(
                name=name,
                session=session,
                tools=tools,
                custom=custom,
                config_version=config_version,
                tool_risks=_verified_tool_risks(tools, tool_policies),
                secret_values=secret_values,
                stop_requested=stop_requested,
                owner_task=asyncio.current_task(),
            )
            if not ready.done():
                ready.set_result(handle)
            await stop_requested.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            raise
        finally:
            await stack.aclose()

    owner = asyncio.create_task(own_session(), name=f"mcp-stdio-{name}")

    try:
        return await asyncio.wait_for(asyncio.shield(ready), timeout=timeout)
    except BaseException:
        stop_requested.set()
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
        if ready.done() and not ready.cancelled():
            try:
                ready.exception()
            except BaseException:
                pass
        raise


async def _close_managed_server(
    handle: _ManagedServer,
    *,
    grace: float = 5.0,
) -> None:
    """有界等待在途调用，再回收 MCP 子进程。

    第三方工具可能永不返回；停用、热加载和后端停机不能因此
    无限阻塞。超过 grace 后取消拥有 stdio 上下文的任务，由
    stdio_client 的退出路径终止子进程。
    """
    try:
        await asyncio.wait_for(handle.idle.wait(), timeout=grace)
    except asyncio.TimeoutError:
        if handle.owner_task is not None:
            handle.owner_task.cancel()
    handle.stop_requested.set()
    if handle.owner_task is not None:
        try:
            await asyncio.wait_for(
                asyncio.shield(handle.owner_task), timeout=grace,
            )
        except asyncio.TimeoutError:
            handle.owner_task.cancel()
            await asyncio.gather(handle.owner_task, return_exceptions=True)
        except asyncio.CancelledError:
            # 这里的 CancelledError 可能来自已被强制回收的 owner，
            # 而不是取消当前热加载请求。
            if not handle.owner_task.cancelled():
                raise


async def discover_stdio_tools(
    config: MCPServerConfig,
    *,
    timeout: float = 15.0,
    exec_user: str = "",
) -> list[dict]:
    """临时启动一份配置，完成握手与工具发现后立即关闭。"""
    try:
        handle = await _open_stdio_server(
            config.id,
            custom_server_parameters(config, exec_user=exec_user),
            custom=True,
            config_version=config.version,
            tool_policies=config.tool_policies,
            secret_values=tuple(config.secret_env.values()),
            timeout=timeout,
        )
    except Exception as exc:
        raise MCPConnectionError(
            redact_mcp_error(exc, list(config.secret_env.values()))) from exc
    tools = handle.tools
    try:
        await _close_managed_server(handle)
    except Exception as exc:
        raise MCPConnectionError(
            redact_mcp_error(exc, list(config.secret_env.values()))) from exc
    if handle.catalog_stale:
        raise MCPConnectionError(
            "MCP 工具目录在发现过程中发生变化，请重新测试。"
        )
    return tools


async def test_stdio_server(
    config: MCPServerConfig,
    *,
    timeout: float = 15.0,
    exec_user: str = "",
) -> dict:
    """测试任意已校验 stdio 配置，不读写持久化状态。"""
    started = time.monotonic()
    tools = await discover_stdio_tools(
        config, timeout=timeout, exec_user=exec_user,
    )
    return {
        "ok": True,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "tool_count": len(tools),
        "tools": tools,
    }


async def test_configured_stdio_server(
    store: MCPConfigStore,
    server_id: str,
    *,
    timeout: float = 15.0,
    exec_user: str = "",
    expected_version: int | None = None,
) -> dict:
    """测试存量配置，并以配置版本为条件保存发现结果。"""
    config = store.runtime_config(
        server_id, expected_version=expected_version,
    )
    try:
        result = await test_stdio_server(
            config, timeout=timeout, exec_user=exec_user,
        )
    except MCPConnectionError as exc:
        store.record_test(
            server_id,
            ok=False,
            error=exc.message,
            expected_version=config.version,
        )
        raise
    store.record_test(
        server_id,
        ok=True,
        tools=result["tools"],
        expected_version=config.version,
    )
    return result


class ToolManager:
    def __init__(
        self,
        *,
        exec_user: str | None = None,
        config_store: MCPConfigStore | None = None,
        custom_start_timeout: float = 15.0,
        custom_call_timeout: float = 300.0,
        output_max_bytes: int = 65536,
    ):
        self._sessions: dict[str, ClientSession] = {}
        self._tool_names: set[str] = set()
        self._catalog: str = ""
        self._exec_user = exec_user
        self._config_store = config_store
        self._custom_start_timeout = custom_start_timeout
        self._custom_call_timeout = max(1.0, float(custom_call_timeout))
        self._output_max_bytes = max(1024, int(output_max_bytes))
        self._handles: dict[str, _ManagedServer] = {}
        self._custom_ids: set[str] = set()
        self._catalog_lines: dict[str, list[str]] = {}
        self._state_lock = asyncio.Lock()
        self._reload_lock = asyncio.Lock()
        self._retirement_tasks: set[asyncio.Task] = set()
        self._started = False

    @staticmethod
    def _meta_for_handle(handle: _ManagedServer, tool: dict) -> ToolMeta:
        if handle.catalog_stale:
            return ToolMeta(
                server=handle.name,
                tool=tool["name"],
                risk=RiskLevel.HIGH,
                description="工具目录运行中已变化，必须重新发现并审核",
                custom=handle.custom,
                risk_source="platform_default",
            )
        if not handle.custom:
            meta = get_meta(handle.name, tool["name"])
            if meta.custom:
                # 运行实例明确属于内置服务；漏登记仍按最高风险收敛，但不能
                # 在界面上伪装成第三方 MCP。
                return meta.model_copy(update={
                    "custom": False,
                    "description": "未登记的内置工具，按最高危处理",
                })
            return meta
        configured = handle.tool_risks.get(tool["name"])
        risk = RiskLevel(configured) if configured else RiskLevel.HIGH
        return ToolMeta(
            server=handle.name,
            tool=tool["name"],
            risk=risk,
            description=tool.get("description", ""),
            custom=True,
            risk_source=("administrator" if configured else "platform_default"),
        )

    def _lines_for(self, handle: _ManagedServer) -> list[str]:
        lines: list[str] = []
        for tool in handle.tools:
            meta = self._meta_for_handle(handle, tool)
            qualified = f"{handle.name}.{tool['name']}"
            source = ""
            if handle.custom:
                source = (", custom, admin" if meta.risk_source == "administrator"
                          else ", custom, default")
            lines.append(
                f"- {qualified} [risk={meta.risk.value}{source}"
                f"{', 需提权' if meta.needs_sudo else ''}]: "
                f"{tool['description'] or meta.description}\n"
                f"  参数: {format_input_schema(tool['input_schema'])}"
            )
        return lines

    def _rebuild_catalog(self) -> None:
        order = [*SERVERS]
        order.extend(sorted(name for name in self._catalog_lines
                            if name not in SERVERS))
        lines = [line for name in order
                 for line in self._catalog_lines.get(name, [])]
        self._catalog = "\n".join(lines)
        self._tool_names = {
            f"{name}.{tool['name']}"
            for name, handle in self._handles.items()
            for tool in handle.tools
        }

    async def _install_builtin(self, handle: _ManagedServer) -> None:
        async with self._state_lock:
            if handle.catalog_stale:
                raise MCPConnectionError(
                    "内置 MCP 工具目录在启动过程中发生变化。"
                )
            self._handles[handle.name] = handle
            self._sessions[handle.name] = handle.session
            self._catalog_lines[handle.name] = self._lines_for(handle)
            self._rebuild_catalog()

    async def _retire_safely(self, handle: _ManagedServer) -> None:
        try:
            await _close_managed_server(handle)
        except Exception:
            # 服务已从路由表移除；回收错误不能让旧版本重新可调用。
            pass

    def _schedule_retirement(self, handle: _ManagedServer) -> asyncio.Task:
        if handle.retirement_task is not None:
            return handle.retirement_task
        task = asyncio.create_task(
            self._retire_safely(handle),
            name=f"mcp-retire-{handle.name}",
        )
        self._retirement_tasks.add(task)
        handle.retirement_task = task
        task.add_done_callback(self._retirement_tasks.discard)
        return task

    async def _on_tools_changed(self, handle: _ManagedServer) -> None:
        """收到 MCP list_changed 后立即摘除路由，旧授权与新调用均失效。"""
        handle.invalidate_tool_catalog()
        detached = False
        async with self._state_lock:
            if self._handles.get(handle.name) is handle:
                self._handles.pop(handle.name, None)
                self._sessions.pop(handle.name, None)
                self._catalog_lines.pop(handle.name, None)
                self._custom_ids.discard(handle.name)
                self._rebuild_catalog()
                detached = True
        if detached:
            if handle.custom:
                self._record_runtime_test(
                    handle.name,
                    ok=False,
                    error=("MCP 服务通知工具目录已变化；运行时已摘除，"
                           "请重新测试并审核风险分级。"),
                    expected_version=handle.config_version,
                )
            self._schedule_retirement(handle)

    def _record_runtime_test(self, server_id: str, **kwargs) -> str:
        """尽力保存健康状态；返回非空文本表示状态库写入失败。"""
        if self._config_store is None:
            return ""
        try:
            self._config_store.record_test(server_id, **kwargs)
        except Exception as exc:
            return redact_mcp_error(exc)
        return ""

    async def start(self):
        async with self._reload_lock:
            if self._started:
                return await self._reload_custom_locked(None)
            settings = get_settings()
            exec_user = (settings.exec_user if self._exec_user is None
                         else self._exec_user)
            opened: list[_ManagedServer] = []
            try:
                for name, module in SERVERS.items():
                    params = server_parameters(
                        name,
                        module,
                        exec_user=exec_user,
                        workspace_root=settings.workspace_root,
                        command_shell=settings.command_shell,
                        command_timeout=settings.command_timeout,
                        command_max_timeout=settings.command_max_timeout,
                        output_max_bytes=settings.output_max_bytes,
                        privileged_helper=settings.privileged_helper,
                    )
                    handle = await _open_stdio_server(
                        name, params, custom=False,
                        on_tools_changed=self._on_tools_changed,
                        timeout=30.0)
                    opened.append(handle)
                    await self._install_builtin(handle)
            except BaseException:
                for handle in reversed(opened):
                    try:
                        await _close_managed_server(handle)
                    except BaseException:
                        pass
                async with self._state_lock:
                    self._handles.clear()
                    self._custom_ids.clear()
                    self._sessions.clear()
                    self._catalog_lines.clear()
                    self._rebuild_catalog()
                raise
            self._started = True
            return await self._reload_custom_locked(None)

    async def _configured_servers(self) -> tuple[list[MCPServerConfig], dict[str, str]]:
        if self._config_store is None:
            return [], {}
        configs: list[MCPServerConfig] = []
        errors: dict[str, str] = {}
        for public in self._config_store.list_servers():
            if not public["enabled"]:
                continue
            try:
                configs.append(self._config_store.runtime_config(public["id"]))
            except Exception as exc:
                safe = redact_mcp_error(exc)
                errors[public["id"]] = safe
                self._record_runtime_test(
                    public["id"], ok=False, error=safe,
                    expected_version=public["version"],
                )
        return configs, errors

    async def _reload_custom_locked(
        self, configs: list[MCPServerConfig] | None,
    ) -> dict:
        exec_user = (get_settings().exec_user if self._exec_user is None
                     else self._exec_user)
        if configs is None:
            configs, failures = await self._configured_servers()
        else:
            failures = {}
        enabled = [config for config in configs if config.enabled]
        if len({config.id for config in enabled}) != len(enabled):
            raise MCPConfigError(
                "mcp_server_duplicate", "热加载配置包含重复的 MCP 服务 ID。")

        async with self._state_lock:
            existing = {
                server_id: self._handles.get(server_id)
                for server_id in self._custom_ids
            }

        next_handles: dict[str, _ManagedServer] = {}
        created_handles: list[_ManagedServer] = []
        loaded: list[str] = []
        warnings: dict[str, str] = {}
        stale_next: dict[str, _ManagedServer] = {}
        swapped = False
        try:
            for config in enabled:
                if config.id in BUILTIN_MCP_SERVER_IDS:
                    failures[config.id] = "MCP 服务 ID 与内置服务冲突。"
                    continue
                old = existing.get(config.id)
                if old is not None and old.config_version == config.version:
                    next_handles[config.id] = old
                    loaded.append(config.id)
                    continue
                try:
                    handle = await _open_stdio_server(
                        config.id,
                        custom_server_parameters(
                            config, exec_user=exec_user,
                        ),
                        custom=True,
                        config_version=config.version,
                        tool_policies=config.tool_policies,
                        secret_values=tuple(config.secret_env.values()),
                        on_tools_changed=self._on_tools_changed,
                        timeout=self._custom_start_timeout,
                    )
                except Exception as exc:
                    safe = redact_mcp_error(
                        exc, list(config.secret_env.values()))
                    failures[config.id] = safe
                    warning = self._record_runtime_test(
                        config.id, ok=False, error=safe,
                        expected_version=config.version,
                    )
                    if warning:
                        warnings[config.id] = warning
                    continue
                created_handles.append(handle)
                next_handles[config.id] = handle
                loaded.append(config.id)
                warning = self._record_runtime_test(
                    config.id, ok=True, tools=handle.tools,
                    expected_version=config.version,
                )
                if warning:
                    warnings[config.id] = warning

            async with self._state_lock:
                stale_next = {
                    server_id: handle
                    for server_id, handle in next_handles.items()
                    if handle.catalog_stale
                }
                for server_id in stale_next:
                    next_handles.pop(server_id, None)
                    if server_id in loaded:
                        loaded.remove(server_id)
                    failures[server_id] = (
                        "MCP 工具目录在启动过程中发生变化，请重新测试。"
                    )
                retired = [
                    handle for server_id, handle in existing.items()
                    if (handle is not None
                        and next_handles.get(server_id) is not handle)
                ]
                for server_id in self._custom_ids:
                    self._handles.pop(server_id, None)
                    self._sessions.pop(server_id, None)
                    self._catalog_lines.pop(server_id, None)
                self._custom_ids = set(next_handles)
                for server_id, handle in next_handles.items():
                    self._handles[server_id] = handle
                    self._sessions[server_id] = handle.session
                    self._catalog_lines[server_id] = self._lines_for(handle)
                self._rebuild_catalog()
                swapped = True
        finally:
            if not swapped:
                for handle in created_handles:
                    self._schedule_retirement(handle)

        for server_id, handle in stale_next.items():
            if handle.custom:
                warning = self._record_runtime_test(
                    server_id,
                    ok=False,
                    error=("MCP 工具目录在启动过程中发生变化；"
                           "请重新测试并审核风险分级。"),
                    expected_version=handle.config_version,
                )
                if warning:
                    warnings[server_id] = warning

        retirement_handles = [*retired]
        retirement_handles.extend(
            handle for handle in created_handles
            if (handle.catalog_stale
                and all(handle is not item for item in retirement_handles))
        )
        retirement_tasks = [self._schedule_retirement(handle)
                            for handle in retirement_handles]
        if retirement_tasks:
            # 若发起热加载的 HTTP 请求被取消，shield 让旧进程仍会在在途调用
            # 结束后回收，避免从路由表消失却遗留孤儿进程。
            await asyncio.shield(asyncio.gather(*retirement_tasks))
        return {
            "loaded": loaded,
            "failed": failures,
            "warnings": warnings,
            "disabled": sorted(set(existing) - set(next_handles)),
        }

    async def reload_custom(
        self, configs: list[MCPServerConfig] | None = None,
    ) -> dict:
        """原子替换自定义服务；已在执行的调用完成后才关闭旧进程。"""
        async with self._reload_lock:
            return await self._reload_custom_locked(configs)

    async def detach_custom(self, server_id: str) -> bool:
        """立即从路由与目录摘除一个自定义服务，再在后台安全回收。

        持久化停用与运行时收敛之间必须 fail closed：本方法在返回前已经让
        新调用无法取得 session；已经开始的调用仍可通过 active_calls/idle
        协议自然完成，子进程回收不阻塞控制面请求。
        """
        handle = None
        async with self._reload_lock:
            async with self._state_lock:
                current = self._handles.get(server_id)
                is_custom = (
                    server_id in self._custom_ids
                    or (current is not None and current.custom)
                )
                if not is_custom:
                    return False
                handle = self._handles.pop(server_id, None)
                self._sessions.pop(server_id, None)
                self._catalog_lines.pop(server_id, None)
                self._custom_ids.discard(server_id)
                self._rebuild_catalog()
            if handle is not None:
                self._schedule_retirement(handle)
        return True

    async def stop(self):
        async with self._reload_lock:
            async with self._state_lock:
                handles = list(self._handles.values())
                self._handles.clear()
                self._custom_ids.clear()
                self._sessions.clear()
                self._catalog_lines.clear()
                self._rebuild_catalog()
                self._started = False
            pending = list(self._retirement_tasks)
            pending.extend(
                self._schedule_retirement(handle)
                for handle in reversed(handles)
            )
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    def describe(self) -> str:
        return self._catalog

    async def active_server_summaries(self) -> list[dict]:
        """返回当前已挂载 MCP 服务的只读摘要，供控制面展示。"""
        async with self._state_lock:
            return [
                {
                    "id": name,
                    "source": "custom" if handle.custom else "builtin",
                    "tool_count": len(handle.tools),
                }
                for name, handle in self._handles.items()
            ]

    def has_tool(self, qualified: str) -> bool:
        """工具名必须与启动时 MCP 清单中的限定名完全一致。"""
        return qualified in self._tool_names

    def tool_meta(self, qualified: str) -> ToolMeta:
        """返回当前运行实例绑定的有效风险元数据。"""
        server, tool_name = split_qualified(qualified)
        handle = self._handles.get(server)
        if handle is None:
            return get_meta(server, tool_name)
        tool = next(
            (item for item in handle.tools if item["name"] == tool_name), None,
        )
        return (self._meta_for_handle(handle, tool)
                if tool is not None else get_meta(server, tool_name))

    def tool_security_context(self, qualified: str) -> tuple[ToolMeta, str]:
        """从同一运行实例原子取得风险基线与执行身份。

        本方法不 await，因此事件循环不会在读取 handle、风险与 identity 之间
        切换到热加载任务。之后若实例发生变化，call_checked 的 identity 比对
        会拒绝旧授权。缺失工具返回非空哨兵，避免稍后同名工具出现时绕过比对。
        """
        server, tool_name = split_qualified(qualified)
        handle = self._handles.get(server)
        if handle is None:
            return get_meta(server, tool_name), f"unavailable:{qualified}"
        tool = next(
            (item for item in handle.tools if item["name"] == tool_name), None,
        )
        if tool is None:
            return get_meta(server, tool_name), f"unavailable:{qualified}"
        return (
            self._meta_for_handle(handle, tool),
            self._tool_identity_for(handle, tool_name),
        )

    @staticmethod
    def _tool_identity_for(handle: _ManagedServer, tool: str) -> str:
        metadata = next(
            (item for item in handle.tools if item["name"] == tool), None,
        )
        if metadata is None:
            return ""
        payload = json.dumps({
            "server": handle.name,
            "custom": handle.custom,
            "config_version": handle.config_version,
            "instance_id": handle.instance_id,
            "catalog_stale": handle.catalog_stale,
            "tool": metadata,
            "effective_risk": (
                ToolManager._meta_for_handle(handle, metadata).risk.value
            ),
            "risk_source": (
                ToolManager._meta_for_handle(handle, metadata).risk_source
            ),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def tool_identity(self, qualified: str) -> str:
        """返回规划/授权可绑定的工具实现摘要。"""
        try:
            server, tool = split_qualified(qualified)
        except ValueError:
            return ""
        handle = self._handles.get(server)
        return self._tool_identity_for(handle, tool) if handle else ""

    async def call_checked(
        self,
        server: str,
        tool: str,
        arguments: dict,
        *,
        expected_identity: str = "",
    ) -> str:
        handle = None
        async with self._state_lock:
            session = self._sessions.get(server)
            handle = self._handles.get(server)
            if handle is not None and handle.catalog_stale:
                raise ToolCallError(
                    "MCP 工具目录在运行中已变化，本次未执行；请重新测试并审核。"
                )
            if (expected_identity and (
                    handle is None
                    or self._tool_identity_for(handle, tool)
                    != expected_identity)):
                raise ToolCallError(
                    "MCP 工具配置在规划或确认后已变更，本次未执行。"
                )
            if handle is not None:
                handle.active_calls += 1
                handle.idle.clear()
        if session is None:
            raise ToolCallError(f"未知工具服务器：{server}")
        is_custom = bool(handle is not None and handle.custom)
        retire = False
        schedule_retirement = False
        try:
            request = session.call_tool(tool, arguments)
            if is_custom:
                try:
                    result = await asyncio.wait_for(
                        request, timeout=self._custom_call_timeout,
                    )
                except asyncio.TimeoutError as exc:
                    retire = True
                    self._record_runtime_test(
                        server, ok=False,
                        error=(f"自定义 MCP 工具调用超时"
                               f"（{self._custom_call_timeout:g} 秒）。"),
                        expected_version=handle.config_version,
                    )
                    raise ToolCallError(
                        f"自定义 MCP 工具调用超时"
                        f"（{self._custom_call_timeout:g} 秒），已中止连接。"
                    ) from exc
            else:
                result = await request
            texts = [c.text for c in result.content
                     if getattr(c, "text", None) is not None]
            structured = getattr(result, "structuredContent", None)
            if structured is None:
                structured = getattr(result, "structured_content", None)
            # FastMCP 对普通字符串常同时填充 text 与
            # structuredContent。有文本时保持原语义，只在纯结构化
            # 返回时使用 structuredContent，避免同一结果重复两次。
            if structured is not None and not texts:
                try:
                    texts.append(json.dumps(
                        structured, ensure_ascii=False,
                        separators=(",", ":"), allow_nan=False,
                    ))
                except (TypeError, ValueError, RecursionError):
                    texts.append("(工具返回了无法序列化的 structuredContent)")
            unsupported_count = sum(
                1 for item in result.content
                if getattr(item, "text", None) is None
            )
            if unsupported_count:
                texts.append(
                    f"(工具返回了 {unsupported_count} 项非文本内容，"
                    "当前仅记录类型摘要)"
                )
            output = "\n".join(texts) if texts else "(无文本输出)"
            if is_custom:
                output = redact_mcp_error(
                    output, handle.secret_values, max_chars=None,
                )
                encoded = output.encode("utf-8")
                if len(encoded) > self._output_max_bytes:
                    marker = (
                        f"\n[自定义 MCP 输出已按 "
                        f"{self._output_max_bytes} 字节上限截断]"
                    )
                    budget = max(
                        0, self._output_max_bytes
                        - len(marker.encode("utf-8")),
                    )
                    prefix = encoded[:budget]
                    while prefix:
                        try:
                            output = prefix.decode("utf-8") + marker
                            break
                        except UnicodeDecodeError as exc:
                            prefix = prefix[:exc.start]
                    else:
                        output = marker.lstrip()
            if getattr(result, "isError", False):
                raise ToolCallError(output)
            return output
        except ToolCallError:
            raise
        except Exception as exc:
            safe = (
                redact_mcp_error(exc, handle.secret_values)
                if is_custom else redact_mcp_error(exc)
            )
            if is_custom:
                retire = True
                self._record_runtime_test(
                    server, ok=False, error=safe,
                    expected_version=handle.config_version,
                )
            raise ToolCallError(safe) from exc
        finally:
            if handle is not None:
                async with self._state_lock:
                    handle.active_calls -= 1
                    if handle.active_calls == 0:
                        handle.idle.set()
                    if retire and self._handles.get(server) is handle:
                        self._handles.pop(server, None)
                        self._sessions.pop(server, None)
                        self._catalog_lines.pop(server, None)
                        self._custom_ids.discard(server)
                        self._rebuild_catalog()
                        schedule_retirement = True
                if schedule_retirement:
                    self._schedule_retirement(handle)

    async def call(self, server: str, tool: str, arguments: dict) -> str:
        return await self.call_checked(server, tool, arguments)
