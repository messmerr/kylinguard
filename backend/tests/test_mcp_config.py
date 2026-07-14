import asyncio
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import kylinguard.mcp_config as mcp_config_module
from kylinguard.mcp_client import (
    MCPConnectionError,
    ToolCallError,
    ToolManager,
    _ManagedServer,
    custom_server_parameters,
    test_configured_stdio_server as run_configured_stdio_test,
    test_stdio_server as run_stdio_test,
)
from kylinguard.mcp_config import (
    MCPConfigError,
    MCPConfigStore,
    MCPConfigVersionConflict,
    make_stdio_server_config,
    normalize_discovered_tools,
    redact_discovered_tool_secrets,
    redact_mcp_error,
)


@pytest.fixture
def mcp_store(tmp_path):
    store = MCPConfigStore(
        str(tmp_path / "control.db"), tmp_path / "mcp-secrets")
    yield store
    store.close()


def _create(store, **overrides):
    values = {
        "server_id": "demo_mcp",
        "name": "Demo MCP",
        "command": sys.executable,
        "args": ["-m", "kylinguard.plugins.sysinfo"],
    }
    values.update(overrides)
    return store.create_server(**values)


def test_旧版数据库自动迁移并补齐cwd(tmp_path):
    db_path = tmp_path / "legacy.db"
    legacy_schema = mcp_config_module._SCHEMA.replace(
        "    cwd TEXT NOT NULL DEFAULT '',\n", "",
    )
    connection = sqlite3.connect(db_path)
    connection.executescript(legacy_schema)
    connection.execute(
        "INSERT INTO custom_mcp_servers(id, name, command, args_json, "
        "env_json, secret_env_ref, secret_env_keys_json, enabled, version, "
        "created_at, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "legacy_mcp", "Legacy MCP", sys.executable, "[]", "{}", "", "[]",
            0, 1, 1.0, 1.0, "legacy",
        ),
    )
    connection.commit()
    connection.close()

    store = MCPConfigStore(db_path, tmp_path / "legacy-secrets")
    try:
        assert store.get_server("legacy_mcp")["cwd"] == str(
            Path(sys.executable).parent.resolve()
        )
        assert store.runtime_config("legacy_mcp").cwd == str(
            Path(sys.executable).parent.resolve()
        )
    finally:
        store.close()


def test_配置持久化区分普通环境与只写秘密(mcp_store, tmp_path):
    working_directory = tmp_path / "mcp-work"
    working_directory.mkdir()
    public = _create(
        mcp_store,
        cwd=str(working_directory.resolve()),
        env={"LOG_LEVEL": "info"},
        secret_env={"SERVICE_TOKEN": "top-secret-value"},
    )

    assert public["enabled"] is False
    assert public["cwd"] == str(working_directory.resolve())
    assert public["env"] == {"LOG_LEVEL": "info"}
    assert public["secret_env_keys"] == ["SERVICE_TOKEN"]
    assert "top-secret-value" not in json.dumps(public)
    runtime = mcp_store.runtime_config("demo_mcp")
    assert runtime.cwd == str(working_directory.resolve())
    assert runtime.secret_env == {"SERVICE_TOKEN": "top-secret-value"}

    row = sqlite3.connect(tmp_path / "control.db").execute(
        "SELECT env_json, secret_env_ref, secret_env_keys_json, cwd "
        "FROM custom_mcp_servers"
    ).fetchone()
    assert "top-secret-value" not in " ".join(row)
    assert row[3] == str(working_directory.resolve())
    secret_path = tmp_path / "mcp-secrets" / row[1]
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600


def test_秘密留空保留且只能用明确键清除(mcp_store):
    _create(mcp_store, secret_env={"SERVICE_TOKEN": "old-value"})
    updated = mcp_store.update_server(
        "demo_mcp",
        expected_version=1,
        name="Demo MCP",
        command=sys.executable,
        args=["-m", "kylinguard.plugins.sysinfo"],
        env={"LOG_LEVEL": "debug"},
        secret_env={"SERVICE_TOKEN": ""},
        enabled=True,
    )
    assert updated["version"] == 2
    assert mcp_store.runtime_config("demo_mcp").secret_env == {
        "SERVICE_TOKEN": "old-value",
    }

    cleared = mcp_store.update_server(
        "demo_mcp",
        expected_version=2,
        name="Demo MCP",
        command=sys.executable,
        args=["-m", "kylinguard.plugins.sysinfo"],
        env={"LOG_LEVEL": "debug"},
        enabled=True,
        clear_secret_env_keys=["SERVICE_TOKEN"],
    )
    assert cleared["secret_env_keys"] == []
    assert mcp_store.runtime_config("demo_mcp").secret_env == {}
    with pytest.raises(MCPConfigVersionConflict):
        mcp_store.set_enabled(
            "demo_mcp", expected_version=2, enabled=False)


