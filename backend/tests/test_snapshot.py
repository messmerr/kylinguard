import kylinguard.snapshot as snap
from kylinguard.models import ExecResult


def _result(code: int, out: str, err: str = "") -> ExecResult:
    return ExecResult(exit_code=code, stdout=out, stderr=err, duration_ms=1)


async def test_全部采集成功(monkeypatch):
    async def fake_run(cmd, **kwargs):
        return _result(0, f"输出于[{cmd}]")

    monkeypatch.setattr(snap, "run_command", fake_run)
    s = await snap.collect_snapshot()
    assert set(s) == {"uptime_load", "memory", "disk",
                      "top_cpu", "failed_units", "recent_errors"}
    assert s["memory"].startswith("输出于[")


async def test_单项失败降级不抛错(monkeypatch):
    async def fake_run(cmd, **kwargs):
        if cmd.startswith("journalctl"):
            return _result(1, "", "权限不足")
        return _result(0, "ok")

    monkeypatch.setattr(snap, "run_command", fake_run)
    s = await snap.collect_snapshot()
    assert "[采集失败]" in s["recent_errors"]
    assert s["memory"] == "ok"


def test_格式化截断():
    s = {"memory": "A" * 5000, "disk": "df 输出"}
    text = snap.format_snapshot(s, per_item=100)
    assert "memory" in text and "disk" in text
    assert len(text) < 700


async def test_并发采集互不阻塞(monkeypatch):
    import asyncio

    concurrent = {"now": 0, "peak": 0}

    async def fake_run(cmd, **kwargs):
        concurrent["now"] += 1
        concurrent["peak"] = max(concurrent["peak"], concurrent["now"])
        await asyncio.sleep(0.05)
        concurrent["now"] -= 1
        return _result(0, "ok")

    monkeypatch.setattr(snap, "run_command", fake_run)
    await snap.collect_snapshot()
    assert concurrent["peak"] >= 2  # 串行采集时峰值恒为 1


async def test_缓存_首次get触发即时采集(monkeypatch):
    calls = {"n": 0}

    async def fake_collect():
        calls["n"] += 1
        return {"memory": f"第{calls['n']}次"}

    monkeypatch.setattr(snap, "collect_snapshot", fake_collect)
    cache = snap.SnapshotCache(interval=999)
    s, age = await cache.get()
    assert s["memory"] == "第1次"
    assert age < 1.0
    # 再次 get 复用缓存，不重复采集
    s2, _ = await cache.get()
    assert calls["n"] == 1 and s2["memory"] == "第1次"


async def test_缓存_后台轮询定期刷新(monkeypatch):
    import asyncio

    calls = {"n": 0}

    async def fake_collect():
        calls["n"] += 1
        return {"memory": f"第{calls['n']}次"}

    monkeypatch.setattr(snap, "collect_snapshot", fake_collect)
    cache = snap.SnapshotCache(interval=0.05)
    await cache.start()
    try:
        await asyncio.sleep(0.18)
    finally:
        await cache.stop()
    assert calls["n"] >= 2  # 至少刷新过两轮
    s, age = await cache.get()
    assert "第" in s["memory"]


async def test_缓存_单轮采集异常不杀死轮询(monkeypatch):
    import asyncio

    calls = {"n": 0}

    async def flaky_collect():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("意外异常")
        return {"memory": "恢复"}

    monkeypatch.setattr(snap, "collect_snapshot", flaky_collect)
    cache = snap.SnapshotCache(interval=0.05)
    await cache.start()
    try:
        await asyncio.sleep(0.18)
    finally:
        await cache.stop()
    assert calls["n"] >= 2  # 异常后仍继续轮询
