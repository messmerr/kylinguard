from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

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
    batch = get_meta("run_command", "run_batch")
    assert batch.dynamic is True
    assert batch.risk == RiskLevel.MEDIUM


async def test_sysinfo_top_processes(monkeypatch):
    import kylinguard.plugins.sysinfo as sysinfo

    async def fake_run(cmd, **kwargs):
        assert "ps aux" in cmd
        return ExecResult(exit_code=0, stdout="PID CPU CMD", stderr="",
                          duration_ms=1)

    monkeypatch.setattr(sysinfo, "run_command", fake_run)
    out = await sysinfo.top_processes(sort_by="cpu", limit=5)
    assert "PID" in out


async def test_system_snapshot_全部采集失败显式失败(monkeypatch):
    import kylinguard.plugins.sysinfo as sysinfo

    async def fake_collect():
        return {
            "memory": "[采集失败] free 不可用",
            "disk": "[采集失败] df 不可用",
        }

    monkeypatch.setattr(sysinfo, "collect_snapshot", fake_collect)
    with pytest.raises(ToolError, match="所有采集项均不可用"):
        await sysinfo.system_snapshot()


async def test_system_snapshot_部分降级仍返回可用数据(monkeypatch):
    import kylinguard.plugins.sysinfo as sysinfo

    async def fake_collect():
        return {
            "memory": "Mem: 100 50",
            "disk": "[采集失败] df 不可用",
        }

    monkeypatch.setattr(sysinfo, "collect_snapshot", fake_collect)
    out = await sysinfo.system_snapshot()
    assert "Mem: 100 50" in out and "df 不可用" in out


async def test_sysinfo_参数越界收敛(monkeypatch):
    import kylinguard.plugins.sysinfo as sysinfo

    with pytest.raises(ToolError, match="参数不合法"):
        await sysinfo.top_processes(sort_by="非法字段", limit=99999)


async def test_service_status_服务名校验():
    import kylinguard.plugins.services as services

    with pytest.raises(ToolError, match="服务名不合法"):
        await services.service_status(name="nginx; rm -rf /")


async def test_service_status_不存在显式失败(monkeypatch):
    import kylinguard.plugins.services as services

    async def fake_run(cmd, **kwargs):
        return ExecResult(exit_code=4, stdout="",
                          stderr="Unit missing.service could not be found.",
                          duration_ms=1)

    monkeypatch.setattr(services, "run_command", fake_run)
    with pytest.raises(ToolError, match="could not be found"):
        await services.service_status(name="missing.service")


