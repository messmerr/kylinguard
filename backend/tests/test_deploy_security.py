from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_privileged_helper白名单缺失时拒绝而非放行():
    helper = (ROOT / "deploy" / "execctl").read_text(encoding="utf-8")
    assert '[ -r "$allow" ] || return 1' in helper
    assert '[ -f "$allow" ] || return 0' not in helper


def test_privileged_helper清理文件绑定目录fd而非检查后按路径rm():
    helper = (ROOT / "deploy" / "execctl").read_text(encoding="utf-8")
    assert "O_NOFOLLOW" in helper
    assert "dir_fd=directory_fd" in helper
    assert "os.unlink(leaf, dir_fd=directory_fd)" in helper
    assert "realpath -m" not in helper
    assert "exec /usr/bin/rm" not in helper


def test_docker运行时使用非root账户():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("FROM python:", 1)[1]
    assert "USER kylinguard" in runtime


def test_compose控制面为只读根文件系统并移除能力():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "    read_only: true\n" in compose
    assert "      - ALL\n" in compose
    assert "      - no-new-privileges:true\n" in compose
    assert "      - /tmp:size=64m,mode=1777\n" in compose
    assert "    pids_limit: 128\n" in compose
    assert "    mem_limit: 1g\n" in compose


def test_systemd允许按sudoers降权但限制可获得能力():
    service = (ROOT / "deploy" / "kylinguard.service").read_text(encoding="utf-8")
    assert "NoNewPrivileges=false" in service
    assert "CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_DAC_OVERRIDE" in service
    assert "AmbientCapabilities=\n" in service
    assert "TasksMax=128" in service
    assert "MemoryMax=1G" in service