@pytest.mark.parametrize("values, code", [
    ({"server_id": "sysinfo"}, "mcp_server_id_reserved"),
    ({"server_id": "Bad.Id"}, "mcp_server_id_invalid"),
    ({"command": "python"}, "mcp_command_must_be_absolute"),
    ({"command": "/definitely/not/executable"}, "mcp_command_not_executable"),
    ({"args": "-m unsafe"}, "mcp_args_invalid"),
    ({"args": ["--token=plain-text"]}, "mcp_arg_secret_misclassified"),
    ({"env": {"PATH": "/tmp"}}, "mcp_env_key_reserved"),
    ({"env": {"API_TOKEN": "plain-text"}}, "mcp_env_secret_misclassified"),
])
def test_启动边界严格校验(mcp_store, values, code):
    with pytest.raises(MCPConfigError) as caught:
        _create(mcp_store, **values)
    assert caught.value.code == code


def test_cwd默认命令目录且拒绝非规范路径与符号链接(tmp_path):
    default = make_stdio_server_config(
        server_id="default_cwd", name="Default cwd", command=sys.executable,
    )
    assert default.cwd == str(Path(sys.executable).parent.resolve())

    working = tmp_path / "working"
    working.mkdir()
    explicit = make_stdio_server_config(
        server_id="explicit_cwd", name="Explicit cwd", command=sys.executable,
        cwd=str(working.resolve()),
    )
    assert explicit.cwd == str(working.resolve())
    assert str(custom_server_parameters(explicit).cwd) == explicit.cwd

    cases = [
        ("relative/path", "mcp_cwd_must_be_absolute"),
        (str(tmp_path / "missing"), "mcp_cwd_not_directory"),
        (str(working / ".." / working.name), "mcp_cwd_not_normalized"),
    ]
    link = tmp_path / "working-link"
    try:
        link.symlink_to(working, target_is_directory=True)
    except OSError:
        pass
    else:
        cases.append((str(link), "mcp_cwd_not_normalized"))

    for index, (cwd, code) in enumerate(cases):
        with pytest.raises(MCPConfigError) as caught:
            make_stdio_server_config(
                server_id=f"bad_cwd_{index}", name="Bad cwd",
                command=sys.executable, cwd=cwd,
            )
        assert caught.value.code == code


def test_mcp状态与终态审计可在同一事务提交(mcp_store):
    mcp_store._conn.execute(
        "CREATE TABLE audit_probe(event TEXT, server_id TEXT, version INTEGER)"
    )
    mcp_store._conn.commit()

    def audit(event):
        def append(value, connection):
            connection.execute(
                "INSERT INTO audit_probe(event, server_id, version) "
                "VALUES (?,?,?)",
                (event, value["id"], value["version"]),
            )
        return append

    created = _create(mcp_store, audit=audit("created"))
    updated = mcp_store.update_server(
        "demo_mcp", expected_version=created["version"],
        name="Updated MCP", command=sys.executable,
        args=["-m", "kylinguard.plugins.sysinfo"], env={}, enabled=False,
        audit=audit("updated"),
    )
    enabled = mcp_store.set_enabled(
        "demo_mcp", expected_version=updated["version"], enabled=True,
        audit=audit("enabled"),
    )
    mcp_store.delete_server(
        "demo_mcp", expected_version=enabled["version"],
        audit=audit("deleted"),
    )

    assert mcp_store.list_servers() == []
    events = [tuple(row) for row in mcp_store._conn.execute(
        "SELECT event, server_id, version FROM audit_probe ORDER BY rowid"
    ).fetchall()]
    assert events == [
        ("created", "demo_mcp", 1),
        ("updated", "demo_mcp", 2),
        ("enabled", "demo_mcp", 3),
        ("deleted", "demo_mcp", 3),
    ]