async def test_service_status_已停止仍是有效状态(monkeypatch):
    import kylinguard.plugins.services as services

    async def fake_run(cmd, **kwargs):
        return ExecResult(exit_code=3, stdout="inactive (dead)", stderr="",
                          duration_ms=1)

    monkeypatch.setattr(services, "run_command", fake_run)
    assert "inactive" in await services.service_status(name="demo.service")


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
    monkeypatch.setattr(services, "get_execution_settings", lambda: SimpleNamespace(
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

    with pytest.raises(ToolError, match="仅允许"):
        await logs.tail_file(path="/etc/shadow", lines=10)


async def test_tail_file_拒绝路径穿越():
    import kylinguard.plugins.logs as logs

    with pytest.raises(ToolError, match="仅允许"):
        await logs.tail_file(path="/var/log/../../etc/shadow", lines=10)


async def test_journal_search_priority枚举():
    import kylinguard.plugins.logs as logs

    with pytest.raises(ToolError, match="参数不合法"):
        await logs.journal_search(unit="", priority="verbose", lines=10)


async def test_run_command_透传执行(monkeypatch):
    import kylinguard.plugins.run_command as rc

    async def fake_run(cmd, **kwargs):
        return ExecResult(exit_code=0, stdout=f"ran:{cmd}", stderr="",
                          duration_ms=1)

    monkeypatch.setattr(rc, "run_command_exec", fake_run)
    out = await rc.run_command(command="uptime")
    assert "ran:uptime" in out
    assert "exit_code=0" in out


async def test_run_command_非零退出显式失败(monkeypatch):
    import kylinguard.plugins.run_command as rc

    async def fake_run(cmd, **kwargs):
        return ExecResult(exit_code=127, stdout="", stderr="not found",
                          duration_ms=1)

    monkeypatch.setattr(rc, "run_command_exec", fake_run)
    with pytest.raises(ToolError, match="exit_code=127"):
        await rc.run_command(command="missing-command")


async def test_run_batch_逐条argv执行且遇错停止(monkeypatch):
    import kylinguard.plugins.run_command as rc
    captured = []

    async def fake_run(argv, **kwargs):
        captured.append(argv)
        return ExecResult(
            exit_code=7 if argv[0] == "false" else 0,
            stdout=f"ran:{argv[0]}", stderr="", duration_ms=1,
        )

    monkeypatch.setattr(rc, "run_command_exec", fake_run)
    with pytest.raises(ToolError, match="批量命令执行失败"):
        await rc.run_batch(commands=[
            ["ps", "aux"], ["false"], ["free", "-m"],
        ])
    assert captured == [["ps", "aux"], ["false"]]


async def test_run_batch_可选择执行完所有命令(monkeypatch):
    import kylinguard.plugins.run_command as rc
    captured = []

    async def fake_run(argv, **kwargs):
        captured.append(argv)
        return ExecResult(
            exit_code=1 if argv[0] == "false" else 0,
            stdout="", stderr="boom" if argv[0] == "false" else "",
            duration_ms=1,
        )

    monkeypatch.setattr(rc, "run_command_exec", fake_run)
    with pytest.raises(ToolError, match='"commands_executed": 3'):
        await rc.run_batch(
            commands=[["uptime"], ["false"], ["free", "-m"]],
            stop_on_error=False,
        )
    assert len(captured) == 3


@pytest.mark.parametrize("commands", [
    [], [[]], [["ok", ""]], [["ok\x00bad"]],
])
async def test_run_batch_拒绝非法argv(commands):
    import kylinguard.plugins.run_command as rc
    with pytest.raises(ToolError):
        await rc.run_batch(commands=commands)


async def test_run_batch_operators按短路语义执行(monkeypatch):
    import json
    import kylinguard.plugins.run_command as rc
    captured = []

    async def fake_run(argv, **kwargs):
        captured.append(argv[0])
        return ExecResult(
            exit_code=1 if argv[0] == "false" else 0,
            stdout="", stderr="", duration_ms=1,
        )

    monkeypatch.setattr(rc, "run_command_exec", fake_run)
    output = await rc.run_batch(
        commands=[["false"], ["then"], ["fallback"], ["always"]],
        operators=["&&", "||", ";"],
    )
    result = json.loads(output)
    assert captured == ["false", "fallback", "always"]
    assert result["commands_executed"] == 3
    assert result["commands_skipped"] == 1
    assert result["results"][1]["skip_reason"] == "previous_failed"


async def test_run_batch_operators最终失败才令工具失败(monkeypatch):
    import kylinguard.plugins.run_command as rc

    async def fake_run(argv, **kwargs):
        return ExecResult(
            exit_code=1 if argv[0] == "false" else 0,
            stdout="", stderr="", duration_ms=1,
        )

    monkeypatch.setattr(rc, "run_command_exec", fake_run)
    # 中间失败被 || 恢复，整个 AND-OR 列表成功。
    await rc.run_batch(
        commands=[["false"], ["true"]], operators=["||"])
    with pytest.raises(ToolError, match="批量命令执行失败"):
        await rc.run_batch(
            commands=[["true"], ["false"]], operators=["&&"])


@pytest.mark.parametrize(("commands", "operators"), [
    ([["a"], ["b"]], [";​"]),
    ([["a"], ["b"]], []),
    ([["a"], ["b"]], [";", "&&"]),
])
async def test_run_batch_operators形状必须有效(commands, operators):
    import kylinguard.plugins.run_command as rc
    if operators == []:
        # 空 operators 是合法的普通批处理。
        return
    with pytest.raises(ToolError):
        await rc.run_batch(commands=commands, operators=operators)


async def test_ping_host_主机名校验():
    import kylinguard.plugins.network as network

    with pytest.raises(ToolError, match="参数不合法"):
        await network.ping_host(host="evil.com; rm -rf /", count=4)
    with pytest.raises(ToolError, match="参数不合法"):
        await network.ping_host(host="127.0.0.1", count=99)


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

    with pytest.raises(ToolError, match="参数不合法"):
        await disk.disk_hotspots(path="/proc", depth=1)


async def test_clean_file_白名单外拒绝():
    import kylinguard.plugins.disk as disk

    with pytest.raises(ToolError, match="拒绝"):
        await disk.clean_file(path="/etc/passwd")
    with pytest.raises(ToolError, match="拒绝"):
        await disk.clean_file(path="/var/log/../../etc/shadow")


async def test_clean_file_白名单内执行(monkeypatch):
    import kylinguard.plugins.disk as disk

    async def fake_run(cmd, **kwargs):
        assert cmd == "rm -f /tmp/big.log"
        return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(disk, "run_command", fake_run)
    out = await disk.clean_file(path="/tmp/big.log")
    assert "已删除" in out


async def test_clean_file_命令失败显式失败(monkeypatch):
    import kylinguard.plugins.disk as disk

    async def fake_run(cmd, **kwargs):
        return ExecResult(exit_code=1, stdout="", stderr="permission denied",
                          duration_ms=1)

    monkeypatch.setattr(disk, "run_command", fake_run)
    with pytest.raises(ToolError, match="permission denied"):
        await disk.clean_file(path="/tmp/big.log")


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


def test_结构化文件工具注册表齐全():
    assert get_meta("files", "read_file").risk == RiskLevel.LOW
    assert get_meta("files", "list_directory").risk == RiskLevel.LOW
    assert get_meta("files", "mkdir").risk == RiskLevel.MEDIUM
    assert get_meta("files", "write_file").risk == RiskLevel.MEDIUM
    assert get_meta("files", "replace_text").risk == RiskLevel.MEDIUM
    assert get_meta("files", "move").risk == RiskLevel.MEDIUM
    assert get_meta("files", "delete").risk == RiskLevel.HIGH
