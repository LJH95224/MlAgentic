"""应用配置：使用 pydantic-settings 从环境变量 / .env 文件加载。

设计原则：
- 所有可变配置必须在 Settings 中显式声明，禁止散落的 os.getenv。
- 字段命名保持与 .env.example 中的 KEY 对齐，便于运维排查。
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局应用配置。"""

    # --- 应用基础 ---
    app_name: str = Field(default="TyAgent", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    # --- 数据库（PostgreSQL + pgvector，必须使用 asyncpg 驱动） ---
    # ⚠️ 密码若含 @ / # / ? 等特殊字符，必须在 .env 中预先 URL 编码
    # （例如 @ -> %40），否则 asyncpg 会把密码后半段当成主机名。
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/tyagent",
        alias="DATABASE_URL",
    )

    # --- Agent 控制（3.3 阶段使用，先预留默认值） ---
    agent_max_iterations: int = Field(default=5, alias="AGENT_MAX_ITERATIONS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局唯一的 Settings 实例（带进程级缓存）。"""
    return Settings()
