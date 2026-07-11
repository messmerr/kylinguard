"""通用命令工具（MCP stdio 服务器）。

模型可自由拟定完整 shell 命令以覆盖 Git、构建、脚本和长尾运维需求。
核心进程负责风险分类与权限授权，插件只忠实执行已授权能力，并保留超时、
输出上限、stdin 隔离和进程组清理等运行时边界。
"""
import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from kylinguard.config import get_execution_settings
from kylinguard.executor import run_command as run_command_exec
from kylinguard.executor import run_shell as run_shell_exec

mcp = FastMCP("run_command")

_MAX_BATCH_COMMANDS = 16
_MAX_ARGV_ITEMS = 64
_MAX_ARGUMENT_BYTES = 4096
_MAX_COMMAND_BYTES = 32 * 1024


def _validate_command(command: str) -> str:
    if not isinstance(command, str) or not command.strip():
        raise ToolError("command 须为非空字符串。")
    if "\x00" in command:
        raise ToolError("command 不能包含 NUL。")
    size = len(command.encode("utf-8"))
    if size > _MAX_COMMAND_BYTES:
        raise ToolError(f"command 超过 {_MAX_COMMAND_BYTES} 字节上限。")
    return command


def _resolve_cwd(cwd: str | None, workspace_root: str) -> str:
    value = workspace_root if cwd is None else cwd
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ToolError("cwd 须为非空绝对目录路径。")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ToolError("cwd 必须是绝对路径。")
    try:
        resolved = candidate.resolve(strict=True)
        is_directory = resolved.is_dir()
    except FileNotFoundError as exc:
        raise ToolError(f"cwd 不是已存在目录：{candidate}") from exc
    except (OSError, RuntimeError) as exc:
        raise ToolError(f"cwd 无法访问：{candidate}（{exc}）") from exc
    if not is_directory:
        raise ToolError(f"cwd 不是已存在目录：{candidate}")
    return str(resolved)


def _resolve_timeout(timeout: int | None, *, default: int, maximum: int) -> int:
    value = default if timeout is None else timeout
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError("timeout 必须是整数秒。")
    if value < 1 or value > maximum:
        raise ToolError(f"timeout 须为 1-{maximum} 秒。")
    return value


def _execution_options(cwd: str | None, timeout: int | None, settings) -> tuple[str, int]:
    return (
        _resolve_cwd(cwd, settings.workspace_root),
        _resolve_timeout(
            timeout,
            default=settings.command_timeout,
            maximum=settings.command_max_timeout,
        ),
    )


def _result_payload(result) -> str:
    return json.dumps(result.model_dump(), ensure_ascii=False)


def _validate_batch(commands: list[list[str]], operators: list[str]) -> None:
    if not commands or len(commands) > _MAX_BATCH_COMMANDS:
        raise ToolError(
            f"commands 须包含 1-{_MAX_BATCH_COMMANDS} 条 argv 命令。"
        )
    for index, argv in enumerate(commands):
        if not isinstance(argv, list) or not 1 <= len(argv) <= _MAX_ARGV_ITEMS:
            raise ToolError(
                f"commands[{index}] 须包含 1-{_MAX_ARGV_ITEMS} 个 argv 字符串。"
            )
        total = 0
        for argument_index, argument in enumerate(argv):
            if (not isinstance(argument, str) or "\x00" in argument
                    or (argument_index == 0 and not argument)):
                raise ToolError(
                    f"commands[{index}] 的可执行文件为空，或参数含非字符串/NUL。"
                )
            size = len(argument.encode("utf-8"))
            if size > _MAX_ARGUMENT_BYTES:
                raise ToolError(
                    f"commands[{index}] 单个参数超过 {_MAX_ARGUMENT_BYTES} 字节。"
                )
            total += size
        if total > _MAX_COMMAND_BYTES:
            raise ToolError(
                f"commands[{index}] 总长度超过 {_MAX_COMMAND_BYTES} 字节。"
            )
    if operators:
        if len(operators) != len(commands) - 1:
            raise ToolError("operators 数量必须恰好比 commands 少 1。")
        unsupported = [op for op in operators if op not in {";", "&&", "||"}]
        if unsupported:
            raise ToolError(
                f"operators 仅支持 ';'、'&&'、'||'，收到：{unsupported!r}"
            )


