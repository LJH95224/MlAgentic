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

    # --- LLM 网关（3.2 阶段启用） ---
    # 通过 LiteLLM 统一调用 DeepSeek / Qwen / GLM 等模型，仅需修改 .env 即可切换厂商。
    # 模型命名规范：
    #   - DeepSeek 官方：       deepseek/deepseek-chat、deepseek/deepseek-reasoner
    #   - 阿里 DashScope（Qwen）：dashscope/qwen-max
    #   - 智谱：                zhipu/glm-4
    # 若 LITELLM_MODEL 未带厂商前缀（如直接写 "deepseek-chat"），LLM 客户端会根据
    # LITELLM_API_BASE 推断并自动补前缀，以减少 .env 配置失误。
    litellm_model: str | None = Field(default=None, alias="LITELLM_MODEL")
    litellm_api_key: str | None = Field(default=None, alias="LITELLM_API_KEY")
    litellm_api_base: str | None = Field(default=None, alias="LITELLM_API_BASE")
    # 请求级超时（秒）与重试次数，给 LiteLLM 透传
    litellm_timeout: float = Field(default=60.0, alias="LITELLM_TIMEOUT")
    litellm_num_retries: int = Field(default=2, alias="LITELLM_NUM_RETRIES")

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
