"""MCP 插件统一失败语义。

插件成功时返回普通文本；参数拒绝、策略拒绝、命令非零退出和超时则抛出
FastMCP ``ToolError``。FastMCP 会把它编码为 ``CallToolResult.isError``，
核心进程因此无需解析任何中文前缀即可区分成功与失败。
"""
from collections.abc import Collection

from mcp.server.fastmcp.exceptions import ToolError

from kylinguard.models import ExecResult


def reject(message: str) -> None:
    """以可安全展示的说明拒绝本次工具调用。"""
    raise ToolError(message)


def format_exec_result(result: ExecResult) -> str:
    """把执行结果格式化为稳定、可审计的文本。"""
    parts = [f"exit_code={result.exit_code}"]
    if result.timed_out:
        parts.append("执行超时，子进程已终止。")
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    return "\n".join(parts)


def require_success(
    result: ExecResult,
    action: str,
    *,
    ok_codes: Collection[int] = (0,),
) -> str:
    """校验命令成功并返回正文；失败统一转换为 MCP ToolError。"""
    if result.timed_out or result.exit_code not in ok_codes:
        raise ToolError(f"{action}失败。\n{format_exec_result(result)}")
    return result.stdout or result.stderr or "(无输出)"
