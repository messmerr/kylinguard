import asyncio
import json

import httpx
import pytest

import kylinguard.api as api_module
from kylinguard.api import create_app
from kylinguard.config import Settings


@pytest.fixture()
def app(tmp_path):
    value = create_app(
        Settings(_env_file=None, db_path=str(tmp_path / "kg.db")),
        with_tools=False,
    )
    value.state.llm_config.create_provider(
        name="测试模型",
        adapter="openai_compatible",
        base_url="https://llm.example.test/v1",
        models=[{
            "id": "test-model", "label": "test-model", "enabled": True,
            "supported_efforts": [], "supports_temperature": False,
        }],
    )
    return value


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    )


def _mcp_payload(**updates):
    payload = {
        "id": "demo_mcp",
        "name": "演示 MCP",
        "command": "/bin/echo",
        "args": ["--stdio"],
        "env": {"LOG_LEVEL": "info"},
        "secret_env": {"API_TOKEN": "top-secret-value"},
        # 即便旧界面误传 true，普通保存也不能启动第三方程序。
        "enabled": True,
    }
    payload.update(updates)
    return payload


def _skill_payload(**updates):
    payload = {
        "id": "demo-skill",
        "name": "演示 Skill",
        "description": "只读取磁盘状态。",
        "version": "1.0.0",
        "required_tools": ["sysinfo.disk_usage"],
        "manual_only": True,
        "instructions": "先读取磁盘状态，再根据事实回答。",
        "enabled": True,
    }
    payload.update(updates)
    return payload


async def test_extensions列出随包内置skills且包含可查看正文(app):
    async with _client(app) as client:
        response = await client.get("/api/extensions")

    assert response.status_code == 200
    body = response.json()
    assert body["mcp_servers"] == []
    assert body["enabled_mcp_servers"] == []
    assert {item["id"] for item in body["skills"]} == {
        "disk-space-diagnosis",
        "systemd-service-troubleshooting",
        "security-baseline-inspection",
    }
    assert all(item["source"] == "builtin" for item in body["skills"])
    assert all(item["manual_only"] is False for item in body["skills"])
    assert all(item["instructions"] for item in body["skills"])


async def test_extensions列出当前已启用的内置与第三方mcp(app, monkeypatch):
    app.state.tools_active = True

    async def active_servers():
        return [
            {"id": "sysinfo", "source": "builtin", "tool_count": 4},
            {"id": "demo_mcp", "source": "custom", "tool_count": 2},
        ]

    monkeypatch.setattr(app.state.tools, "active_server_summaries", active_servers)
    created = app.state.mcp_config.create_server(
        server_id="demo_mcp", name="演示 MCP", command="/bin/echo",
        enabled=False,
        updated_by="tester",
    )
    app.state.mcp_config.set_enabled(
        "demo_mcp", enabled=True, updated_by="tester",
        expected_version=created["version"],
    )

    async with _client(app) as client:
        response = await client.get("/api/extensions")

    assert response.status_code == 200
    assert response.json()["enabled_mcp_servers"] == [
        {
            "id": "sysinfo", "name": "系统状态", "source": "builtin",
            "tool_count": 4, "available": True,
        },
        {
            "id": "demo_mcp", "name": "演示 MCP", "source": "custom",
            "tool_count": 2, "available": True,
        },
    ]


async def test_扩展审计范围可在界面查看并校验(app):
    async with _client(app) as client:
        created = await client.post(
            "/api/extensions/mcp", json=_mcp_payload(),
        )
        scopes = await client.get("/api/audit/scopes")
        events = await client.get("/api/sessions/__extensions__/events")
        verified = await client.get("/api/sessions/__extensions__/verify")

    assert created.status_code == 201
    assert any(
        item["id"] == "__extensions__" and item["event_count"] >= 2
        for item in scopes.json()["scopes"]
    )
    assert events.status_code == 200
    assert events.json()["events"][0]["event_type"] == (
        "mcp_server_create_requested"
    )
    assert verified.json() == {"ok": True}


