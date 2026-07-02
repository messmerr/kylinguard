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
