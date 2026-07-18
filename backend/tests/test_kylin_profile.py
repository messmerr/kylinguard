import json

from kylinguard.kylin_profile import (
    collect_capability_matrix,
    collect_deployment_readiness,
    collect_kylin_identity,
    normalize_architecture,
    parse_os_release,
)
from kylinguard.models import ExecResult


def _result(code=0, stdout="", stderr=""):
    return ExecResult(exit_code=code, stdout=stdout, stderr=stderr, duration_ms=1)


def _identity_runner(*, arch="loongarch64", os_release=None):
    os_release = os_release or """ID=kylin
NAME="Kylin Linux Advanced Server"
PRETTY_NAME="银河麒麟高级服务器操作系统 V11"
VERSION="V11 (Sword)"
VERSION_ID="V11"
"""
    responses = {
        ("cat", "/etc/os-release"): _result(stdout=os_release),
        ("cat", "/etc/kylin-release"): _result(stdout="Kylin Linux Advanced Server V11"),
        ("nkvers",): _result(stdout="银河麒麟高级服务器操作系统 V11 2503"),
        ("uname", "-m"): _result(stdout=arch),
        ("uname", "-r"): _result(stdout="6.6.0-kylin"),
        ("systemd", "--version"): _result(stdout="systemd 255 (255.4)"),
        ("getconf", "GNU_LIBC_VERSION"): _result(stdout="glibc 2.38"),
        ("node", "--version"): _result(stdout="v18.20.4"),
        ("npm", "--version"): _result(stdout="10.8.2"),
    }

    async def runner(argv, **_kwargs):
        return responses.get(tuple(argv), _result(127, stderr="not found"))

    return runner


def test_os_release只解析白名单并收敛控制字符():
    parsed = parse_os_release(
        'ID=kylin\nPRETTY_NAME="银河麒麟\\nV11"\nHOME_URL=https://example.test\n'
    )
    assert parsed["ID"] == "kylin"
    assert "HOME_URL" not in parsed
    assert "\n" not in parsed["PRETTY_NAME"]


def test_架构别名归一化():
    assert normalize_architecture("loong64\n") == "loongarch64"
    assert normalize_architecture("AMD64") == "x86_64"
    assert normalize_architecture("arm64") == "aarch64"


async def test_识别麒麟V11服务器版LoongArch完全匹配():
    profile = await collect_kylin_identity(
        runner=_identity_runner(), platform_name="linux")

    assert profile["kylin"]["detected"] is True
    assert profile["kylin"]["edition"] == "server"
    assert profile["architecture"]["normalized"] == "loongarch64"
    assert profile["runtime"]["init_system"] == "systemd"
    assert profile["contest_target"] == {
        "kylin": True,
        "version_v11": True,
        "server_edition": True,
        "loongarch": True,
        "status": "matched",
    }
    assert profile["warnings"] == []
    assert {"os_release", "nkvers", "architecture"}.issubset(profile["evidence_sources"])


async def test_通用x86Linux不会被包装成麒麟():
    runner = _identity_runner(
        arch="x86_64",
        os_release='ID=ubuntu\nNAME="Ubuntu"\nVERSION_ID="24.04"\n',
    )
    # nkvers/kylin-release 在此 runner 中仍模拟成功，所以单独覆盖为通用主机。
    # 重新定义更贴近真实非麒麟环境的失败结果。
    async def generic_runner(argv, **kwargs):
        if tuple(argv) in {("nkvers",), ("cat", "/etc/kylin-release")}:
            return _result(127, stderr="not found")
        return await runner(argv, **kwargs)

    profile = await collect_kylin_identity(
        runner=generic_runner, platform_name="linux")
    assert profile["kylin"]["detected"] is False
    assert profile["architecture"]["loongarch"] is False
    assert profile["contest_target"]["status"] == "mismatch"
    assert profile["warnings"]


def test_能力矩阵区分原生探测与通用降级():
    available = {
        "nkvers": "/usr/bin/nkvers",
        "kalert": "/usr/bin/kalert",
        "systemctl": "/usr/bin/systemctl",
        "journalctl": "/usr/bin/journalctl",
        "ip": "/usr/sbin/ip",
        "ss": "/usr/sbin/ss",
        "lsblk": "/usr/bin/lsblk",
        "iostat": "/usr/bin/iostat",
        "file": "/usr/bin/file",
        "readelf": "/usr/bin/readelf",
        "dnf": "/usr/bin/dnf",
    }
    matrix = collect_capability_matrix(which=lambda name: available.get(name))

    assert matrix["strategy"] == "kylin_native_preferred_with_linux_fallback"
    assert matrix["native_available"] == 2
    assert matrix["package_manager"] == "dnf"
    identity = next(item for item in matrix["native_tools"] if item["id"] == "version_query")
    assert identity["invocation"] == "identity_verified"
    assert all(item["available"] for item in matrix["fallback_capabilities"])


async def test_部署就绪度验证源码构建条件():
    available = {
        name: f"/usr/bin/{name}" for name in (
            "systemctl", "journalctl", "ps", "free", "df", "ss", "find", "stat",
            "lsof", "iostat", "ip", "lsblk", "file", "readelf", "node", "npm",
        )
    }
    readiness = await collect_deployment_readiness(
        runner=_identity_runner(), which=lambda name: available.get(name),
        platform_name="linux",
    )

    assert readiness["ready"] is True
    assert readiness["status"] == "ready"
    assert readiness["node"]["meets_minimum"] is True
    assert readiness["blockers"] == []
    json.dumps(readiness, ensure_ascii=False)


async def test_部署就绪度明确列出非目标架构与缺失构建工具():
    available = {
        name: f"/usr/bin/{name}" for name in (
            "systemctl", "journalctl", "ps", "free", "df", "ss", "find", "stat",
        )
    }
    readiness = await collect_deployment_readiness(
        runner=_identity_runner(arch="x86_64"),
        which=lambda name: available.get(name), platform_name="linux",
    )

    assert readiness["ready"] is False
    blockers = " ".join(readiness["blockers"])
    assert "LoongArch64" in blockers
    assert "Node.js" in blockers
    assert "npm" in blockers
