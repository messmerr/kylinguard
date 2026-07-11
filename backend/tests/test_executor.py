import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

import kylinguard.executor as executor
from kylinguard.executor import run_command

PY = sys.executable


def _descendant_command(
    pid_file: Path,
    *,
    keep_parent_alive: bool = True,
) -> list[str]:
    tail = "; time.sleep(30)" if keep_parent_alive else ""
    code = (
        "import pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(30)']); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid), "
        f"encoding='ascii'){tail}"
    )
    return [PY, "-c", code]


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    # Linux 上已终止但尚未被 init 回收的孤儿会短暂保留为 zombie。
    # 对执行器而言它已不能继续工作，应视为成功终止。
    stat_file = Path(f"/proc/{pid}/stat")
    try:
        if stat_file.read_text(encoding="ascii").split()[2] == "Z":
            return False
    except (FileNotFoundError, IndexError, OSError):
        pass
    return True


async def _wait_for_pid(pid_file: Path) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while loop.time() < deadline:
        try:
            return int(pid_file.read_text(encoding="ascii"))
        except (FileNotFoundError, ValueError):
            await asyncio.sleep(0.01)
    raise AssertionError("后代进程未能在期限内写入 PID")


async def _assert_process_stopped(pid: int) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3
    while loop.time() < deadline:
        if not _process_is_running(pid):
            return
        await asyncio.sleep(0.02)
    assert not _process_is_running(pid), f"后代进程 {pid} 仍在运行"


async def test_正常执行捕获stdout():
    # -X utf8 强制子进程 UTF-8 输出（Windows 开发机默认 GBK；生产麒麟环境为 UTF-8）
    r = await run_command(f'"{PY}" -X utf8 -c "print(\'麒盾\')"')
    assert r.exit_code == 0
    assert "麒盾" in r.stdout
    assert r.timed_out is False


async def test_非零退出码与stderr():
    r = await run_command(
        f'"{PY}" -c "import sys; sys.stderr.write(\'boom\'); sys.exit(3)"'
    )
    assert r.exit_code == 3
    assert "boom" in r.stderr


async def test_超时强杀():
    r = await run_command(f'"{PY}" -c "import time; time.sleep(30)"', timeout=1)
    assert r.timed_out is True
    assert r.exit_code != 0


async def test_输出截断():
    r = await run_command(f'"{PY}" -c "print(\'A\' * 100000)"', max_output=1000)
    assert r.truncated is True
    assert len(r.stdout) <= 1100  # 截断上限 + 截断提示


async def test_stdout与stderr并发读取且分别受预算约束():
    code = (
        "import os; chunk = b'A' * 65536; "
        "[(os.write(1, chunk), os.write(2, chunk)) for _ in range(32)]"
    )
    r = await run_command([PY, "-c", code], timeout=10, max_output=1024)

    assert r.exit_code == 0
    assert r.truncated is True
    assert r.stdout == "A" * 1024 + executor._TRUNCATE_MARK
    assert r.stderr == "A" * 1024 + executor._TRUNCATE_MARK


async def test_有界读取器会排空大输出但只保留预算内容():
    class RepeatingStream:
        def __init__(self) -> None:
            self.remaining = 2048
            self.chunk = b"X" * 4096

        async def read(self, _size: int) -> bytes:
            if self.remaining == 0:
                return b""
            self.remaining -= 1
            return self.chunk

    stream = RepeatingStream()
    kept, truncated = await executor._read_bounded(stream, 1000)

    assert stream.remaining == 0
    assert kept == b"X" * 1000
    assert truncated is True


async def test_命令不存在():
    r = await run_command("kylinguard-no-such-cmd-xyz")
    assert r.exit_code == 127


async def test_执行命令不继承控制面秘密(monkeypatch):
    monkeypatch.setenv("KG_LLM_API_KEY", "llm-secret")
    monkeypatch.setenv("KG_ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:password@proxy")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/auth.sock")
    code = (
        "import os; print('|'.join(str(os.getenv(k)) for k in "
        "['KG_LLM_API_KEY','KG_ADMIN_PASSWORD','OPENAI_API_KEY',"
        "'HTTPS_PROXY','SSH_AUTH_SOCK']))"
    )
    r = await run_command([PY, "-c", code])
    assert r.exit_code == 0
    assert r.stdout.strip() == "None|None|None|None|None"


async def test_取消会终止并回收子进程(monkeypatch):
    created = []
    original = executor.asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        proc = await original(*args, **kwargs)
        created.append(proc)
        return proc

    monkeypatch.setattr(executor.asyncio, "create_subprocess_exec", capture)
    task = asyncio.create_task(run_command(
        [PY, "-c", "import time; time.sleep(30)"], timeout=60))
    while not created:
        await asyncio.sleep(0)
    # 让 run_command 进入受 CancelledError 保护的 communicate 等待区间。
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert created[0].returncode is not None


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group 专项")
async def test_超时会终止整个进程组(tmp_path):
    pid_file = tmp_path / "timeout-descendant.pid"
    task = asyncio.create_task(run_command(
        _descendant_command(pid_file, keep_parent_alive=False), timeout=1,
    ))
    descendant_pid = await _wait_for_pid(pid_file)

    try:
        result = await task
        assert result.timed_out is True
        await _assert_process_stopped(descendant_pid)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if _process_is_running(descendant_pid):
            os.kill(descendant_pid, signal.SIGKILL)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group 专项")
async def test_取消会终止整个进程组(tmp_path):
    pid_file = tmp_path / "cancel-descendant.pid"
    task = asyncio.create_task(run_command(
        _descendant_command(pid_file, keep_parent_alive=False), timeout=60,
    ))
    descendant_pid = await _wait_for_pid(pid_file)
    task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        await _assert_process_stopped(descendant_pid)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if _process_is_running(descendant_pid):
            os.kill(descendant_pid, signal.SIGKILL)
