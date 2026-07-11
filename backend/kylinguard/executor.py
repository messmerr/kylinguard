"""受限命令执行器：不经 shell（杜绝元字符生效），强制超时与输出截断。

run_as 非空时经 `sudo -n -u <user>` 降权执行（生产环境 kylinguard-exec，
配合 sudoers 精确白名单）；开发环境留空以当前用户执行。

部署要求（调研结论，M3 落地）：审计数据库与策略文件必须对执行账户
kylinguard-exec 不可写——Agent 执行的命令绝不能碰审计链和规则配置。
"""
import asyncio
import os
import shlex
import signal
import sys
import time

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from kylinguard.models import ExecResult
from kylinguard.subprocess_env import safe_subprocess_env

_TRUNCATE_MARK = "\n…[输出超限已截断]"
_READ_CHUNK_SIZE = 64 * 1024


def _decode(
    data: bytes,
    max_output: int,
    *,
    truncated: bool = False,
) -> tuple[str, bool]:
    limit = max(0, max_output)
    truncated = truncated or len(data) > limit
    text = data[:limit].decode("utf-8", errors="replace")
    if truncated:
        text += _TRUNCATE_MARK
    return text, truncated


async def _read_bounded(
    stream: asyncio.StreamReader,
    max_output: int,
) -> tuple[bytes, bool]:
    """持续排空一个管道，但只在内存中保留固定上限的输出。"""
    limit = max(0, max_output)
    kept = bytearray()
    truncated = False

    while True:
        chunk = await stream.read(_READ_CHUNK_SIZE)
        if not chunk:
            break

        remaining = limit - len(kept)
        if remaining > 0:
            kept.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            truncated = True

    return bytes(kept), truncated


async def _wait_and_collect(
    proc: asyncio.subprocess.Process,
    stdout_task: asyncio.Task[tuple[bytes, bool]],
    stderr_task: asyncio.Task[tuple[bytes, bool]],
) -> tuple[tuple[bytes, bool], tuple[bytes, bool]]:
    await proc.wait()
    # shield 保证外层超时或取消时，两个读取器仍可在进程组被终止后排空管道。
    stdout_result, stderr_result = await asyncio.gather(
        asyncio.shield(stdout_task),
        asyncio.shield(stderr_task),
    )
    return stdout_result, stderr_result


async def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """终止命令及其未脱离 session 的所有后代，并回收直接子进程。"""
    if os.name == "posix":
        # 即使组长已经退出，继承其 PGID 的后代仍可能存活，因此始终尝试 killpg。
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # 异常部署下至少保留原有的直接子进程清理能力。
            if proc.returncode is None:
                proc.kill()
    elif proc.returncode is None:
        proc.kill()

    await proc.wait()


async def _settle_readers(*tasks: asyncio.Task[tuple[bytes, bool]]) -> None:
    # 脱离 session 的异常后代仍可能持有管道；强制清理不能因此无限等待 EOF。
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


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
        spawn_options = {"start_new_session": True} if os.name == "posix" else {}
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_subprocess_env(),
            **spawn_options,
        )
    except (FileNotFoundError, OSError, NotImplementedError) as e:
        return ExecResult(exit_code=127, stdout="",
                          stderr=f"命令无法启动：{e}", duration_ms=0)

    assert proc.stdout is not None
    assert proc.stderr is not None
    stdout_task = asyncio.create_task(_read_bounded(proc.stdout, max_output))
    stderr_task = asyncio.create_task(_read_bounded(proc.stderr, max_output))

    try:
        (out_b, out_truncated), (err_b, err_truncated) = await asyncio.wait_for(
            _wait_and_collect(proc, stdout_task, stderr_task),
            timeout,
        )
    except asyncio.CancelledError:
        # 取消上层流水线时不能只停止等待：否则系统命令会在后台继续运行，
        # 同时再也没有机会写入 execution 审计事件。
        await _kill_process_group(proc)
        await _settle_readers(stdout_task, stderr_task)
        raise
    except asyncio.TimeoutError:
        await _kill_process_group(proc)
        await _settle_readers(stdout_task, stderr_task)
        ms = int((time.monotonic() - start) * 1000)
        return ExecResult(exit_code=-1, stdout="",
                          stderr=f"执行超时（{timeout}s），进程已强制终止",
                          duration_ms=ms, timed_out=True)

    ms = int((time.monotonic() - start) * 1000)
    stdout, t1 = _decode(out_b, max_output, truncated=out_truncated)
    stderr, t2 = _decode(err_b, max_output, truncated=err_truncated)
    return ExecResult(exit_code=proc.returncode or 0, stdout=stdout,
                      stderr=stderr, duration_ms=ms, truncated=t1 or t2)
