import kylinguard.snapshot as snap
import kylinguard.alert_pusher as alert_pusher
from kylinguard.models import ExecResult
from kylinguard.alert_rules import AlertRule


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
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        if "journalctl" in cmd_str or "recent_errors" in cmd_str or "WinEvent" in cmd_str:
            return _result(1, "", "权限不足")
        return _result(0, "ok")

    monkeypatch.setattr(snap, "run_command", fake_run)
    monkeypatch.setattr(snap, "_SNAPSHOT_COMMANDS", snap._SNAPSHOT_COMMANDS_LINUX)
    monkeypatch.setattr(snap, "_STATIC_SNAPSHOT", {})
    s = await snap.collect_snapshot()
    assert "[采集失败]" in s["recent_errors"]
    assert s["memory"] == "ok"


def test_格式化截断():
    s = {"memory": "A" * 5000, "disk": "df 输出"}
    text = snap.format_snapshot(s, per_item=100)
    assert "memory" in text and "disk" in text
    assert len(text) < 700


def test_macos磁盘解析忽略伪文件系统与inode百分比(monkeypatch):
    monkeypatch.setattr(snap, "_IS_WINDOWS", False)
    raw = """Filesystem        Size Used Avail Capacity iused ifree %iused Mounted on
/dev/disk3s1s1   228Gi 17Gi 22Gi 44% 447k 227M 0% /
devfs            204Ki 204Ki 0Bi 100% 706 0 100% /dev
/dev/disk3s5     228Gi 171Gi 22Gi 89% 2.4M 227M 1% /System/Volumes/Data
map auto_home      0Bi 0Bi 0Bi 100% 0 0 - /System/Volumes/Data/home
"""
    assert snap._parse_disk_pcts({"disk": raw}) == [
        ("/", 44), ("/System/Volumes/Data", 89),
    ]


def test_load_average不会被当成cpu百分比(monkeypatch):
    monkeypatch.setattr(snap, "_IS_WINDOWS", False)
    assert snap._parse_cpu_pct({
        "uptime_load": "up 4:47, load averages: 1.88 2.03 2.27",
    }) is None
    assert snap._parse_cpu_pct({
        "uptime_load": "CPU usage: 22.98% user, 13.70% sys, 63.30% idle",
    }) == 37


def test_linux_top快照可以解析真实cpu使用率(monkeypatch):
    monkeypatch.setattr(snap, "_IS_WINDOWS", False)
    assert "/usr/bin/top" in snap._SNAPSHOT_COMMANDS_LINUX["uptime_load"]
    assert snap._parse_cpu_pct({
        "uptime_load": (
            "top - 12:30:01 up 1 day, load average: 0.20, 0.30, 0.40\n"
            "%Cpu(s):  4.0 us,  2.0 sy,  0.0 ni, 87.0 id, 7.0 wa"
        ),
    }) == 13


def test_macos内存压力使用空闲百分比(monkeypatch):
    monkeypatch.setattr(snap, "_IS_WINDOWS", False)
    assert snap._parse_memory_pct({
        "memory": "System-wide memory free percentage: 68%",
    }) == 32


def test_平台不支持不会触发失败服务告警(monkeypatch):
    monkeypatch.setattr(snap, "_IS_WINDOWS", False)
    snapshot = {"failed_units": "[平台不支持] macOS 不提供 systemd 失败服务列表"}
    assert snap._has_failed_units(snapshot) is False
    assert snap.detect_anomalies(snapshot) == []


def test_未读同类告警不会重复堆积(monkeypatch):
    now = {"value": 1000.0}
    monkeypatch.setattr(snap.time, "time", lambda: now["value"])
    store = snap.AlertStore()
    alert = {"kind": "disk_C", "severity": "warning", "title": "磁盘紧张",
             "message": "首次", "metric": "90%"}

    assert len(store.ingest([alert])) == 1
    now["value"] = 2000.0  # 即使超过冷却期，未读同类告警仍更新原记录
    store.ingest([{**alert, "message": "仍然紧张", "metric": "92%"}])
    active = store.active()
    assert len(active) == 1
    assert active[0]["message"] == "仍然紧张"

    assert store.ack(active[0]["id"]) is True
    assert store.ingest([alert]) == []  # 确认后从当前时刻重新计算冷却期


def test_内存告警支持一键确认且重复调用幂等():
    store = snap.AlertStore()
    first = store.ingest([{
        "kind": "memory", "severity": "warning", "title": "内存",
        "message": "内存偏高", "metric": "86%",
    }])[0]
    second = store.ingest([{
        "kind": "cpu", "severity": "warning", "title": "CPU",
        "message": "CPU 偏高", "metric": "81%",
    }])[0]
    assert store.ack(first["id"]) is True
    assert store.ack_all() == [second["id"]]
    assert store.ack_all() == []
    assert store.active() == []


async def test_规则评估使用磁盘最高值并正确表达失败服务(monkeypatch):
    histories = []

    class Store:
        def list_rules(self):
            base = dict(severity="warning", silence_minutes=10,
                        channel_ids=[], enabled=True, created_at=0)
            return [
                AlertRule(1, "任意磁盘过高", "disk_pct", ">=", 95, **base),
                AlertRule(2, "失败服务", "failed_services", ">=", 85, **base),
            ]

        def get_last_fired(self, _rule_id): return 0
        def get_channel(self, _channel_id): return None
        def record_trigger(self, **entry):
            histories.append(entry)
            return len(histories)
        def update_history_channels(self, history_id, channels_notified):
            histories[history_id - 1]["channels_notified"] = channels_notified

    async def fake_push_all(_channels, _payload): return []

    monkeypatch.setattr(snap, "_IS_WINDOWS", True)
    monkeypatch.setattr(alert_pusher, "push_all", fake_push_all)
    await snap._evaluate_rules({
        "disk": "C total=100G used=95G free=5G\nD total=100G used=90G free=10G",
        "failed_units": "Name DisplayName Status\n---- ----------- ------\nsvc Demo Stopped",
    }, Store())

    assert [item["metric_value"] for item in histories] == ["95%", "存在"]


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
