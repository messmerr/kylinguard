import asyncio
import json
import os
import stat

import httpx
import pytest

from kylinguard.api import create_app
from kylinguard.audit import AuditError
from kylinguard.config import Settings
from kylinguard.llm import LLMClient
from kylinguard.llm_config import (
    LLMConfigError,
    LLMConfigStore,
    LLMRuntime,
    ModelSelection,
)


def _settings(tmp_path, **overrides):
    return Settings(
        _env_file=None,
        db_path=str(tmp_path / "kg.db"),
        llm_secrets_dir=str(tmp_path / "control-secrets"),
        admin_password="test-password",
        **overrides,
    )


def _model(model_id="agent-model", efforts=None):
    return {
        "id": model_id,
        "label": model_id,
        "enabled": True,
        "supported_efforts": efforts or [],
        "supports_temperature": False,
    }


def test环境变量不再生成提供商且旧绑定会在启动时清理(tmp_path, monkeypatch):
    monkeypatch.setenv("KG_LLM_BASE_URL", "https://legacy.example.test/v1")
    monkeypatch.setenv("KG_LLM_API_KEY", "legacy-secret-must-not-load")
    monkeypatch.setenv("KG_PLANNER_MODEL", "legacy-agent")
    monkeypatch.setenv("KG_REVIEWER_MODEL", "legacy-reviewer")
    settings = _settings(tmp_path)
    store = LLMConfigStore(settings.db_path, settings)
    assert store.list_providers() == []
    assert store.get_defaults()["agent"]["provider_id"] == ""

    now = 1.0
    store._conn.execute(
        "INSERT INTO llm_defaults(singleton, agent_provider_id, agent_model_id, "
        "agent_reasoning_effort, reviewer_provider_id, reviewer_model_id, "
        "reviewer_reasoning_effort, version, updated_at, updated_by) "
        "VALUES (1,'legacy-env','legacy-agent','auto','legacy-env',"
        "'legacy-reviewer','auto',1,?,'migration-test')",
        (now,),
    )
    store._conn.execute(
        "INSERT INTO session_llm_settings(session_id, provider_id, model_id, "
        "reasoning_effort, version, updated_at, updated_by) "
        "VALUES ('old-session','legacy-env','legacy-agent','auto',1,?,"
        "'migration-test')",
        (now,),
    )
    store._conn.commit()
    store.close()

    reopened = LLMConfigStore(settings.db_path, settings)
    assert reopened.get_defaults()["version"] == 0
    assert reopened.get_session("old-session", ensure=False) is None
    assert reopened.list_providers() == []
    reopened.close()


def test_api_key只写入受限文件且不进入数据库或公开结构(tmp_path):
    settings = _settings(tmp_path)
    store = LLMConfigStore(settings.db_path, settings)
    secret = "sk-test-provider-secret-123456"
    provider = store.create_provider(
        name="测试网关",
        adapter="openai_compatible",
        base_url="https://llm.example.test/v1",
        api_key=secret,
        models=[_model()],
        updated_by="admin",
    )

    assert provider["api_key_configured"] is True
    assert secret not in json.dumps(provider, ensure_ascii=False)
    row = store._conn.execute(  # 数据库只允许随机引用，不允许明文 Key
        "SELECT secret_ref FROM llm_providers WHERE id=?", (provider["id"],)
    ).fetchone()
    assert row and secret not in row[0]
    secret_path = store.secrets.directory / row[0]
    assert secret_path.read_text(encoding="utf-8") == secret
    if os.name == "posix":
        assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.secrets.directory.stat().st_mode) == 0o700
    for path in tmp_path.glob("kg.db*"):
        assert secret.encode() not in path.read_bytes()
    store.close()


