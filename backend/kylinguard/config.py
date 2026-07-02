"""全局配置：从环境变量与项目根 .env 读取，统一 KG_ 前缀。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根的 .env 用绝对路径定位：无论从哪个目录启动服务都能读到；
# 当前工作目录若另有 .env 则优先（后加载覆盖）
_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_ROOT_ENV), ".env"), env_prefix="KG_", extra="ignore"
    )

    # LLM 网关（OpenAI 兼容；规划与审查双实例可用不同模型）
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    planner_model: str = "deepseek-v4-pro"
    reviewer_model: str = "deepseek-v4-pro"
    llm_max_retries: int = 3

    # 存储
    db_path: str = "data/kylinguard.db"

    # 执行器
    command_timeout: int = 30
    output_max_bytes: int = 65536
    exec_user: str = ""  # 生产环境设为 kylinguard-exec；空 = 当前用户（开发）

    # 规划循环
    max_json_retries: int = 3
    max_iterations: int = 6
    confirm_timeout: int = 300  # 人工确认等待秒数，超时按拒绝处理


def get_settings() -> Settings:
    return Settings()
