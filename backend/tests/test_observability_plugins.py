from kylinguard.models import ExecResult, RiskLevel
from kylinguard.registry import get_meta


def test_new_observability_tools_registered_low_risk():
    assert get_meta("sysinfo", "process_tree").risk == RiskLevel.LOW
    assert get_meta("network", "lsof_listening").risk == RiskLevel.LOW
    assert get_meta("disk", "io_stats").risk == RiskLevel.LOW


async def test_process_tree(monkeypatch):
    import kylinguard.plugins.sysinfo as sysinfo

    async def fake_run(cmd, **kwargs):
        assert "ps -eo" in cmd
        return ExecResult(exit_code=0, stdout="PID PPID\n1 0\n2 1",
                          stderr="", duration_ms=1)

    monkeypatch.setattr(sysinfo, "run_command", fake_run)
    out = await sysinfo.process_tree(limit=10)
    assert "PID" in out and "2 1" in out


async def test_lsof_listening_falls_back_to_ss(monkeypatch):
    import kylinguard.plugins.network as network
    calls = []

    async def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd.startswith("lsof"):
            return ExecResult(exit_code=127, stdout="", stderr="not found",
                              duration_ms=1)
        return ExecResult(exit_code=0, stdout="LISTEN 0 128 *:8000",
                          stderr="", duration_ms=1)

    monkeypatch.setattr(network, "run_command", fake_run)
    out = await network.lsof_listening()
    assert calls == ["lsof -nP -iTCP -sTCP:LISTEN", "ss -tulnp"]
    assert "已降级为 ss" in out


async def test_io_stats_falls_back_to_diskstats(monkeypatch):
    import kylinguard.plugins.disk as disk
    calls = []

    async def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd.startswith("iostat"):
            return ExecResult(exit_code=127, stdout="", stderr="not found",
                              duration_ms=1)
        return ExecResult(exit_code=0, stdout="8 0 sda 1 2 3\n8 16 sdb 4",
                          stderr="", duration_ms=1)

    monkeypatch.setattr(disk, "run_command", fake_run)
    out = await disk.io_stats()
    assert calls == ["iostat -xz 1 1", "cat /proc/diskstats"]
    assert "/proc/diskstats" in out
