"""全局配置：从环境变量与项目根 .env 读取，统一 KG_ 前缀。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根的 .env 用绝对路径定位：无论从哪个目录启动服务都能读到；
# 当前工作目录若另有 .env 则优先（后加载覆盖）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ROOT_ENV = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_ROOT_ENV), ".env"), env_prefix="KG_", extra="ignore"
    )

    # 模型提供商、API Key、可用模型和 Agent/Reviewer 默认值只通过
    # “模型服务”界面管理。环境变量仅保留进程级重试/超时与凭据目录参数。
    llm_max_retries: int = 3
    llm_timeout: float = 60.0
    # GUI 保存的模型凭据不再写回项目 .env。留空时放到
    # XDG_STATE_HOME（或 ~/.local/state）下的受限目录；生产部署可显式
    # 指向 /var/lib/kylinguard 等仅控制面账户可读的位置。
    llm_secrets_dir: str = ""

    # 扩展配置。MCP 的敏感环境变量与模型密钥一样放在数据库之外的
    # 受限目录；自定义 Skill 只会从这个受控根目录按需读取。
    mcp_secrets_dir: str = ""
    skills_dir: str = ""
    skills_state_path: str = ""

    # 存储
    db_path: str = "data/kylinguard.db"

    # 执行器
    workspace_root: str = str(_PROJECT_ROOT)
    command_shell: str = "/bin/bash"
    command_timeout: int = 30
    command_max_timeout: int = 900
    output_max_bytes: int = 65536
    exec_user: str = ""  # 生产环境设为 kylinguard-exec；空 = 当前用户（开发）
    privileged_helper: str = ""  # 生产环境设为 root-owned 受限 helper；空 = 不启用

    # 全局审批权限。full_access 默认可用；显式设为 false 才关闭。它以配置的
    # exec_user 或后端当前 OS 身份执行。完全访问持续到手动收回、入口隐藏、服务端禁用或后端重启。
    allow_full_access: bool = True
    # 以下 TTL 仅用于会话内的单次/同类操作授权，不限制全局权限模式。
    permission_default_ttl: int = 30 * 60

    # 感知
    snapshot_interval: int = 30  # 快照后台轮询间隔（秒）

    # 规划循环
    max_json_retries: int = 3
    max_iterations: int = 6
    confirm_timeout: int = 300  # 人工确认等待秒数，超时按拒绝处理


def get_settings() -> Settings:
    return Settings()


def get_execution_settings() -> Settings:
    """工具子进程仅读取显式环境，不加载含密钥的项目 ``.env``。"""
    return Settings(_env_file=None)