def test_终态审计失败会回滚配置与秘密文件(mcp_store):
    mcp_store._conn.execute("CREATE TABLE audit_probe(event TEXT)")
    mcp_store._conn.commit()

    def fail(_value, connection):
        connection.execute("INSERT INTO audit_probe(event) VALUES ('attempt')")
        raise RuntimeError("audit unavailable")

    with pytest.raises(RuntimeError, match="audit unavailable"):
        _create(
            mcp_store, secret_env={"SERVICE_TOKEN": "top-secret-value"},
            audit=fail,
        )
    assert mcp_store.list_servers() == []
    assert list(mcp_store.secrets.directory.iterdir()) == []
    assert mcp_store._conn.execute("SELECT * FROM audit_probe").fetchall() == []

    created = _create(
        mcp_store, secret_env={"SERVICE_TOKEN": "top-secret-value"},
    )
    with pytest.raises(RuntimeError, match="audit unavailable"):
        mcp_store.update_server(
            "demo_mcp", expected_version=created["version"],
            name="Should roll back", command=sys.executable,
            args=[], env={}, enabled=False, audit=fail,
        )
    assert mcp_store.get_server("demo_mcp")["name"] == "Demo MCP"
    assert mcp_store.get_server("demo_mcp")["version"] == 1

    with pytest.raises(RuntimeError, match="audit unavailable"):
        mcp_store.set_enabled(
            "demo_mcp", expected_version=1, enabled=True, audit=fail,
        )
    assert mcp_store.get_server("demo_mcp")["enabled"] is False
    assert mcp_store.get_server("demo_mcp")["version"] == 1

    with pytest.raises(RuntimeError, match="audit unavailable"):
        mcp_store.delete_server("demo_mcp", expected_version=1, audit=fail)
    assert mcp_store.get_server("demo_mcp")["version"] == 1
    assert mcp_store.runtime_config("demo_mcp").secret_env == {
        "SERVICE_TOKEN": "top-secret-value",
    }
    assert mcp_store._conn.execute("SELECT * FROM audit_probe").fetchall() == []


def test_错误按已知值与常见凭据形态脱敏():
    safe = redact_mcp_error(
        "failed token=visible Bearer abc.def endpoint secret-value",
        ["secret-value"],
    )
    assert "visible" not in safe
    assert "abc.def" not in safe
    assert "secret-value" not in safe
    assert safe.count("[REDACTED]") >= 3


def test_成功输出脱敏不沿用错误长度上限():
    value = "x" * 3000 + "top-secret-value" + "y" * 1000
    safe = redact_mcp_error(
        value, ["top-secret-value"], max_chars=None,
    )

    assert "top-secret-value" not in safe
    assert "[REDACTED]" in safe
    assert len(safe) > 4000


def test_第三方服务只获得最小环境和显式配置(monkeypatch):
    monkeypatch.setenv("KG_LLM_API_KEY", "control-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@proxy")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("PATH", "/tmp/untrusted-bin")
    config = make_stdio_server_config(
        server_id="external",
        name="External",
        command=sys.executable,
        env={"LOG_LEVEL": "info"},
        secret_env={"SERVICE_TOKEN": "explicit-secret"},
    )

    params = custom_server_parameters(config)
    assert params.command == sys.executable
    assert params.args == []
    assert str(params.cwd) == str(Path(sys.executable).parent)
    assert params.env["LOG_LEVEL"] == "info"
    assert params.env["SERVICE_TOKEN"] == "explicit-secret"
    assert params.env["PATH"].split(os.pathsep)[0] == str(Path(sys.executable).parent)
    assert "/tmp/untrusted-bin" not in params.env["PATH"]
    assert "KG_LLM_API_KEY" not in params.env
    assert "HTTPS_PROXY" not in params.env
    assert "SSH_AUTH_SOCK" not in params.env

    isolated = custom_server_parameters(config, exec_user="kylinguard-exec")
    assert isolated.command == "sudo"
    assert isolated.args == [
        "-n", "-E", "-H", "-u", "kylinguard-exec", "--",
        sys.executable,
    ]
    assert isolated.env["SERVICE_TOKEN"] == "explicit-secret"
    assert "KG_LLM_API_KEY" not in isolated.env


def test_畸形第三方schema在进入目录前被拒绝():
    with pytest.raises(MCPConfigError, match="enum"):
        normalize_discovered_tools([{
            "name": "bad",
            "input_schema": {
                "type": "object",
                "properties": {"mode": {"enum": None}},
            },
        }])


