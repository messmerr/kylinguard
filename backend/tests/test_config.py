from kylinguard.config import Settings, get_execution_settings


def test_默认值():
    s = Settings(_env_file=None)
    assert s.llm_base_url == "https://api.deepseek.com"
    assert s.planner_model == "deepseek-v4-pro"
    assert s.llm_timeout == 60.0
    assert s.command_timeout == 30
    assert s.exec_user == ""
    assert s.allow_full_access is False
    assert s.permission_default_ttl == 1800


def test_环境变量覆盖(monkeypatch):
    monkeypatch.setenv("KG_PLANNER_MODEL", "qwen-max")
    monkeypatch.setenv("KG_COMMAND_TIMEOUT", "10")
    monkeypatch.setenv("KG_LLM_TIMEOUT", "25")
    monkeypatch.setenv("KG_ALLOW_FULL_ACCESS", "true")
    monkeypatch.setenv("KG_FULL_ACCESS_MAX_TTL", "600")
    s = Settings(_env_file=None)
    assert s.planner_model == "qwen-max"
    assert s.command_timeout == 10
    assert s.llm_timeout == 25.0
    assert s.allow_full_access is True
    assert s.full_access_max_ttl == 600


def test_执行子进程配置不读取当前目录dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "KG_LLM_API_KEY=should-not-load\n"
        "KG_ADMIN_PASSWORD=should-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = get_execution_settings()
    assert settings.llm_api_key == ""
    assert settings.admin_password == ""
