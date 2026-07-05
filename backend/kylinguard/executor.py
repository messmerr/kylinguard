"""受限命令执行器：不经 shell（杜绝元字符生效），强制超时与输出截断。

run_as 非空时经 `sudo -n -u <user>` 降权执行（生产环境 kylinguard-exec，
配合 sudoers 精确白名单）；开发环境留空以当前用户执行。

部署要求（调研结论，M3 落地）：审计数据库与策略文件必须对执行账户
kylinguard-exec 不可写——Agent 执行的命令绝不能碰审计链和规则配置。
"""
import asyncio
import shlex
import sys
import time

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from kylinguard.models import ExecResult

_TRUNCATE_MARK = "\n…[输出超限已截断]"


def _decode(data: bytes, max_output: int) -> tuple[str, bool]:
    truncated = len(data) > max_output
    text = data[:max_output].decode("utf-8", errors="replace")
    if truncated:
        text += _TRUNCATE_MARK
    return text, truncated


async def run_command(command: str | list[str], *, timeout: int = 30,
                      max_output: int = 65536, run_as: str = "") -> ExecResult:
    if isinstance(command, list):
        argv = command
    else:
        try:
            argv = shlex.split(command)
        except ValueError as e:
            return ExecResult(exit_code=127, stdout="",
                              stderr=f"命令无法解析：{e}", duration_ms=0)
    if not argv:
        return ExecResult(exit_code=127, stdout="",
                          stderr="空命令", duration_ms=0)
    if run_as:
        argv = ["sudo", "-n", "-u", run_as, "--"] + argv

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError, NotImplementedError) as e:
        return ExecResult(exit_code=127, stdout="",
                          stderr=f"命令无法启动：{e}", duration_ms=0)

    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        ms = int((time.monotonic() - start) * 1000)
        return ExecResult(exit_code=-1, stdout="",
                          stderr=f"执行超时（{timeout}s），进程已强制终止",
                          duration_ms=ms, timed_out=True)

    ms = int((time.monotonic() - start) * 1000)
    stdout, t1 = _decode(out_b, max_output)
    stderr, t2 = _decode(err_b, max_output)
    return ExecResult(exit_code=proc.returncode or 0, stdout=stdout,
                      stderr=stderr, duration_ms=ms, truncated=t1 or t2)
