import ast
import os
import runpy
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from kylinguard.api import create_app
from kylinguard.config import Settings


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
HELPER = DEPLOY / "kylinguard-privileged"


@pytest.fixture(scope="module")
def helper_namespace():
    return runpy.run_path(str(HELPER))


def test_生产环境文件明确双账户和前端目录():
    environment = (DEPLOY / "kylinguard.env").read_text(encoding="utf-8")
    service = (DEPLOY / "kylinguard.service").read_text(encoding="utf-8")

    assert "KG_EXEC_USER=kylinguard-exec" in environment
    assert "KG_FRONTEND_DIST=/opt/kylinguard/current/frontend" in environment
    assert "KG_DB_PATH=/var/lib/kylinguard/db/kylinguard.db" in environment
    assert "User=kylinguard" in service
    assert "Group=kylinguard" in service
    assert "--host 127.0.0.1 --port 8000" in service
    assert "User=root" not in service
    assert "NoNewPrivileges=true" not in service


def test_sudoers_只开放执行账户和固定_helper():
    sudoers = (DEPLOY / "sudoers-kylinguard").read_text(encoding="utf-8")

    assert "kylinguard ALL=(kylinguard-exec) NOPASSWD: ALL" in sudoers
    assert (
        "kylinguard ALL=(root) NOPASSWD: KYLINGUARD_PRIVILEGED"
        in sudoers
    )
    root_lines = [
        line.strip()
        for line in sudoers.splitlines()
        if line.strip().startswith("kylinguard ") and "(root)" in line
    ]
    assert root_lines == [
        "kylinguard ALL=(root) NOPASSWD: KYLINGUARD_PRIVILEGED"
    ]


def test_helper_不使用_shell或subprocess():
    source = HELPER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    calls = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }

    assert "subprocess" not in imported
    assert "os.system" not in calls
    assert "os.popen" not in calls
    assert 'SYSTEMCTL = "/usr/bin/systemctl"' in source
    assert "renameat2" in source
    assert "O_NOFOLLOW" in source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("nginx", "nginx.service"),
        ("sshd.service", "sshd.service"),
        ("worker@1.service", "worker@1.service"),
    ],
)
def test_helper_服务名规范化(helper_namespace, raw, expected):
    assert helper_namespace["normalize_service"](raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "kylinguard.service",
        "reboot.target",
        "multi-user.target",
        "--now",
        "bad/name.service",
        "name;id.service",
    ],
)
def test_helper_拒绝越权或非服务单元(helper_namespace, raw):
    with pytest.raises(helper_namespace["Refused"]):
        helper_namespace["normalize_service"](raw)


@pytest.mark.parametrize(
    "raw",
    [
        "/etc/passwd",
        "/root/secret",
        "/var/lib/kylinguard/db/kylinguard.db",
        "/var/log",
        "relative.log",
    ],
)
def test_helper_拒绝清理白名单外路径(helper_namespace, raw):
    with pytest.raises(helper_namespace["Refused"]):
        helper_namespace["normalize_clean_path"](raw)


@pytest.mark.parametrize(
    "raw",
    [
        "/tmp/a.log",
        "/var/tmp/a.log",
        "/var/cache/app/a.cache",
        "/var/log/app/a.log",
    ],
)
def test_helper_接受白名单内普通路径语法(helper_namespace, raw):
    assert helper_namespace["normalize_clean_path"](raw) == raw


@pytest.mark.skipif(os.name != "posix", reason="需要 Linux openat/renameat2")
def test_helper_真实删除只作用于已绑定普通文件(helper_namespace, tmp_path):
    if not str(tmp_path).startswith("/tmp/"):
        pytest.skip("pytest 临时目录不在 helper 的 /tmp 白名单")
    target = tmp_path / "delete-me.log"
    target.write_text("test", encoding="utf-8")

    helper_namespace["clean_file"](str(target))

    assert not target.exists()


@pytest.mark.skipif(os.name != "posix", reason="需要 Linux O_NOFOLLOW")
def test_helper_拒绝符号链接(helper_namespace, tmp_path):
    if not str(tmp_path).startswith("/tmp/"):
        pytest.skip("pytest 临时目录不在 helper 的 /tmp 白名单")
    real = tmp_path / "real.log"
    link = tmp_path / "link.log"
    real.write_text("keep", encoding="utf-8")
    link.symlink_to(real)

    with pytest.raises(helper_namespace["Refused"]):
        helper_namespace["clean_file"](str(link))

    assert real.read_text(encoding="utf-8") == "keep"


async def test_安装路径可通过配置挂载前端(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text(
        "<!doctype html><title>installed</title>",
        encoding="utf-8",
    )
    app = create_app(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "state" / "kylinguard.db"),
            frontend_dist=str(frontend),
        ),
        with_tools=False,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "<title>installed</title>" in response.text


def test_LoongArch_依赖约束与V11_Rust基线相容():
    constraints = (
        DEPLOY / "constraints-kylin-v11.txt"
    ).read_text(encoding="utf-8")
    assert "pydantic==2.11.4" in constraints
    assert "pydantic-core==2.33.2" in constraints
    assert "jiter==0.10.0" in constraints
    assert "rpds-py==0.27.1" in constraints
    assert "cryptography==46.0.7" in constraints
    assert "maturin==1.9.4" in constraints

    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "--build-constraint" in installer
    assert "'pip>=25.3,<27'" in installer


def test_安装卸载脚本通过bash语法检查():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("当前测试环境没有 bash")
    for script in (
        ROOT / "install.sh",
        ROOT / "uninstall.sh",
        DEPLOY / "verify-install.sh",
    ):
        subprocess.run([bash, "-n", str(script)], check=True)


def test_卸载递归删除目标全部是固定绝对路径():
    source = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
    assert "safe_remove_tree" in source
    assert "rm -rf -- \"$1\"" in source
    assert "rm -rf /" not in source
    assert "rm -rf -- \"$HOME\"" not in source
