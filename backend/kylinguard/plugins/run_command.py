"""通用命令工具（MCP stdio 服务器）。

模型可自由拟定命令以覆盖长尾运维需求；安全性由核心三道闸保证
（规则引擎在核心进程内先行判定，插件进程只负责受限执行）。
"""
import json

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from kylinguard.config import get_execution_settings
from kylinguard.executor import run_command as run_command_exec
from kylinguard.plugins._result import format_exec_result, require_success

mcp = FastMCP("run_command")

_MAX_BATCH_COMMANDS = 16
_MAX_ARGV_ITEMS = 64
_MAX_ARGUMENT_BYTES = 4096
_MAX_COMMAND_BYTES = 32 * 1024


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
        for argument in argv:
            if not isinstance(argument, str) or not argument or "\x00" in argument:
                raise ToolError(
                    f"commands[{index}] 含空参数、非字符串或 NUL，拒绝执行。"
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
async def run_command(command: str) -> str:
    """执行一条 shell 命令（不经 shell 解释，不支持管道/重定向/命令串联）。"""
    settings = get_execution_settings()
    r = await run_command_exec(
        command,
        timeout=settings.command_timeout,
        max_output=settings.output_max_bytes,
        run_as=settings.exec_user,
    )
    require_success(r, "自由命令执行")
    return format_exec_result(r)


@mcp.tool()
async def run_batch(
    commands: list[list[str]],
    operators: list[str] | None = None,
    stop_on_error: bool = True,
) -> str:
    """按 argv 执行批处理；可表达 ;/&&/||，但始终不启动 shell。"""
    operators = operators or []
    _validate_batch(commands, operators)
    settings = get_execution_settings()
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
            timeout=settings.command_timeout,
            max_output=settings.output_max_bytes,
            run_as=settings.exec_user,
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

    payload = json.dumps({
        "commands_requested": len(commands),
        "commands_executed": executed,
        "commands_skipped": len(results) - executed,
        "stopped_early": len(results) < len(commands),
        "results": results,
    }, ensure_ascii=False)
    # 显式 operators 使用 AND-OR 列表的最终状态；例如 `test -f x || ls`
    # 中第一次失败可被 fallback 成功恢复。无 operators 时沿用批内任一失败
    # 即工具失败的保守语义。
    if (operators and last_ok is not True) or (not operators and failed):
        raise ToolError(f"批量命令执行失败。\n{payload}")
    return payload


if __name__ == "__main__":
    mcp.run()
