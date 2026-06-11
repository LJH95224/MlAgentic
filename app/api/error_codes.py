"""V1.5 业务错误码定义（PRD §7.2）。

设计约定：
- 业务 code 与 HTTP status 解耦但成对维护，便于前端按业务 code 做差异化处理
- code = 0 是唯一的"成功"标志；任何非 0 都是失败
- 错误码命名空间规则（5 位整数）：
    40xxx → 客户端错误（对应 HTTP 4xx）
    50xxx → 服务端错误（对应 HTTP 5xx）
  低位两位用来细分同 HTTP 类型下的子原因
- code → HTTP status 的反向映射由 `app/api/exceptions.py::HTTP_STATUS_BY_CODE` 维护，
  errno 表本身只声明业务语义，不耦合 HTTP 概念
- 使用方：`raise BusinessError(NOT_FOUND, "会话 xx 不存在")`，handler 自动转 `{code,...,data:null}` + 对应 HTTP 状态
"""

# ───────── 成功 ─────────
SUCCESS = 0

# ───────── 客户端错误 (40xxx → HTTP 4xx) ─────────
# 通用参数错误
PARAM_INVALID = 40001  # 请求参数校验失败（缺字段 / 类型错 / 超长 / 取值越界）
IMMUTABLE_FIELD = 40002  # 尝试修改 embedding_dim 等不可变字段（PRD KB-04）

# 404 — 资源不存在
NOT_FOUND = 40400  # Session / KnowledgeBase / File 不存在

# 409 — 资源冲突
NAME_CONFLICT = 40900  # 知识库 name 唯一冲突（PRD KB-01）

# 413 — 请求体过大
FILE_TOO_LARGE = 41300  # 文件超出 MAX_FILE_SIZE_MB（PRD FILE-01）

# 415 — 媒体类型不支持
UNSUPPORTED_MEDIA = 41500  # 文件格式不支持或编码无法识别（PRD FILE-01 / §6.1）

# 422 — 语义错误
EMBEDDING_DIM_MISMATCH = 42200  # 向量维度与知识库 embedding_dim 不一致（PRD §6.2）

# ───────── 服务端错误 (50xxx → HTTP 5xx) ─────────
INTERNAL_ERROR = 50000  # 未分类服务端异常（Milvus / Neo4j 连接异常等）
CELERY_UNAVAILABLE = 50300  # Celery Worker 不可达或 Redis 连接失败


# ───────── 默认 message（业务层可覆盖） ─────────
DEFAULT_MESSAGES: dict[int, str] = {
    SUCCESS: "success",
    PARAM_INVALID: "请求参数校验失败",
    IMMUTABLE_FIELD: "字段不可修改，须删除后重建",
    NOT_FOUND: "资源不存在",
    NAME_CONFLICT: "名称已存在",
    FILE_TOO_LARGE: "文件大小超出限制",
    UNSUPPORTED_MEDIA: "文件格式不支持或编码无法识别",
    EMBEDDING_DIM_MISMATCH: "向量维度与知识库配置不匹配",
    INTERNAL_ERROR: "服务器内部错误",
    CELERY_UNAVAILABLE: "异步任务队列不可达",
}


__all__ = [
    "SUCCESS",
    "PARAM_INVALID",
    "IMMUTABLE_FIELD",
    "NOT_FOUND",
    "NAME_CONFLICT",
    "FILE_TOO_LARGE",
    "UNSUPPORTED_MEDIA",
    "EMBEDDING_DIM_MISMATCH",
    "INTERNAL_ERROR",
    "CELERY_UNAVAILABLE",
    "DEFAULT_MESSAGES",
]