def test默认值与会话模型固定并使用乐观版本(tmp_path):
    settings = _settings(tmp_path)
    store = LLMConfigStore(settings.db_path, settings)
    provider = store.create_provider(
        name="A", adapter="openai", base_url="https://api.example.test/v1",
        api_key="key-a", models=[_model("m1", ["low", "high"]), _model("m2")],
    )
    defaults = store.get_defaults()
    assert defaults["agent"]["model_id"] == "m1"

    session = store.ensure_session("s1")
    assert session["model_id"] == "m1" and session["version"] == 1
    changed = store.update_session(
        "s1", selection=ModelSelection(provider["id"], "m2", "auto"),
        expected_version=1, updated_by="admin",
    )
    assert changed["model_id"] == "m2" and changed["version"] == 2
    with pytest.raises(LLMConfigError) as conflict:
        store.update_session(
            "s1", selection=ModelSelection(provider["id"], "m1", "low"),
            expected_version=1,
        )
    assert conflict.value.status_code == 409
    with pytest.raises(LLMConfigError) as unsupported:
        store.update_session(
            "s1", selection=ModelSelection(provider["id"], "m2", "high"),
            expected_version=2,
        )
    assert unsupported.value.code == "reasoning_effort_unsupported"
    store.close()


def test更换提供商主机必须重新输入key(tmp_path):
    settings = _settings(tmp_path)
    store = LLMConfigStore(settings.db_path, settings)
    provider = store.create_provider(
        name="A", adapter="openai_compatible",
        base_url="https://one.example.test/v1", api_key="key-a",
        models=[_model()],
    )
    with pytest.raises(LLMConfigError) as caught:
        store.update_provider(
            provider["id"], expected_version=provider["version"], name="A",
            adapter="openai_compatible", base_url="https://two.example.test/v1",
            models=[_model()], enabled=True,
        )
    assert caught.value.code == "api_key_required_for_origin_change"
    store.close()


def test模型发现为兼容协议填入常用档位并保留DeepSeek语义(tmp_path):
    settings = _settings(tmp_path)
    store = LLMConfigStore(settings.db_path, settings)
    deepseek = store.create_provider(
        name="DeepSeek",
        adapter="deepseek",
        base_url="https://api.deepseek.com",
        api_key="key-deepseek",
        models=[_model("deepseek-v4-pro")],
    )
    compatible = store.create_provider(
        name="兼容网关",
        adapter="openai_compatible",
        base_url="https://gateway.example.test/v1",
        api_key="key-compatible",
        models=[],
    )

    deepseek = store.add_discovered_models(
        deepseek["id"], ["deepseek-v4-pro"],
        expected_version=deepseek["version"],
    )
    compatible = store.add_discovered_models(
        compatible["id"], ["custom-reasoner"],
        expected_version=compatible["version"],
    )

    assert deepseek["models"][0]["supported_efforts"] == ["none", "high", "max"]
    assert compatible["models"][0]["supported_efforts"] == [
        "low", "medium", "high",
    ]
    store.close()


async def test并发会话的contextvar路由不会串模型(tmp_path):
    settings = _settings(tmp_path)
    store = LLMConfigStore(settings.db_path, settings)
    first = store.create_provider(
        name="A", adapter="openai", base_url="https://a.example.test/v1",
        api_key="key-a", models=[_model("model-a")],
    )
    second = store.create_provider(
        name="B", adapter="deepseek", base_url="https://b.example.test/v1",
        api_key="key-b", models=[_model("model-b", ["high"])],
    )
    store.ensure_session(
        "s-a", selection=ModelSelection(first["id"], "model-a", "auto"))
    store.ensure_session(
        "s-b", selection=ModelSelection(second["id"], "model-b", "high"))
    runtime = LLMRuntime(store, settings)
    routed = runtime.routed_client("agent")

    async def inspect(session_id):
        async with runtime.bind(session_id) as bundle:
            before = routed.model
            await asyncio.sleep(0)
            return before, routed.model, bundle.agent.provider_id

    a, b = await asyncio.gather(inspect("s-a"), inspect("s-b"))
    assert a == ("model-a", "model-a", first["id"])
    assert b == ("model-b", "model-b", second["id"])
    store.close()


