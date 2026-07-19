from pathlib import Path

from kylinguard.config import Settings, get_execution_settings


def test_默认值():
    s = Settings(_env_file=None)
    assert s.llm_timeout == 60.0
    assert Path(s.workspace_root).resolve() == Path(__file__).resolve().parents[2]
    assert Path(s.frontend_dist).resolve() == (
        Path(__file__).resolve().parents[2] / "frontend" / "dist"
    )
    assert s.command_shell == "/bin/bash"
    assert s.command_timeout == 30
    assert s.command_max_timeout == 900
    assert s.exec_user == ""
    assert s.allow_full_access is True
    assert s.permission_default_ttl == 1800


def test_环境变量覆盖(monkeypatch):
    monkeypatch.setenv("KG_COMMAND_TIMEOUT", "10")
    monkeypatch.setenv("KG_COMMAND_MAX_TIMEOUT", "1200")
    monkeypatch.setenv("KG_COMMAND_SHELL", "/bin/zsh")
    monkeypatch.setenv("KG_WORKSPACE_ROOT", "/srv/agent-workspace")
    monkeypatch.setenv("KG_FRONTEND_DIST", "/opt/kylinguard/current/frontend")
    monkeypatch.setenv("KG_LLM_TIMEOUT", "25")
    monkeypatch.setenv("KG_ALLOW_FULL_ACCESS", "false")
    s = Settings(_env_file=None)
    assert s.command_timeout == 10
    assert s.command_max_timeout == 1200
    assert s.command_shell == "/bin/zsh"
    assert s.workspace_root == "/srv/agent-workspace"
    assert s.frontend_dist == "/opt/kylinguard/current/frontend"
    assert s.llm_timeout == 25.0
    assert s.allow_full_access is False


def test_执行子进程配置不读取当前目录dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "KG_LLM_API_KEY=should-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = get_execution_settings()
    assert not hasattr(settings, "llm_api_key")
