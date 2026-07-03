"""进程与服务插件（MCP stdio 服务器）。

查询只读；启停/重启为变更操作，元数据声明 needs_sudo，
是否放行由核心三道闸决定——插件本身不做门控，只做参数合法性收敛。
"""
import re

from mcp.server.fastmcp import FastMCP

from kylinguard.config import get_settings
from kylinguard.executor import run_command

mcp = FastMCP("services")

_NAME_RE = re.compile(r"^[\w.@:-]+$")


def _bad_name(name: str) -> bool:
    return not _NAME_RE.fullmatch(name)


async def _systemctl(action: str, name: str, *, sudo: bool) -> str:
    if _bad_name(name):
        return f"服务名不合法：{name!r}（只允许字母数字及 .@:-_）"
    settings = get_settings()
    if sudo and settings.privileged_helper:
        # 生产部署中，真正需要 root 的动作只允许通过 root-owned helper。
        # helper 再做服务名/动作白名单校验，sudoers 只放行这个窄入口。
        r = await run_command(
            f"sudo -n {settings.privileged_helper} service {action} {name}",
            timeout=settings.command_timeout,
        )
    else:
        r = await run_command(
            f"systemctl {action} {name}",
            timeout=settings.command_timeout,
            run_as=settings.exec_user if sudo else "",
        )
    body = r.stdout or r.stderr
    return f"exit_code={r.exit_code}\n{body}"


@mcp.tool()
async def service_status(name: str) -> str:
    """查询指定服务的运行状态（只读）。"""
    if _bad_name(name):
        return f"服务名不合法：{name!r}（只允许字母数字及 .@:-_）"
    r = await run_command(f"systemctl status {name} --no-pager -l",
                          timeout=15, max_output=8192)
    return r.stdout or r.stderr


@mcp.tool()
async def list_failed_services() -> str:
    """列出所有失败状态的服务（只读）。"""
    r = await run_command("systemctl --failed --no-pager --plain", timeout=15)
    return r.stdout or r.stderr


@mcp.tool()
async def start_service(name: str) -> str:
    """启动指定服务（中危，需管理员确认）。"""
    return await _systemctl("start", name, sudo=True)


@mcp.tool()
async def restart_service(name: str) -> str:
    """重启指定服务（中危，需管理员确认）。"""
    return await _systemctl("restart", name, sudo=True)


@mcp.tool()
async def stop_service(name: str) -> str:
    """停止指定服务（高危，需二次确认）。"""
    return await _systemctl("stop", name, sudo=True)


if __name__ == "__main__":
    mcp.run()