def test推理参数按适配器映射且能力未声明时可省略temperature():
    messages = [{"role": "user", "content": "hi"}]
    deepseek = LLMClient(
        "https://api.deepseek.com", "k", "deepseek-v4-pro",
        adapter="deepseek", reasoning_effort="max",
        supports_temperature=False,
    )._completion_options(messages, 0.2)
    assert deepseek["reasoning_effort"] == "max"
    assert deepseek["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in deepseek

    redirect_guard = LLMClient("https://example.test/v1", "k", "m")
    assert redirect_guard._http_client.follow_redirects is False

    disabled = LLMClient(
        "https://api.deepseek.com", "k", "deepseek-v4-pro",
        adapter="deepseek", reasoning_effort="none",
        supports_temperature=True,
    )._completion_options(messages, 0.2)
    assert disabled["extra_body"] == {"thinking": {"type": "disabled"}}
    assert disabled["temperature"] == 0.2

    dashscope = LLMClient(
        "https://dashscope.aliyuncs.com/compatible-mode/v1", "k", "qwen",
        adapter="dashscope", reasoning_effort="medium",
        supports_temperature=False,
    )._completion_options(messages, 0.2)
    assert dashscope["extra_body"] == {
        "enable_thinking": True, "thinking_budget": 8192,
    }


class _FakePipeline:
    def session_busy(self, _session_id):
        return False

    async def handle(self, session_id, user_query, emit):
        await emit({"type": "user_query", "query": user_query})
        await emit({"type": "final_answer", "answer": "ok", "aborted": False})


async def _login(client):
    response = await client.post("/api/login", json={
        "username": "admin", "password": "test-password",
    })
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _sse(text):
    return [json.loads(line[6:]) for line in text.split("\n\n")
            if line.startswith("data: ")]


async def test图形化配置API与会话切换且响应不泄漏key(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings, with_tools=False)
    app.state.pipeline = _FakePipeline()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        unauthenticated = await client.post("/api/llm/providers", json={
            "name": "未登录不能配置",
            "adapter": "openai_compatible",
            "base_url": "https://gateway.example.test/v1",
            "api_key": "sk-unauthenticated",
            "models": [_model("m1")],
        })
        assert unauthenticated.status_code == 401
        headers = await _login(client)
        initial = (await client.get("/api/llm/config", headers=headers)).json()
        assert initial["providers"] == []
        assert initial["defaults"] == {
            "version": 0,
            "agent": {
                "provider_id": "", "model_id": "", "reasoning_effort": "auto",
            },
            "reviewer": {
                "provider_id": "", "model_id": "", "reasoning_effort": "auto",
            },
        }

        secret = "sk-api-only-secret-abcdef"
        created = await client.post("/api/llm/providers", headers=headers, json={
            "name": "测试提供商",
            "adapter": "openai_compatible",
            "base_url": "https://gateway.example.test/v1",
            "api_key": secret,
            "models": [_model("m1", ["low", "high"]), _model("m2")],
            "enabled": True,
        })
        assert created.status_code == 201
        provider = created.json()["provider"]
        assert secret not in created.text and "secret_ref" not in created.text

        config_response = await client.get("/api/llm/config", headers=headers)
        assert secret not in config_response.text
        config = config_response.json()
        assert config["defaults"]["agent"]["provider_id"] == provider["id"]

        chat = await client.post("/api/chat", headers=headers, json={
            "message": "hello", "provider_id": provider["id"],
            "model_id": "m1", "reasoning_effort": "high",
        })
        events = _sse(chat.text)
        session_created = events[0]
        assert session_created["model_context"]["model_id"] == "m1"
        session_id = session_created["session_id"]

        current = await client.get(
            f"/api/sessions/{session_id}/model", headers=headers)
        assert current.json()["reasoning_effort"] == "high"
        changed = await client.put(
            f"/api/sessions/{session_id}/model", headers=headers, json={
                "version": current.json()["version"],
                "provider_id": provider["id"], "model_id": "m2",
                "reasoning_effort": "auto",
            })
        assert changed.status_code == 200
        assert changed.json()["model_id"] == "m2"

        unsafe = await client.post("/api/llm/providers", headers=headers, json={
            "name": "x", "adapter": "not-an-adapter",
            "base_url": "https://example.test", "api_key": secret,
            "models": [],
        })
        assert unsafe.status_code == 422
        assert secret not in unsafe.text

    audit_text = json.dumps(
        app.state.audit.events("__llm_config__"), ensure_ascii=False)
    assert secret not in audit_text


async def test未配置图形化模型时拒绝创建任务且不留下会话(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings, with_tools=False)
    app.state.pipeline = _FakePipeline()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = await _login(client)
        response = await client.post(
            "/api/chat", headers=headers, json={"message": "hello"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "model_configuration_required"
        sessions = await client.get("/api/sessions", headers=headers)
        assert sessions.json()["sessions"] == []


async def test模型配置变更与审计同事务失败会完整回滚(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    app = create_app(settings, with_tools=False)
    app.state.pipeline = _FakePipeline()
    original_append = app.state.audit.append

    def fail_created(session_id, event_type, payload, **kwargs):
        if event_type == "llm_provider_created":
            raise AuditError("模拟审计磁盘故障")
        return original_append(session_id, event_type, payload, **kwargs)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = await _login(client)
        monkeypatch.setattr(app.state.audit, "append", fail_created)
        with pytest.raises(AuditError):
            await client.post("/api/llm/providers", headers=headers, json={
                "name": "不能半提交", "adapter": "openai_compatible",
                "base_url": "https://gateway.example.test/v1",
                "api_key": "sk-rollback-secret",
                "models": [_model("m1")], "enabled": True,
            })
        assert app.state.llm_config.get_defaults()["version"] == 0
        assert app.state.llm_config.list_providers() == []
        assert list(app.state.llm_config.secrets.directory.iterdir()) == []

        monkeypatch.setattr(app.state.audit, "append", original_append)
        created = await client.post("/api/llm/providers", headers=headers, json={
            "name": "可提交", "adapter": "openai_compatible",
            "base_url": "https://gateway.example.test/v1",
            "api_key": "sk-valid-secret",
            "models": [_model("m1"), _model("m2")], "enabled": True,
        })
        provider = created.json()["provider"]
        chat = await client.post("/api/chat", headers=headers, json={
            "message": "hello", "provider_id": provider["id"], "model_id": "m1",
        })
        session_id = _sse(chat.text)[0]["session_id"]
        before = app.state.llm_config.get_session(session_id)

        def fail_session(session_id_value, event_type, payload, **kwargs):
            if event_type == "session_model_changed":
                raise AuditError("模拟审计磁盘故障")
            return original_append(session_id_value, event_type, payload, **kwargs)

        monkeypatch.setattr(app.state.audit, "append", fail_session)
        with pytest.raises(AuditError):
            await client.put(
                f"/api/sessions/{session_id}/model", headers=headers, json={
                    "version": before["version"], "provider_id": provider["id"],
                    "model_id": "m2", "reasoning_effort": "auto",
                })
        after = app.state.llm_config.get_session(session_id)
        assert after["model_id"] == "m1"
        assert after["version"] == before["version"]


async def test会话模型绑定失败不会留下普通或完全访问幽灵会话(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    app = create_app(settings, with_tools=False)
    app.state.llm_config.create_provider(
        name="测试提供商",
        adapter="openai_compatible",
        base_url="https://gateway.example.test/v1",
        api_key="sk-binding-test",
        models=[_model("m1")],
    )

    def fail_model_binding(*_args, **_kwargs):
        raise LLMConfigError("model_binding_failed", "模拟模型绑定失败。")

    monkeypatch.setattr(
        app.state.llm_config, "create_session_with_connection",
        fail_model_binding,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        headers = await _login(client)
        ordinary = await client.post(
            "/api/chat", headers=headers, json={"message": "不能留下幽灵会话"})
        assert ordinary.status_code == 400
        assert app.state.sessions.list() == []

        draft_id = "a" * 32
        full_access = await client.post("/api/sessions", headers=headers, json={
            "session_id": draft_id,
            "mode": "full_access",
            "ttl_seconds": 60,
            "password": "test-password",
        })
        assert full_access.status_code == 400
        assert app.state.sessions.exists(draft_id) is False
        assert app.state.audit.events(draft_id) == []
