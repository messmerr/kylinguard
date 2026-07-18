import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from kylinguard.models import ExecResult, RiskLevel
from kylinguard.registry import get_meta


def _result(code=0, stdout="", stderr=""):
    return ExecResult(exit_code=code, stdout=stdout, stderr=stderr, duration_ms=1)


def test_麒麟工具全部注册为只读低风险():
    for name in (
        "system_identity", "capability_matrix", "deployment_readiness",
        "service_diagnosis", "network_diagnosis", "io_diagnosis",
        "binary_compatibility",
    ):
        meta = get_meta("kylin", name)
        assert meta.risk == RiskLevel.LOW
        assert meta.needs_sudo is False


async def test_system_identity返回结构化证据(monkeypatch):
    import kylinguard.plugins.kylin as kylin

    async def fake_identity():
        return {"schema_version": 1, "kylin": {"detected": True}}

    monkeypatch.setattr(kylin, "collect_kylin_identity", fake_identity)
    payload = json.loads(await kylin.system_identity())
    assert payload["kylin"]["detected"] is True


async def test_service_diagnosis提取状态与失败线索(monkeypatch):
    import kylinguard.plugins.kylin as kylin

    async def fake_run(argv, **_kwargs):
        if argv[0] == "systemctl":
            return _result(stdout=(
                "Id=demo.service\nLoadState=loaded\nActiveState=failed\n"
                "SubState=failed\nResult=exit-code\nExecMainStatus=203\nNRestarts=3\n"
            ))
        return _result(stdout="2026-07-19 demo: Failed to execute /opt/demo")

    monkeypatch.setattr(kylin, "run_command", fake_run)
    payload = json.loads(await kylin.service_diagnosis("demo.service", lines=80))
    kinds = {item["kind"] for item in payload["findings"]}
    assert {"failed_state", "unit_result", "main_exit_status", "restart_loop"}.issubset(kinds)
    assert payload["evidence_complete"] is True


async def test_service_diagnosis拒绝注入式单元名():
    import kylinguard.plugins.kylin as kylin
    with pytest.raises(ToolError, match="参数不合法"):
        await kylin.service_diagnosis("demo;reboot.service")
    with pytest.raises(ToolError, match="参数不合法"):
        await kylin.service_diagnosis("--system")


async def test_network_diagnosis归并接口与错误计数(monkeypatch):
    import kylinguard.plugins.kylin as kylin

    async def fake_run(argv, **_kwargs):
        joined = " ".join(argv)
        if "address show" in joined:
            return _result(stdout=json.dumps([{
                "ifname": "eth0", "operstate": "DOWN", "mtu": 1500,
                "flags": ["BROADCAST"],
                "addr_info": [{"family": "inet", "local": "10.0.0.2", "prefixlen": 24}],
            }]))
        if "link show" in joined:
            return _result(stdout=json.dumps([{
                "ifname": "eth0",
                "stats64": {"rx": {"bytes": 1, "packets": 1, "errors": 2, "dropped": 3},
                            "tx": {"bytes": 1, "packets": 1, "errors": 0, "dropped": 0}},
            }]))
        if "route show" in joined:
            return _result(stdout='[{"dst":"default","gateway":"10.0.0.1","dev":"eth0"}]')
        return _result(stdout="TCP: 3 (estab 1)")

    monkeypatch.setattr(kylin, "run_command", fake_run)
    monkeypatch.setattr(kylin, "_native_available", lambda _capability: [])
    payload = json.loads(await kylin.network_diagnosis("eth0"))
    assert payload["interfaces"][0]["operstate"] == "DOWN"
    assert {item["kind"] for item in payload["findings"]} == {
        "link_not_up", "link_errors_or_drops",
    }
    assert payload["routes"][0]["gateway"] == "10.0.0.1"


async def test_io_diagnosis在iostat缺失时诚实降级(monkeypatch):
    import kylinguard.plugins.kylin as kylin

    async def fake_run(argv, **_kwargs):
        if argv[0] == "lsblk":
            return _result(stdout='{"blockdevices":[{"name":"sda","path":"/dev/sda"}]}')
        if argv[0] == "iostat":
            return _result(127, stderr="not found")
        if argv[:2] == ["cat", "/proc/diskstats"]:
            return _result(stdout="8 0 sda 1 0 2 3 4 0 5 6 0 7 8\n")
        raise AssertionError(argv)

    monkeypatch.setattr(kylin, "run_command", fake_run)
    monkeypatch.setattr(kylin, "_native_available", lambda _capability: [])
    payload = json.loads(await kylin.io_diagnosis("sda"))
    assert payload["io_source"] == "proc_diskstats"
    assert "累计计数" in " ".join(payload["limitations"])
    assert "sda" in payload["io_evidence"]


async def test_io_diagnosis拒绝选项式设备名():
    import kylinguard.plugins.kylin as kylin
    with pytest.raises(ToolError, match="参数不合法"):
        await kylin.io_diagnosis("-x")


async def test_binary_compatibility确认架构不匹配且不调用ldd(monkeypatch):
    import kylinguard.plugins.kylin as kylin
    called = []

    async def fake_run(argv, **_kwargs):
        called.append(argv)
        if argv[0] == "file":
            return _result(stdout="ELF 64-bit LSB pie executable, x86-64")
        if argv[:2] == ["readelf", "-h"]:
            return _result(stdout="Class: ELF64\nData: 2's complement, little endian\nMachine: Advanced Micro Devices X86-64\n")
        if argv[:2] == ["readelf", "-l"]:
            return _result(stdout="[Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]")
        if argv[:2] == ["uname", "-m"]:
            return _result(stdout="loongarch64")
        if argv[0] in {"rpm", "dpkg-query"}:
            return _result(1, stderr="not owned")
        raise AssertionError(argv)

    monkeypatch.setattr(kylin, "run_command", fake_run)
    payload = json.loads(await kylin.binary_compatibility("/opt/demo/bin/app"))
    assert payload["host_architecture"] == "loongarch64"
    assert payload["elf"]["architecture"] == "x86_64"
    assert payload["architecture_match"] is False
    assert any(item["kind"] == "architecture_mismatch" for item in payload["findings"])
    assert all(argv[0] != "ldd" for argv in called)


@pytest.mark.parametrize("path", ["relative", "/proc/1/exe", "/dev/sda", "/tmp/a\nb"])
async def test_binary_compatibility拒绝危险路径(path):
    import kylinguard.plugins.kylin as kylin
    with pytest.raises(ToolError):
        await kylin.binary_compatibility(path)
