from kylinguard.config import Settings


def test_默认值():
    s = Settings(_env_file=None)
    assert s.llm_base_url == "https://api.deepseek.com"
    assert s.planner_model == "deepseek-v4-pro"
    assert s.command_timeout == 30
    assert s.exec_user == ""


def test_环境变量覆盖(monkeypatch):
    monkeypatch.setenv("KG_PLANNER_MODEL", "qwen-max")
    monkeypatch.setenv("KG_COMMAND_TIMEOUT", "10")
    s = Settings(_env_file=None)
    assert s.planner_model == "qwen-max"
    assert s.command_timeout == 10
