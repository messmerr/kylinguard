"""安全巡检插件（MCP stdio 服务器）：全只读。"""
from mcp.server.fastmcp import FastMCP

from kylinguard.executor import run_command
from kylinguard.plugins._result import (
    format_exec_result,
    reject,
    require_success,
)

mcp = FastMCP("security")

# 关键文件的期望权限（八进制字符串集合 = 可接受值）
_EXPECTED_PERMS = {
    "/etc/passwd": {"644"},
    "/etc/shadow": {"0", "400", "600", "640"},
    "/etc/sudoers": {"440"},
    "/etc/group": {"644"},
    "/etc/ssh/sshd_config": {"600", "644"},
}


@mcp.tool()
async def login_failures(lines: int = 20) -> str:
    """查看近期登录失败记录（只读）。lines 取 1-200。"""
    if not (1 <= lines <= 200):
        reject("参数不合法：lines 取 1-200")
    r = await run_command(
        f"journalctl -g 'Failed password' -n {lines} --no-pager",
        timeout=20, max_output=16384)
    if r.exit_code == 0:
        return r.stdout.strip() or "(近期无登录失败记录)"
    # journalctl 不可用时降级 lastb
    r2 = await run_command(f"lastb -n {lines}", timeout=15, max_output=16384)
    if r2.exit_code == 0:
        return r2.stdout.strip() or "(近期无登录失败记录)"
    reject(
        "登录失败记录采集失败；主命令与降级命令均不可用。\n"
        f"journalctl:\n{format_exec_result(r)}\n"
        f"lastb:\n{format_exec_result(r2)}"
    )


@mcp.tool()
async def sudo_history(lines: int = 20) -> str:
    """查看近期 sudo 提权记录（只读）。lines 取 1-200。"""
    if not (1 <= lines <= 200):
        reject("参数不合法：lines 取 1-200")
    r = await run_command(f"journalctl _COMM=sudo -n {lines} --no-pager",
                          timeout=20, max_output=16384)
    require_success(r, "sudo 记录采集")
    return r.stdout.strip() or "(近期无 sudo 记录)"


@mcp.tool()
async def critical_file_perms() -> str:
    """检查关键系统文件权限是否符合安全基线（只读）。"""
    files = " ".join(_EXPECTED_PERMS)
    r = await run_command(f"stat -c '%a %U %n' {files}",
                          timeout=10, max_output=8192)
    require_success(r, "关键文件权限采集")
    report = []
    for line in r.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        perm, owner, name = parts
        expected = _EXPECTED_PERMS.get(name)
        if expected is None:
            continue
        ok = perm in expected and owner == "root"
        mark = "✓" if ok else "⚠ 偏离基线"
        report.append(f"{mark} {name}: 权限 {perm} 属主 {owner}"
                      f"（期望 {'/'.join(sorted(expected))}，root）")
    return "\n".join(report) if report else "(未取得权限信息)"


if __name__ == "__main__":
    mcp.run()
