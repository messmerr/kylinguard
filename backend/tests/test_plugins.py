from types import SimpleNamespace

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


async def test_restart_service_uses_privileged_helper(monkeypatch):
    import kylinguard.plugins.services as services
    captured = {}

    async def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["run_as"] = kwargs.get("run_as", "")
        return ExecResult(exit_code=0, stdout="done", stderr="", duration_ms=1)

    monkeypatch.setattr(services, "run_command", fake_run)
    monkeypatch.setattr(services, "get_settings", lambda: SimpleNamespace(
        command_timeout=30,
        exec_user="kylinguard-exec",
        privileged_helper="/usr/local/libexec/kylinguard/execctl",
    ))
    out = await services.restart_service(name="nginx")
    assert captured["cmd"] == (
        "sudo -n /usr/local/libexec/kylinguard/execctl service restart nginx")
    assert captured["run_as"] == ""
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


async def test_ping_host_主机名校验():
    import kylinguard.plugins.network as network

    out = await network.ping_host(host="evil.com; rm -rf /", count=4)
    assert "参数不合法" in out
    out2 = await network.ping_host(host="127.0.0.1", count=99)
    assert "参数不合法" in out2


async def test_ping_host_正常构造命令(monkeypatch):
    import kylinguard.plugins.network as network

    async def fake_run(cmd, **kwargs):
        assert cmd == "ping -c 2 -W 2 kylinos.cn"
        return ExecResult(exit_code=0, stdout="2 received", stderr="",
                          duration_ms=1)

    monkeypatch.setattr(network, "run_command", fake_run)
    out = await network.ping_host(host="kylinos.cn", count=2)
    assert "2 received" in out


async def test_disk_hotspots_解析排序(monkeypatch):
    import kylinguard.plugins.disk as disk

    async def fake_run(cmd, **kwargs):
        return ExecResult(exit_code=0,
                          stdout="1024\t/var/a\n8192\t/var/b\n512\t/var/c",
                          stderr="", duration_ms=1)

    monkeypatch.setattr(disk, "run_command", fake_run)
    out = await disk.disk_hotspots(path="/var", depth=1)
    lines = out.splitlines()
    assert "/var/b" in lines[1]  # 占用最大的排最前


async def test_disk_hotspots_拒绝伪文件系统():
    import kylinguard.plugins.disk as disk

    out = await disk.disk_hotspots(path="/proc", depth=1)
    assert "参数不合法" in out


async def test_clean_file_白名单外拒绝():
    import kylinguard.plugins.disk as disk

    assert "拒绝" in await disk.clean_file(path="/etc/passwd")
    assert "拒绝" in await disk.clean_file(path="/var/log/../../etc/shadow")


async def test_clean_file_白名单内执行(monkeypatch):
    import kylinguard.plugins.disk as disk

    async def fake_run(cmd, **kwargs):
        assert cmd == "rm -f /tmp/big.log"
        return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(disk, "run_command", fake_run)
    out = await disk.clean_file(path="/tmp/big.log")
    assert "已删除" in out


async def test_critical_file_perms_基线对比(monkeypatch):
    import kylinguard.plugins.security as security

    async def fake_run(cmd, **kwargs):
        return ExecResult(
            exit_code=0,
            stdout="644 root /etc/passwd\n777 nobody /etc/shadow",
            stderr="", duration_ms=1)

    monkeypatch.setattr(security, "run_command", fake_run)
    out = await security.critical_file_perms()
    assert "✓ /etc/passwd" in out
    assert "⚠ 偏离基线 /etc/shadow" in out


def test_新插件注册表齐全():
    from kylinguard.registry import get_meta

    assert get_meta("disk", "clean_file").risk == RiskLevel.HIGH
    assert get_meta("disk", "clean_file").needs_sudo is True
    assert get_meta("network", "ping_host").risk == RiskLevel.LOW
    assert get_meta("security", "critical_file_perms").risk == RiskLevel.LOW