async def test_控制面拒绝非本机host与跨源写请求(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://evil.example",
    ) as remote:
        bad_host = await remote.get("/api/health")

    async with _client(app) as client:
        cross_origin = await client.post(
            "/api/extensions/mcp",
            json=_mcp_payload(),
            headers={"Origin": "http://evil.example"},
        )

    assert bad_host.status_code == 400
    assert cross_origin.status_code == 403
    assert app.state.mcp_config.list_servers() == []


async def test_扩展意图审计失败时绝不修改状态(app, monkeypatch):
    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(app.state.audit, "append", fail_audit)
    async with _client(app) as client:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await client.post(
                "/api/extensions/mcp", json=_mcp_payload(),
            )

    assert app.state.mcp_config.list_servers() == []


async def test_mcp终态审计失败与配置在同一事务回滚(app, monkeypatch):
    real_append = app.state.audit.append
    calls = 0

    def fail_terminal(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("terminal audit unavailable")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(app.state.audit, "append", fail_terminal)
    async with _client(app) as client:
        with pytest.raises(RuntimeError, match="terminal audit unavailable"):
            await client.post(
                "/api/extensions/mcp", json=_mcp_payload(),
            )

    assert app.state.mcp_config.list_servers() == []
    assert [event["event_type"] for event in
            app.state.audit.events("__extensions__")] == [
        "mcp_server_create_requested"
    ]


async def test_mcp工具风险分级绑定当前定义并进入扩展审计(app):
    async with _client(app) as client:
        created_response = await client.post(
            "/api/extensions/mcp", json=_mcp_payload(),
        )
        created = created_response.json()["server"]
        app.state.mcp_config.record_test(
            "demo_mcp",
            ok=True,
            expected_version=created["version"],
            tools=[{
                "name": "lookup",
                "description": "只读查询",
                "input_schema": {"type": "object", "properties": {}},
                "annotations": {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                },
            }],
        )
        listed = await client.get("/api/extensions")
        tool = listed.json()["mcp_servers"][0]["tools"][0]
        assert tool["effective_risk"] == "high"
        assert tool["risk_source"] == "platform_default"
        assert tool["annotations"]["readOnlyHint"] is True

        saved = await client.put(
            "/api/extensions/mcp/demo_mcp/tool-policies",
            json={
                "version": created["version"],
                "policies": {"lookup": {
                    "risk": "low",
                    "definition_sha256": tool["definition_sha256"],
                }},
            },
        )
        assert saved.status_code == 200
        effective = saved.json()["server"]["tools"][0]
        assert effective["effective_risk"] == "low"
        assert effective["risk_source"] == "administrator"
        assert effective["policy_status"] == "active"

        app.state.mcp_config.record_test(
            "demo_mcp",
            ok=True,
            expected_version=saved.json()["server"]["version"],
            tools=[{
                "name": "lookup",
                "description": "定义发生变化",
                "input_schema": {"type": "object", "properties": {}},
            }],
        )
        stale = await client.get("/api/extensions")
        stale_tool = stale.json()["mcp_servers"][0]["tools"][0]
        assert stale_tool["effective_risk"] == "high"
        assert stale_tool["policy_status"] == "stale"

        conflict = await client.put(
            "/api/extensions/mcp/demo_mcp/tool-policies",
            json={
                "version": saved.json()["server"]["version"],
                "policies": {"lookup": {
                    "risk": "low",
                    "definition_sha256": tool["definition_sha256"],
                }},
            },
        )
        assert conflict.status_code == 409

    event_types = [event["event_type"] for event in
                   app.state.audit.events("__extensions__")]
    assert "mcp_tool_policies_update_requested" in event_types
    assert "mcp_tool_policies_updated" in event_types
    terminal = next(
        event for event in app.state.audit.events("__extensions__")
        if event["event_type"] == "mcp_tool_policies_updated"
    )
    assert terminal["payload"]["tool_policies"] == {"lookup": {
        "risk": "low",
        "definition_sha256": tool["definition_sha256"],
    }}


async def test_mcp风险分级终态审计失败会回滚(app, monkeypatch):
    created = app.state.mcp_config.create_server(
        server_id="demo_mcp", name="演示 MCP", command="/bin/echo",
    )
    app.state.mcp_config.record_test(
        "demo_mcp", ok=True, expected_version=created["version"],
        tools=[{
            "name": "lookup", "description": "只读查询",
            "input_schema": {"type": "object", "properties": {}},
        }],
    )
    tool = app.state.mcp_config.get_server("demo_mcp")["tools"][0]
    real_append = app.state.audit.append

    def fail_terminal(session_id, event_type, payload, **kwargs):
        if event_type == "mcp_tool_policies_updated":
            raise RuntimeError("terminal audit unavailable")
        return real_append(session_id, event_type, payload, **kwargs)

    monkeypatch.setattr(app.state.audit, "append", fail_terminal)
    async with _client(app) as client:
        with pytest.raises(RuntimeError, match="terminal audit unavailable"):
            await client.put(
                "/api/extensions/mcp/demo_mcp/tool-policies",
                json={
                    "version": created["version"],
                    "policies": {"lookup": {
                        "risk": "low",
                        "definition_sha256": tool["definition_sha256"],
                    }},
                },
            )

    current = app.state.mcp_config.get_server("demo_mcp")
    assert current["version"] == created["version"]
    assert current["tool_policies"] == {}


async def test_mcp风险策略在写入审计前拒绝非法工具名(app):
    before = len(app.state.audit.events("__extensions__"))
    async with _client(app) as client:
        response = await client.put(
            "/api/extensions/mcp/demo_mcp/tool-policies",
            json={
                "version": 1,
                "policies": {"x" * 10_000: {
                    "risk": "low", "definition_sha256": "a" * 64,
                }},
            },
        )

    assert response.status_code == 422
    assert len(app.state.audit.events("__extensions__")) == before


async def test_mcp保存不启动_秘密不回显_完整生命周期进入审计(
    app, monkeypatch,
):
    calls = []

    async def fake_test(store, server_id, **_kwargs):
        calls.append(server_id)
        config = store.runtime_config(server_id)
        tools = [{
            "name": "lookup",
            "description": "只读查询",
            "input_schema": {"type": "object", "properties": {}},
        }]
        store.record_test(
            server_id, ok=True, tools=tools,
            expected_version=config.version,
        )
        return {"ok": True, "latency_ms": 1,
                "tool_count": 1, "tools": tools}

    monkeypatch.setattr(api_module, "test_configured_stdio_server", fake_test)

    async with _client(app) as client:
        created = await client.post(
            "/api/extensions/mcp", json=_mcp_payload(),
        )
        assert calls == []
        assert created.status_code == 201
        server = created.json()["server"]
        assert server["enabled"] is False
        assert server["secret_env_keys"] == ["API_TOKEN"]
        assert "top-secret-value" not in created.text

        tested = await client.post(
            "/api/extensions/mcp/demo_mcp/test",
            json={"version": server["version"]},
        )
        assert tested.status_code == 200
        assert calls == ["demo_mcp"]

        enabled = await client.post(
            "/api/extensions/mcp/demo_mcp/enabled",
            json={"enabled": True, "version": server["version"]},
        )
        assert enabled.status_code == 200
        server = enabled.json()["server"]
        assert server["enabled"] is True
        assert calls == ["demo_mcp", "demo_mcp"]

        rejected_edit = await client.put(
            "/api/extensions/mcp/demo_mcp",
            json=_mcp_payload(id="", version=server["version"]),
        )
        assert rejected_edit.status_code == 409

        disabled = await client.post(
            "/api/extensions/mcp/demo_mcp/enabled",
            json={"enabled": False, "version": server["version"]},
        )
        assert disabled.status_code == 200
        server = disabled.json()["server"]
        assert server["enabled"] is False

        updated = await client.put(
            "/api/extensions/mcp/demo_mcp",
            json=_mcp_payload(
                id="", version=server["version"], secret_env={},
                clear_secret_env_keys=["API_TOKEN"],
            ),
        )
        assert updated.status_code == 200
        server = updated.json()["server"]
        assert server["secret_env_keys"] == []

        deleted = await client.request(
            "DELETE", "/api/extensions/mcp/demo_mcp",
            json={"version": server["version"]},
        )
        assert deleted.status_code == 200

    serialized = json.dumps(
        app.state.audit.events("__extensions__"), ensure_ascii=False,
    )
    assert "top-secret-value" not in serialized
    assert [event["event_type"] for event in
            app.state.audit.events("__extensions__")] == [
        "mcp_server_create_requested", "mcp_server_created",
        "mcp_server_test_requested", "mcp_server_tested",
        "mcp_server_enable_requested", "mcp_server_enabled",
        "mcp_server_disable_requested", "mcp_server_disabled",
        "mcp_server_update_requested", "mcp_server_updated",
        "mcp_server_delete_requested", "mcp_server_deleted",
    ]


async def test_mcp启用热加载失败会自动恢复停用(app, monkeypatch):
    async def fake_test(_store, _server_id, **_kwargs):
        return {"ok": True, "latency_ms": 1, "tool_count": 0, "tools": []}

    class FailedRuntime:
        async def reload_custom(self):
            return {
                "loaded": [],
                "failed": {"demo_mcp": "握手失败"},
                "disabled": [],
            }

    monkeypatch.setattr(api_module, "test_configured_stdio_server", fake_test)
    app.state.tools_active = True
    app.state.tools = FailedRuntime()

    async with _client(app) as client:
        created = await client.post(
            "/api/extensions/mcp", json=_mcp_payload(),
        )
        version = created.json()["server"]["version"]
        enabled = await client.post(
            "/api/extensions/mcp/demo_mcp/enabled",
            json={"enabled": True, "version": version},
        )
        listed = await client.get("/api/extensions")

    assert enabled.status_code == 400
    assert enabled.json()["detail"]["code"] == "mcp_start_failed"
    server = listed.json()["mcp_servers"][0]
    assert server["enabled"] is False
    assert server["version"] == version + 2
    assert app.state.audit.events("__extensions__")[-1][
        "event_type"
    ] == "mcp_server_enable_failed"


async def test_mcp停用会在持久化前先从运行时路由摘除(app):
    created = app.state.mcp_config.create_server(
        server_id="demo_mcp", name="演示 MCP", command="/bin/echo",
    )
    enabled = app.state.mcp_config.set_enabled(
        "demo_mcp", expected_version=created["version"], enabled=True,
    )
    observations = []

    class DetachingRuntime:
        async def detach_custom(self, server_id):
            observations.append({
                "server_id": server_id,
                "enabled_during_detach": app.state.mcp_config.get_server(
                    server_id,
                )["enabled"],
            })
            return True

    app.state.tools_active = True
    app.state.tools = DetachingRuntime()
    async with _client(app) as client:
        response = await client.post(
            "/api/extensions/mcp/demo_mcp/enabled",
            json={"enabled": False, "version": enabled["version"]},
        )

    assert response.status_code == 200
    assert observations == [{
        "server_id": "demo_mcp", "enabled_during_detach": True,
    }]
    assert app.state.mcp_config.get_server("demo_mcp")["enabled"] is False


async def test_mcp启用请求取消也会摘路由并回滚持久化(app, monkeypatch):
    async def fake_test(_store, _server_id, **_kwargs):
        return {"ok": True, "latency_ms": 1, "tool_count": 0, "tools": []}

    calls = []

    class CancelledRuntime:
        async def reload_custom(self):
            raise asyncio.CancelledError

        async def detach_custom(self, server_id):
            calls.append(server_id)
            return True

    monkeypatch.setattr(api_module, "test_configured_stdio_server", fake_test)
    app.state.tools_active = True
    app.state.tools = CancelledRuntime()

    async with _client(app) as client:
        created = await client.post(
            "/api/extensions/mcp", json=_mcp_payload(),
        )
        version = created.json()["server"]["version"]
        # BaseHTTPMiddleware 将端点级 CancelledError 映射为“无响应”；两者
        # 都表示原请求已取消，安全收敛必须已经完成。
        with pytest.raises((asyncio.CancelledError, RuntimeError)):
            await client.post(
                "/api/extensions/mcp/demo_mcp/enabled",
                json={"enabled": True, "version": version},
            )

    assert calls == ["demo_mcp"]
    assert app.state.mcp_config.get_server("demo_mcp")["enabled"] is False


async def test_skill自定义crud_编辑不暗中停用且审计不含正文(app):
    async with _client(app) as client:
        created = await client.post(
            "/api/extensions/skills", json=_skill_payload(),
        )
        assert created.status_code == 201
        skill = created.json()["skill"]
        assert skill["enabled"] is False

        enabled = await client.post(
            "/api/extensions/skills/demo-skill/enabled",
            json={
                "enabled": True,
                "expected_sha256": skill["sha256"],
                "expected_enabled": False,
            },
        )
        assert enabled.status_code == 200
        skill = enabled.json()["skill"]
        assert skill["enabled"] is True

        updated = await client.put(
            "/api/extensions/skills/demo-skill",
            json=_skill_payload(
                id="", instructions="更新后的只读诊断步骤。",
                expected_sha256=skill["sha256"],
            ),
        )
        assert updated.status_code == 200
        skill = updated.json()["skill"]
        assert skill["enabled"] is True
        assert skill["instructions"] == "更新后的只读诊断步骤。"

        deleted = await client.request(
            "DELETE", "/api/extensions/skills/demo-skill",
            json={
                "expected_sha256": skill["sha256"],
                "expected_enabled": skill["enabled"],
            },
        )
        assert deleted.status_code == 200

        listed = (await client.get("/api/extensions")).json()["skills"]
        builtin = next(
            item for item in listed if item["id"] == "disk-space-diagnosis"
        )
        builtin_delete = await client.request(
            "DELETE", "/api/extensions/skills/disk-space-diagnosis",
            json={
                "expected_sha256": builtin["sha256"],
                "expected_enabled": builtin["enabled"],
            },
        )
        assert builtin_delete.status_code == 409

    audit_text = json.dumps(
        app.state.audit.events("__extensions__"), ensure_ascii=False,
    )
    skill_created = next(
        event for event in app.state.audit.events("__extensions__")
        if event["event_type"] == "skill_created"
    )
    assert skill_created["payload"]["required_tools"] == [
        "sysinfo.disk_usage"
    ]
    assert "allowed_tools" not in skill_created["payload"]
    assert "allow_all_tools" not in skill_created["payload"]
    assert "更新后的只读诊断步骤" not in audit_text
    assert [event["event_type"] for event in
            app.state.audit.events("__extensions__")][:8] == [
        "skill_create_requested", "skill_created",
        "skill_enable_requested", "skill_enabled",
        "skill_update_requested", "skill_updated",
        "skill_delete_requested", "skill_deleted",
    ]


async def test_禁用skill在创建会话前拒绝_启用后透传到pipeline(app):
    class CapturingPipeline:
        def __init__(self):
            self.skill_ids = None
            self.skill_mode = None

        async def handle(
            self, _session_id, _query, emit, *, skill_ids=None,
            skill_mode="auto", **_kwargs,
        ):
            self.skill_ids = list(skill_ids or [])
            self.skill_mode = skill_mode
            await emit({
                "type": "final_answer", "answer": "完成", "aborted": False,
            })

    async with _client(app) as client:
        created = await client.post(
            "/api/extensions/skills", json=_skill_payload(),
        )
        assert created.status_code == 201

        rejected = await client.post("/api/chat", json={
            "message": "检查磁盘", "skill_id": "demo-skill",
        })
        assert rejected.status_code == 409
        assert app.state.sessions.list() == []

        skill = created.json()["skill"]
        await client.post(
            "/api/extensions/skills/demo-skill/enabled",
            json={
                "enabled": True,
                "expected_sha256": skill["sha256"],
                "expected_enabled": False,
            },
        )
        app.state.tools = type("AvailableTools", (), {
            "has_tool": lambda _self, name: name == "sysinfo.disk_usage",
        })()
        pipeline = CapturingPipeline()
        app.state.pipeline = pipeline
        accepted = await client.post("/api/chat", json={
            "message": "检查磁盘", "skill_id": "demo-skill",
        })

    assert accepted.status_code == 200
    assert pipeline.skill_ids == ["demo-skill"]
    assert pipeline.skill_mode == "manual"


async def test_chat多skill和mentions规范化后透传_任一无效则不建会话(app):
    class CapturingPipeline:
        def __init__(self):
            self.payload = None

        async def handle(self, _session_id, query, emit, **kwargs):
            self.payload = {"query": query, **kwargs}
            await emit({
                "type": "final_answer", "answer": "完成", "aborted": False,
            })

    async with _client(app) as client:
        created = []
        for skill_id, name in (
            ("config-review", "配置复核"),
            ("security-check", "安全检查"),
        ):
            response = await client.post(
                "/api/extensions/skills",
                json=_skill_payload(id=skill_id, name=name),
            )
            created.append(response.json()["skill"])
        for skill in created:
            enabled = await client.post(
                f"/api/extensions/skills/{skill['id']}/enabled",
                json={
                    "enabled": True,
                    "expected_sha256": skill["sha256"],
                    "expected_enabled": False,
                },
            )
            assert enabled.status_code == 200

        app.state.tools = type("AvailableTools", (), {
            "has_tool": lambda _self, name: name == "sysinfo.disk_usage",
        })()
        rejected = await client.post("/api/chat", json={
            "message": "组合检查",
            "skill_mode": "manual",
            "skill_ids": ["config-review", "missing"],
        })
        assert rejected.status_code == 404
        assert app.state.sessions.list() == []

        workspace = app.state.skills.user_dir.parent
        config = workspace / "config"
        config.mkdir(exist_ok=True)
        (config / "app.yaml").write_text("safe: true\n", encoding="utf-8")
        pipeline = CapturingPipeline()
        app.state.pipeline = pipeline
        mismatched = await client.post("/api/chat", json={
            "message": "这里没有可见标签",
            "workspace_root": str(workspace),
            "skill_ids": ["config-review"],
            "context_mentions": [{
                "type": "skill", "offset": 0,
                "skill_id": "config-review",
            }],
        })
        assert mismatched.status_code == 400
        assert mismatched.json()["detail"]["code"] == (
            "context_mentions_invalid"
        )
        assert app.state.sessions.list() == []

        message = "😀@配置复核 @安全检查 请检查 @app.yaml"
        security_offset = message.index("@安全检查")
        file_offset = message.index("@app.yaml")
        accepted = await client.post("/api/chat", json={
            "message": message,
            "workspace_root": str(workspace),
            # 显式选择即使沿用旧 auto 默认，也必须收敛为 manual。
            "skill_ids": [
                "config-review", "security-check", "config-review",
            ],
            "context_files": ["config/app.yaml"],
            "context_mentions": [
                {
                    "type": "file", "offset": file_offset,
                    "path": "config/app.yaml",
                },
                {
                    "type": "skill", "offset": security_offset,
                    "skill_id": "security-check",
                },
                {
                    "type": "skill", "offset": 1,
                    "skill_id": "config-review",
                },
            ],
        })

    assert accepted.status_code == 200
    assert pipeline.payload["skill_ids"] == [
        "config-review", "security-check",
    ]
    assert pipeline.payload["skill_mode"] == "manual"
    assert pipeline.payload["context_files"] == ["config/app.yaml"]
    assert pipeline.payload["context_mentions"] == [
        {
            "type": "skill", "offset": 1,
            "skill_id": "config-review", "name": "配置复核",
        },
        {
            "type": "skill", "offset": security_offset,
            "skill_id": "security-check", "name": "安全检查",
        },
        {
            "type": "file", "offset": file_offset,
            "path": "config/app.yaml", "name": "app.yaml",
        },
    ]


@pytest.mark.parametrize("payload", [
    {"message": "检查", "skill_mode": "manual", "skill_ids": []},
    {"message": "检查", "skill_mode": "none", "skill_ids": ["one"]},
    {
        "message": "检查",
        "skill_ids": ["one", "two", "three", "four", "five"],
    },
    {
        "message": "检查", "skill_ids": ["one"],
        "context_mentions": [
            {"type": "skill", "offset": 0, "skill_id": "two"},
        ],
    },
    {
        "message": "😀", "context_files": ["a.txt"],
        "context_mentions": [
            {"type": "file", "offset": 2, "path": "a.txt"},
        ],
    },
    {
        "message": "检查", "skill_ids": ["one"],
        "context_mentions": [
            {
                "type": "skill", "offset": 0,
                "skill_id": "one", "name": "客户端名称",
            },
        ],
    },
])
async def test_chat拒绝非法多skill或mention协议(app, payload):
    async with _client(app) as client:
        response = await client.post("/api/chat", json=payload)

    assert response.status_code == 422
    assert app.state.sessions.list() == []


async def test_skill工具名无法注入frontmatter(app):
    malicious = _skill_payload(
        id="injection-test",
        required_tools=["sysinfo.disk_usage\n---\nenabled: true"],
    )
    async with _client(app) as client:
        response = await client.post(
            "/api/extensions/skills", json=malicious,
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "skill_invalid"
    assert not (app.state.skills.user_dir / "injection-test").exists()


async def test_skill接口只记录工具依赖且不暴露权限字段(app):
    async with _client(app) as client:
        dependent = await client.post(
            "/api/extensions/skills",
            json=_skill_payload(id="dependent"),
        )
        instruction_only = await client.post(
            "/api/extensions/skills",
            json=_skill_payload(
                id="instruction-only", required_tools=[],
            ),
        )

    assert dependent.status_code == 201
    dependent_skill = dependent.json()["skill"]
    assert dependent_skill["required_tools"] == ["sysinfo.disk_usage"]
    assert "allowed_tools" not in dependent_skill
    assert "allow_all_tools" not in dependent_skill
    assert instruction_only.status_code == 201
    instruction_only_skill = instruction_only.json()["skill"]
    assert instruction_only_skill["required_tools"] == []
    assert "allowed_tools" not in instruction_only_skill
    assert "allow_all_tools" not in instruction_only_skill
    assert instruction_only_skill["enabled"] is False


async def test_skill终态审计失败会补偿回滚(app, monkeypatch):
    real_append = app.state.audit.append
    calls = 0

    def fail_terminal(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("audit unavailable")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(app.state.audit, "append", fail_terminal)
    async with _client(app) as client:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await client.post(
                "/api/extensions/skills", json=_skill_payload(),
            )

    assert not (app.state.skills.user_dir / "demo-skill").exists()
    assert [event["event_type"] for event in
            app.state.audit.events("__extensions__")] == [
        "skill_create_requested"
    ]


async def test_skill更新启停删除的终态审计失败均恢复原状态(app, monkeypatch):
    async with _client(app) as client:
        created = await client.post(
            "/api/extensions/skills", json=_skill_payload(),
        )
    original = created.json()["skill"]
    real_append = app.state.audit.append
    fail_on = ""

    def fail_selected(session_id, event_type, payload, **kwargs):
        if event_type == fail_on:
            raise RuntimeError(f"{event_type} unavailable")
        return real_append(session_id, event_type, payload, **kwargs)

    monkeypatch.setattr(app.state.audit, "append", fail_selected)
    async with _client(app) as client:
        fail_on = "skill_updated"
        with pytest.raises(RuntimeError, match="skill_updated"):
            await client.put(
                "/api/extensions/skills/demo-skill",
                json=_skill_payload(
                    id="", expected_sha256=original["sha256"],
                    instructions="不应留下的更新。",
                ),
            )
        after_update = (await client.get("/api/extensions")).json()["skills"]
        restored = next(item for item in after_update
                        if item["id"] == "demo-skill")
        assert restored["instructions"] == original["instructions"]
        assert restored["sha256"] == original["sha256"]

        fail_on = "skill_enabled"
        with pytest.raises(RuntimeError, match="skill_enabled"):
            await client.post(
                "/api/extensions/skills/demo-skill/enabled",
                json={
                    "enabled": True,
                    "expected_sha256": restored["sha256"],
                    "expected_enabled": False,
                },
            )
        after_enable = (await client.get("/api/extensions")).json()["skills"]
        restored = next(item for item in after_enable
                        if item["id"] == "demo-skill")
        assert restored["enabled"] is False

        fail_on = "skill_deleted"
        with pytest.raises(RuntimeError, match="skill_deleted"):
            await client.request(
                "DELETE", "/api/extensions/skills/demo-skill",
                json={
                    "expected_sha256": restored["sha256"],
                    "expected_enabled": False,
                },
            )
        after_delete = (await client.get("/api/extensions")).json()["skills"]

    restored = next(item for item in after_delete
                    if item["id"] == "demo-skill")
    assert restored["sha256"] == original["sha256"]
    assert restored["enabled"] is False