def test_工具描述和schema不能回显该mcp的裸密密():
    cleaned = redact_discovered_tool_secrets([{
        "name": "lookup",
        "description": "token is top-secret-value",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string", "default": "top-secret-value",
                },
            },
        },
    }], ["top-secret-value"])

    rendered = json.dumps(cleaned, ensure_ascii=False)
    assert "top-secret-value" not in rendered
    assert rendered.count("[REDACTED]") == 2
    with pytest.raises(MCPConfigError, match="type"):
        normalize_discovered_tools([{
            "name": "bad-type",
            "input_schema": {
                "type": "object",
                "properties": {"mode": {"type": ["string", 1]}},
            },
        }])


async def test_存量配置可测试并持久化工具发现(mcp_store):
    _create(mcp_store)
    result = await run_configured_stdio_test(mcp_store, "demo_mcp")

    assert result["ok"] is True
    assert result["tool_count"] >= 1
    assert any(tool["name"] == "disk_usage" for tool in result["tools"])
    public = mcp_store.get_server("demo_mcp")
    assert public["status"] == "connected"
    assert public["tool_count"] == result["tool_count"]
    assert public["tools"] == result["tools"]


async def test_测试失败只暴露脱敏错误(tmp_path):
    script = tmp_path / "broken.py"
    script.write_text(
        "import os, sys\n"
        "sys.stderr.write('SERVICE_TOKEN=' + os.environ['SERVICE_TOKEN'])\n"
        "raise RuntimeError(os.environ['SERVICE_TOKEN'])\n",
        encoding="utf-8",
    )
    config = make_stdio_server_config(
        server_id="broken",
        name="Broken",
        command=sys.executable,
        args=[str(script)],
        secret_env={"SERVICE_TOKEN": "never-expose-this"},
    )

    with pytest.raises(MCPConnectionError) as caught:
        await run_stdio_test(config, timeout=3)
    assert "never-expose-this" not in caught.value.message


def _write_slow_server(path: Path) -> None:
    path.write_text(
        "import asyncio, json, os\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "mcp = FastMCP('slow-test')\n"
        "@mcp.tool()\n"
        "async def pause(delay: float = 0.2) -> str:\n"
        "    await asyncio.sleep(delay)\n"
        "    return 'done'\n"
        "@mcp.tool()\n"
        "def environment_boundary() -> str:\n"
        "    return json.dumps({\n"
        "        'explicit_regular': 'LOG_LEVEL' in os.environ,\n"
        "        'explicit_secret': 'SERVICE_TOKEN' in os.environ,\n"
        "        'parent_control': 'KG_PARENT_SECRET' in os.environ,\n"
        "        'parent_proxy': 'HTTPS_PROXY' in os.environ,\n"
        "    })\n"
        "if __name__ == '__main__':\n"
        "    mcp.run()\n",
        encoding="utf-8",
    )


