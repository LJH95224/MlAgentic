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

    # --- 数据库（PostgreSQL，会话与消息存储；必须使用 asyncpg 驱动） ---
    # 注：知识切片库由 Milvus 管理（3.5 阶段引入），知识图谱由 Neo4j 管理（3.6 阶段引入），
    #     PostgreSQL 不再承担向量存储职责。
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

    # --- Milvus 向量库（3.5 阶段启用） ---
    # PyMilvus 2.6+ 的 MilvusClient 同时兼容本地 standalone 与 Zilliz Cloud：
    #   本地：MILVUS_URI=http://localhost:19530，token 留空
    #   云上：MILVUS_URI=https://xxx.zillizcloud.com，token 填集群凭证
    milvus_uri: str = Field(default="http://localhost:19530", alias="MILVUS_URI")
    milvus_token: str | None = Field(default=None, alias="MILVUS_TOKEN")
    milvus_collection: str = Field(default="knowledge_chunks", alias="MILVUS_COLLECTION")

    # --- Embedding 模型（3.5 阶段启用） ---
    # 独立于 chat 模型配置：chat 与 embedding 经常不同源
    # （例如 chat 走 DeepSeek，embedding 走 SiliconFlow 的 Qwen3-Embedding-8B）。
    # 经由 LiteLLM 调用，model 命名需带厂商前缀（通常用 openai/ 走通用 OpenAI 兼容协议）。
    embedding_model: str | None = Field(default=None, alias="EMBEDDING_MODEL")
    embedding_api_key: str | None = Field(default=None, alias="EMBEDDING_API_KEY")
    embedding_api_base: str | None = Field(default=None, alias="EMBEDDING_API_BASE")
    # 必须与 Milvus Collection 中 vector 字段的 dim 严格一致，否则写入/检索都会出错
    embedding_dimension: int = Field(default=4096, alias="EMBEDDING_DIMENSION")

    # --- RAG 权限基线（V1.0 硬编码，3.6 接入用户体系后再替换） ---
    # 工具内部检索时会自动追加 ARRAY_CONTAINS(allowed_roles, rag_default_role) 过滤
    rag_default_role: str = Field(default="ALL", alias="RAG_DEFAULT_ROLE")

    # --- Neo4j 知识图谱（3.6 阶段启用） ---
    # 默认账号与本仓库 docker-compose/docker-compose.yml 中 NEO4J_AUTH 对齐：
    #   neo4j / tyagent_neo4j
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="tyagent_neo4j", alias="NEO4J_PASSWORD")
    # 数据库名（社区版固定 "neo4j"）
    neo4j_database: str = Field(default="neo4j", alias="NEO4J_DATABASE")

    # --- 知识图谱 NER（3.6 阶段启用） ---
    # 留空则复用 LITELLM_MODEL / LITELLM_API_KEY / LITELLM_API_BASE
    # 想用更便宜的轻量模型做 NER 时，可独立指定
    kg_ner_model: str | None = Field(default=None, alias="KG_NER_MODEL")

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
