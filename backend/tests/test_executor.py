import asyncio
import sys

import pytest

import kylinguard.executor as executor
from kylinguard.executor import run_command

PY = sys.executable


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


async def test_命令不存在():
    r = await run_command("kylinguard-no-such-cmd-xyz")
    assert r.exit_code == 127


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
