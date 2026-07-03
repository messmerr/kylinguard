from types import SimpleNamespace

from kylinguard.models import ExecResult


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


async def test_clean_file_uses_privileged_helper(monkeypatch):
    import kylinguard.plugins.disk as disk
    captured = {}

    async def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return ExecResult(exit_code=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setattr(disk, "run_command", fake_run)
    monkeypatch.setattr(disk, "get_settings", lambda: SimpleNamespace(
        command_timeout=30,
        exec_user="kylinguard-exec",
        privileged_helper="/usr/local/libexec/kylinguard/execctl",
    ))
    out = await disk.clean_file(path="/tmp/big.log")
    assert captured["cmd"] == (
        "sudo -n /usr/local/libexec/kylinguard/execctl clean-file /tmp/big.log")
    assert "已删除" in out