@mcp.tool()
async def run_command(
    command: str,
    cwd: str | None = None,
    timeout: int | None = None,
) -> str:
    """在 Bash 中执行完整 shell 命令；支持管道、重定向、变量和命令串联。"""
    settings = get_execution_settings()
    command = _validate_command(command)
    resolved_cwd, resolved_timeout = _execution_options(cwd, timeout, settings)
    result = await run_shell_exec(
        command,
        shell=settings.command_shell,
        cwd=resolved_cwd,
        timeout=resolved_timeout,
        max_output=settings.output_max_bytes,
        run_as=settings.exec_user,
    )
    # 非零退出与超时都是命令的正常可观察结果；仅参数错误使用 MCP ToolError。
    return _result_payload(result)


@mcp.tool()
async def run_batch(
    commands: list[list[str]],
    operators: list[str] | None = None,
    stop_on_error: bool = True,
    cwd: str | None = None,
    timeout: int | None = None,
) -> str:
    """按 argv 在固定系统环境执行批处理；支持 ;/&&/||，但不启动 shell。"""
    operators = operators or []
    _validate_batch(commands, operators)
    settings = get_execution_settings()
    resolved_cwd, resolved_timeout = _execution_options(cwd, timeout, settings)
    results = []
    failed = False
    executed = 0
    last_ok: bool | None = None
    for index, argv in enumerate(commands):
        should_execute = True
        operator = operators[index - 1] if index and operators else None
        if operator == "&&":
            should_execute = last_ok is True
        elif operator == "||":
            should_execute = last_ok is False
        # 跳过短路分支时保留上一条实际执行结果，符合 shell AND-OR 列表
        # 的左结合状态传播，但没有任何 shell 展开或解释行为。
        if not should_execute:
            results.append({
                "index": index,
                "executable": argv[0],
                "executed": False,
                "operator": operator,
                "skip_reason": (
                    "previous_succeeded" if operator == "||"
                    else "previous_failed"
                ),
            })
            continue
        result = await run_command_exec(
            argv,
            cwd=resolved_cwd,
            timeout=resolved_timeout,
            max_output=settings.output_max_bytes,
            run_as=settings.exec_user,
            # run_batch 是无 shell 的结构化执行通道，也承载 READ_ONLY 的
            # 自动执行；必须固定系统 PATH 并剥离 LD_PRELOAD 等加载器变量。
            # 需要完整用户工具链环境时使用 run_command 的完整 shell。
            agent_environment=False,
        )
        executed += 1
        ok = not result.timed_out and result.exit_code == 0
        last_ok = ok
        results.append({
            "index": index,
            # 只回显可执行文件名，不复制可能包含口令/令牌的参数。
            "executable": argv[0],
            "executed": True,
            "operator": operator,
            "ok": ok,
            **result.model_dump(),
        })
        if not ok:
            failed = True
            if not operators and stop_on_error:
                break

    batch_ok = (last_ok is True) if operators else not failed
    payload = json.dumps({
        "ok": batch_ok,
        "commands_requested": len(commands),
        "commands_executed": executed,
        # skipped 表示所有未执行项；其中 short_circuited 是 &&/|| 短路，
        # omitted_after_stop 是无 operators 且 stop_on_error 后尚未访问的尾部。
        "commands_skipped": len(commands) - executed,
        "commands_short_circuited": len(results) - executed,
        "commands_omitted_after_stop": len(commands) - len(results),
        "stopped_early": len(results) < len(commands),
        "results": results,
    }, ensure_ascii=False)
    # 非零退出是命令结果而非 MCP 协议错误。显式 operators 使用 AND-OR
    # 列表最终状态；无 operators 时任一失败令 ok=false。
    return payload


if __name__ == "__main__":
    mcp.run()
