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

### 3. 启动服务

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
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
