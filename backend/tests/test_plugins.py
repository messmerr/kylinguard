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


async def test_service_status_服务名校验():
    import kylinguard.plugins.services as services

    out = await services.service_status(name="nginx; rm -rf /")
    assert "服务名不合法" in out


async def test_restart_service_构造sudo命令(monkeypatch):
    import kylinguard.plugins.services as services
    captured = {}

    async def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["run_as"] = kwargs.get("run_as", "")
        return ExecResult(exit_code=0, stdout="done", stderr="", duration_ms=1)

    monkeypatch.setattr(services, "run_command", fake_run)
    out = await services.restart_service(name="nginx")
    assert captured["cmd"] == "systemctl restart nginx"
    assert "done" in out


async def test_tail_file_限制在var_log():
    import kylinguard.plugins.logs as logs

    out = await logs.tail_file(path="/etc/shadow", lines=10)
    assert "仅允许" in out


async def test_tail_file_拒绝路径穿越():
    import kylinguard.plugins.logs as logs

    out = await logs.tail_file(path="/var/log/../../etc/shadow", lines=10)
    assert "仅允许" in out


async def test_journal_search_priority枚举():
    import kylinguard.plugins.logs as logs

    out = await logs.journal_search(unit="", priority="verbose", lines=10)
    assert "参数不合法" in out


async def test_run_command_透传执行(monkeypatch):
    import kylinguard.plugins.run_command as rc

    async def fake_run(cmd, **kwargs):
        return ExecResult(exit_code=0, stdout=f"ran:{cmd}", stderr="",
                          duration_ms=1)

    monkeypatch.setattr(rc, "run_command_exec", fake_run)
    out = await rc.run_command(command="uptime")
    assert "ran:uptime" in out
    assert "exit_code=0" in out
