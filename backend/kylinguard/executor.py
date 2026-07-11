"""命令执行器：同时提供确定性的 argv 执行和显式 shell 执行。

``run_command`` 保留不经 shell 的 argv 语义，供结构化插件使用；
``run_shell`` 则通过配置的 shell 执行完整脚本，供通用 Agent 终端使用。
两条路径共享进程组隔离、stdin 隔离、超时和有界输出收集。

run_as 非空时经 `sudo -n -H -u <user>` 降权执行（生产环境 kylinguard-exec，
配合 sudoers 精确白名单）；开发环境留空以当前用户执行。

生产部署建议让审计数据库、密钥和策略文件对独立执行账户不可读写；本地 WSL
若选择与后端共用当前身份，界面会明确显示这不是 OS 账户级隔离。
"""
import asyncio
import os
import shlex
import signal
import sys
import time
from dataclasses import dataclass, field

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from kylinguard.models import ExecResult
from kylinguard.subprocess_env import agent_subprocess_env, safe_subprocess_env

_TRUNCATE_MARK = "\n…[输出超限已截断]"
_READ_CHUNK_SIZE = 64 * 1024


@dataclass
class _BoundedCapture:
    """在读取任务被取消时仍可取回已经收集的部分输出。"""

    limit: int
    kept: bytearray = field(default_factory=bytearray)
    truncated: bool = False

    def append(self, chunk: bytes) -> None:
        remaining = self.limit - len(self.kept)
        if remaining > 0:
            self.kept.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            self.truncated = True

    def result(self) -> tuple[bytes, bool]:
        return bytes(self.kept), self.truncated


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
    *,
    capture: _BoundedCapture | None = None,
) -> tuple[bytes, bool]:
    """持续排空一个管道，但只在内存中保留固定上限的输出。"""
    capture = capture or _BoundedCapture(max(0, max_output))

    while True:
        chunk = await stream.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        capture.append(chunk)

    return capture.result()


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


async def _settle_readers(
    *tasks: asyncio.Task[tuple[bytes, bool]],
    grace_seconds: float = 1.0,
) -> None:
    """给已终止进程的管道一次排空机会，异常后代不能令清理无限等待。"""
    _, pending = await asyncio.wait(tasks, timeout=grace_seconds)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _run_argv(
    argv: list[str],
    *,
    timeout: int = 30,
    max_output: int = 65536,
    run_as: str = "",
    cwd: str | None = None,
    agent_environment: bool = False,
) -> ExecResult:
    if run_as:
        argv = ["sudo", "-n", "-H", "-u", run_as, "--"] + argv

    start = time.monotonic()
    try:
        spawn_options = {"start_new_session": True} if os.name == "posix" else {}
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=(agent_subprocess_env()
                 if agent_environment else safe_subprocess_env()),
            cwd=cwd,
            **spawn_options,
        )
    except (FileNotFoundError, OSError, NotImplementedError) as e:
        return ExecResult(exit_code=127, stdout="",
                          stderr=f"命令无法启动：{e}", duration_ms=0)

    assert proc.stdout is not None
    assert proc.stderr is not None
    stdout_capture = _BoundedCapture(max(0, max_output))
    stderr_capture = _BoundedCapture(max(0, max_output))
    stdout_task = asyncio.create_task(_read_bounded(
        proc.stdout, max_output, capture=stdout_capture))
    stderr_task = asyncio.create_task(_read_bounded(
        proc.stderr, max_output, capture=stderr_capture))

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
        out_b, out_truncated = stdout_capture.result()
        err_b, err_truncated = stderr_capture.result()
        stdout, t1 = _decode(
            out_b, max_output, truncated=out_truncated)
        stderr, t2 = _decode(
            err_b, max_output, truncated=err_truncated)
        timeout_notice = f"执行超时（{timeout}s），进程已强制终止"
        stderr = f"{stderr.rstrip()}\n{timeout_notice}" if stderr else timeout_notice
        return ExecResult(
            exit_code=-1,
            stdout=stdout,
            stderr=stderr,
            duration_ms=ms,
            truncated=t1 or t2,
            timed_out=True,
        )

    ms = int((time.monotonic() - start) * 1000)
    stdout, t1 = _decode(out_b, max_output, truncated=out_truncated)
    stderr, t2 = _decode(err_b, max_output, truncated=err_truncated)
    return ExecResult(exit_code=proc.returncode or 0, stdout=stdout,
                      stderr=stderr, duration_ms=ms, truncated=t1 or t2)


async def run_command(
    command: str | list[str],
    *,
    timeout: int = 30,
    max_output: int = 65536,
    run_as: str = "",
    cwd: str | None = None,
    agent_environment: bool = False,
) -> ExecResult:
    """直接执行 argv，不进行 shell 展开。"""
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
    return await _run_argv(
        argv,
        timeout=timeout,
        max_output=max_output,
        run_as=run_as,
        cwd=cwd,
        agent_environment=agent_environment,
    )


async def run_shell(
    command: str,
    *,
    shell: str = "/bin/bash",
    timeout: int = 30,
    max_output: int = 65536,
    run_as: str = "",
    cwd: str | None = None,
) -> ExecResult:
    """通过显式 shell 的 ``-lc`` 执行完整脚本。"""
    if not isinstance(command, str) or not command.strip():
        return ExecResult(exit_code=127, stdout="",
                          stderr="空命令", duration_ms=0)
    if not isinstance(shell, str) or not shell.strip():
        return ExecResult(exit_code=127, stdout="",
                          stderr="shell 路径为空", duration_ms=0)
    return await _run_argv(
        [shell, "-lc", command],
        timeout=timeout,
        max_output=max_output,
        run_as=run_as,
        cwd=cwd,
        agent_environment=True,
    )
