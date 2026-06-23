# TyAgent / GeoAgent V1.0

具备自主推理能力与主动知识检索能力的气象空间智能体基础后端引擎。

> 详细设计见 [docs/GeoAgent V1.0 (基础底座) 需求规格说明书.md](docs/GeoAgent%20V1.0%20%28%E5%9F%BA%E7%A1%80%E5%BA%95%E5%BA%A7%29%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md)；
> 环境管理规范见 [environment_guide_zh.md](environment_guide_zh.md)；
> Claude Code 协作约定见 [CLAUDE.md](CLAUDE.md)。

## 当前进度

- ✅ **3.1 接入与通信模块**（API-01 / API-02 / API-03）
- ✅ **3.2 LLM 路由**（LiteLLM 接入）
- ✅ **3.3 LangGraph ReAct 引擎**
- ✅ **3.4 本地脚本工具**（subprocess + 30s 超时）
- ⏳ 3.5 Agentic RAG（**Milvus** · HNSW · 4096 维）
- ⏳ 3.6 知识图谱（**Neo4j** · Graph RAG 联合查询）

> 详细进度看 [docs/progress.md](docs/progress.md)。

## 快速开始

### 1. 环境准备

```bash
# 激活 conda 环境（详见 environment_guide_zh.md）
conda activate geo_agent

# 安装 Python 依赖（必须用 uv，禁用裸 pip；走清华镜像）
uv pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 2. 配置数据库

```bash
# 复制环境变量样例并填入真实 PostgreSQL 连接串
cp .env.example .env
```

> V1.0 PostgreSQL 仅存会话与消息，不再使用 pgvector。
> 向量检索由 Milvus（3.5）承担，知识图谱由 Neo4j（3.6）承担，
> 两者均通过 `.env` 配置独立服务地址，待对应模块开发时再启用。

### 2.5 数据库迁移（Alembic）

V2 hardening Batch 2（B M-01）起，项目用 Alembic 管理 PostgreSQL schema 演进，
取代手工 `ALTER TABLE` 与启动期的 `_ensure_v2_compat_columns` 兜底。

**新部署（空库）**：

```bash
conda activate geo_agent
alembic upgrade head
```

**已有 V1.5 / V2.0 库（避免覆写已有数据）**：

```bash
# 仅标记当前库已升级到最新迁移版本，不实际执行 SQL
alembic stamp head
alembic current   # 验证：应输出当前最新 revision id (head)
```

**新增 schema 改动**：

```bash
# 1. 改 app/models/*.py
# 2. 自动生成迁移脚本（生成在 alembic/versions/YYYYMMDD_<rev>_<slug>.py）
alembic revision --autogenerate -m "<本次改动的简要描述>"
# 3. 人工 review 迁移脚本（autogen 不会处理 enum 改名 / 数据迁移等复杂场景）
# 4. 应用迁移
alembic upgrade head
# 5. 双向校验：能回滚 -> 能再升级，确认 downgrade() 没写漏
alembic downgrade -1
alembic upgrade head
```

> ⚠️ 生产环境改 schema 前必须先在测试库跑过 `upgrade head` + `downgrade -1` 双向校验。
>
> 配置入口：[alembic.ini](alembic.ini) + [alembic/env.py](alembic/env.py)。
> env.py 直接从 `app.core.config.get_settings().database_url` 拿 DB URL，
> 与 `.env` 单一来源，不存在配置漂移。

### 3. 启动服务

```bash
# uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

celery -A app.tasks.celery_app worker --pool=solo -l info
```

启动后访问 <http://localhost:8000/docs> 查看 Swagger UI。

### 4. 运行测试

```bash
pytest
```

## 接口快速验证

```bash
# 1. 创建会话
curl -X POST http://localhost:8000/api/v1/sessions

# 2. 流式对话（SSE）
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"session_id":"<上一步返回的 id>","content":"你好"}'
```

SSE 输出会区分两类事件：
- `event: message`  — 文本流（打字机）
- `event: control`  — 控制流（如 `{"type":"tool_start","tool":"mock"}`）

## 目录速览

```
app/
  api/v1/endpoints/   # FastAPI 路由
  schemas/            # Pydantic I/O 模型
  models/             # SQLAlchemy ORM（chat_sessions / chat_messages）
  services/           # 业务编排（API 与 Agent 之间的胶水）
  agent/              # LangGraph Agent（ReAct 状态机）
  llm/                # LiteLLM 网关
  tools/              # 本地脚本工具（subprocess + dummy）
  rag/                # Agentic RAG（Milvus，3.5 阶段引入）
  kg/                 # 知识图谱（Neo4j，3.6 阶段引入）
  db/                 # PostgreSQL 异步引擎与 Session
  core/               # 配置、日志
tests/                # pytest 测试
```
