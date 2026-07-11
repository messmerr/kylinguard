from kylinguard.subprocess_env import agent_subprocess_env, safe_subprocess_env


def test_结构化插件环境保持最小化():
    env = safe_subprocess_env({
        "PATH": "/tmp/evil:/home/user/.local/bin:/usr/bin",
        "LD_PRELOAD": "/tmp/evil.so",
        "LD_AUDIT": "/tmp/audit.so",
        "LD_LIBRARY_PATH": "/tmp/lib",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "HTTPS_PROXY": "http://proxy.example",
        "VIRTUAL_ENV": "/srv/project/.venv",
        "KG_LLM_API_KEY": "secret",
    })

    assert env["PATH"] == "/usr/sbin:/usr/bin:/sbin:/bin"
    assert "LD_PRELOAD" not in env
    assert "LD_AUDIT" not in env
    assert "LD_LIBRARY_PATH" not in env
    assert "SSH_AUTH_SOCK" not in env
    assert "HTTPS_PROXY" not in env
    assert "VIRTUAL_ENV" not in env
    assert "KG_LLM_API_KEY" not in env


def test_通用终端保留开发环境但剥离控制面():
    env = agent_subprocess_env({
        "PATH": "/home/user/.local/bin:/usr/bin",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "HTTPS_PROXY": "http://proxy.example",
        "VIRTUAL_ENV": "/srv/project/.venv",
        "JAVA_HOME": "/opt/jdk",
        "PROJECT_MODE": "test",
        "LD_LIBRARY_PATH": "/home/user/project/lib",
        "KG_LLM_API_KEY": "llm-secret",
        "KG_DB_PATH": "/srv/control.db",
    })

    assert env["SSH_AUTH_SOCK"] == "/tmp/agent.sock"
    assert env["HTTPS_PROXY"] == "http://proxy.example"
    assert env["VIRTUAL_ENV"] == "/srv/project/.venv"
    assert env["JAVA_HOME"] == "/opt/jdk"
    assert env["PROJECT_MODE"] == "test"
    assert env["PATH"] == "/home/user/.local/bin:/usr/bin"
    assert env["LD_LIBRARY_PATH"] == "/home/user/project/lib"
    assert all(not key.upper().startswith("KG_") for key in env)
