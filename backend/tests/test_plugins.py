from kylinguard.models import ExecResult, RiskLevel
from kylinguard.registry import get_meta


def test_已注册工具元数据():
    m = get_meta("sysinfo", "system_snapshot")
    assert m.risk == RiskLevel.LOW
    assert m.needs_sudo is False


def test_未注册工具按最高危():
    m = get_meta("thirdparty", "unknown_tool")
    assert m.risk == RiskLevel.HIGH
    assert "未注册" in m.description


def test_run_command为动态风险():
    m = get_meta("run_command", "run_command")
    assert m.dynamic is True
    assert m.risk == RiskLevel.MEDIUM


async def test_sysinfo_top_processes(monkeypatch):
    import kylinguard.plugins.sysinfo as sysinfo

    async def fake_run(cmd, **kwargs):
        assert "ps aux" in cmd
        return ExecResult(exit_code=0, stdout="PID CPU CMD", stderr="",
                          duration_ms=1)

    monkeypatch.setattr(sysinfo, "run_command", fake_run)
    out = await sysinfo.top_processes(sort_by="cpu", limit=5)
    assert "PID" in out


async def test_sysinfo_参数越界收敛(monkeypatch):
    import kylinguard.plugins.sysinfo as sysinfo

    out = await sysinfo.top_processes(sort_by="非法字段", limit=99999)
    assert "参数不合法" in out