async def test_热加载不打断进行中的调用且不会继承父进程秘密(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("KG_PARENT_SECRET", "control-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy-secret")
    script = tmp_path / "slow_mcp.py"
    _write_slow_server(script)
    config = make_stdio_server_config(
        server_id="slow_test",
        name="Slow test",
        command=sys.executable,
        args=[str(script)],
        env={"LOG_LEVEL": "info"},
        secret_env={"SERVICE_TOKEN": "server-secret"},
        enabled=True,
    )
    manager = ToolManager(custom_start_timeout=5)
    loaded = await manager.reload_custom([config])
    assert loaded["loaded"] == ["slow_test"]
    assert manager.has_tool("slow_test.pause")

    boundary = json.loads(
        await manager.call("slow_test", "environment_boundary", {}))
    assert boundary == {
        "explicit_regular": True,
        "explicit_secret": True,
        "parent_control": False,
        "parent_proxy": False,
    }

    call = asyncio.create_task(
        manager.call("slow_test", "pause", {"delay": 0.25}))
    await asyncio.sleep(0.05)
    reload_task = asyncio.create_task(manager.reload_custom([]))
    await asyncio.sleep(0.05)
    assert reload_task.done() is False
    assert await call == "done"
    result = await reload_task
    assert result["disabled"] == ["slow_test"]
    assert manager.has_tool("slow_test.pause") is False
    await manager.stop()


async def test_call_checked兼容只有替身session的测试双():
    class Session:
        async def call_tool(self, _tool, _arguments):
            return SimpleNamespace(
                content=[SimpleNamespace(text="fake-result")],
                isError=False,
            )

    manager = ToolManager()
    manager._sessions["fake"] = Session()

    assert await manager.call_checked("fake", "lookup", {}) == "fake-result"
    with pytest.raises(ToolCallError, match="配置在规划或确认后已变更"):
        await manager.call_checked(
            "fake", "lookup", {}, expected_identity="sha256:stale",
        )
    await manager.stop()


async def test_detach_custom先摘除路由再后台等待在途调用():
    entered = asyncio.Event()
    release = asyncio.Event()

    class Session:
        async def call_tool(self, _tool, _arguments):
            entered.set()
            await release.wait()
            return SimpleNamespace(
                content=[SimpleNamespace(text="done")], isError=False,
            )

    manager = ToolManager(custom_call_timeout=2)
    handle = _ManagedServer(
        name="external", session=Session(),
        tools=[{"name": "pause", "description": "", "input_schema": {}}],
        custom=True, config_version=1,
    )
    manager._handles["external"] = handle
    manager._sessions["external"] = handle.session
    manager._custom_ids.add("external")
    manager._catalog_lines["external"] = manager._lines_for(handle)
    manager._rebuild_catalog()

    call = asyncio.create_task(manager.call("external", "pause", {}))
    await entered.wait()
    assert manager.has_tool("external.pause") is True

    assert await manager.detach_custom("external") is True
    assert manager.has_tool("external.pause") is False
    assert "external" not in manager._sessions
    with pytest.raises(ToolCallError, match="未知工具服务器"):
        await manager.call("external", "pause", {})

    release.set()
    assert await call == "done"
    await manager.stop()


async def test_运行时输出脱敏裸密密且保留长文本():
    class Session:
        async def call_tool(self, _tool, _arguments):
            return SimpleNamespace(
                content=[SimpleNamespace(
                    text="x" * 3000 + "top-secret-value" + "y" * 1000,
                )],
                isError=False,
            )

    manager = ToolManager(custom_call_timeout=1)
    handle = _ManagedServer(
        name="external", session=Session(),
        tools=[{"name": "lookup", "description": "", "input_schema": {}}],
        custom=True, config_version=7,
        secret_values=("top-secret-value",),
    )
    manager._handles["external"] = handle
    manager._sessions["external"] = handle.session
    manager._custom_ids.add("external")

    output = await manager.call("external", "lookup", {})

    assert "top-secret-value" not in output
    assert "[REDACTED]" in output
    assert len(output) > 4000
    await manager.stop()


async def test_自定义工具的structured_content会作为安全json返回():
    class Session:
        async def call_tool(self, _tool, _arguments):
            return SimpleNamespace(
                content=[], isError=False,
                structuredContent={
                    "count": 2, "token": "top-secret-value",
                },
            )

    manager = ToolManager(custom_call_timeout=1)
    handle = _ManagedServer(
        name="external", session=Session(),
        tools=[{"name": "lookup", "description": "", "input_schema": {}}],
        custom=True, config_version=1,
        secret_values=("top-secret-value",),
    )
    manager._handles["external"] = handle
    manager._sessions["external"] = handle.session
    manager._custom_ids.add("external")

    output = await manager.call("external", "lookup", {})

    assert json.loads(output) == {"count": 2, "token": "[REDACTED]"}
    await manager.stop()


async def test_自定义工具输出受字节上限约束():
    class Session:
        async def call_tool(self, _tool, _arguments):
            return SimpleNamespace(
                content=[SimpleNamespace(text="汉" * 3000)],
                isError=False,
            )

    manager = ToolManager(custom_call_timeout=1, output_max_bytes=4096)
    handle = _ManagedServer(
        name="external", session=Session(),
        tools=[{"name": "large", "description": "", "input_schema": {}}],
        custom=True, config_version=1,
    )
    manager._handles["external"] = handle
    manager._sessions["external"] = handle.session
    manager._custom_ids.add("external")

    output = await manager.call("external", "large", {})

    assert len(output.encode("utf-8")) <= 4096
    assert "已按 4096 字节上限截断" in output
    await manager.stop()


async def test_自定义工具超时会摘除死会话():
    class Session:
        async def call_tool(self, _tool, _arguments):
            await asyncio.Future()

    manager = ToolManager(custom_call_timeout=0.01)
    handle = _ManagedServer(
        name="external", session=Session(),
        tools=[{"name": "hang", "description": "", "input_schema": {}}],
        custom=True, config_version=1,
    )
    manager._handles["external"] = handle
    manager._sessions["external"] = handle.session
    manager._custom_ids.add("external")

    with pytest.raises(ToolCallError, match="超时"):
        await manager.call("external", "hang", {})

    assert not manager.has_tool("external.hang")
    await manager.stop()
