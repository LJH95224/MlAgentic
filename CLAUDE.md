# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 📌 工作前必读（强制流程）

**任何编码任务开始前，必须按此顺序阅读**：

1. [docs/progress.md](docs/progress.md) —— **当前进度文档**：了解项目实现到哪一步、各模块状态、关键文件位置、待办清单。
2. **当前迭代 PRD（V1.5，进行中）**：[docs/TyAgent V1.5 · 需求规格说明书.md](docs/TyAgent%20V1.5%20%C2%B7%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) —— 会话/知识库/文件管理 + 异步任务全链路。
3. **基线 PRD（V1.0，已完成）**：[docs/GeoAgent V1.0 (基础底座) 需求规格说明书.md](docs/GeoAgent%20V1.0%20%28%E5%9F%BA%E7%A1%80%E5%BA%95%E5%BA%A7%29%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) —— ReAct Agent 基础底座（已 100% 通过）。
4. **V1.5 开发拆分计划**：[docs/v1.5_dev_plan.md](docs/v1.5_dev_plan.md) —— 按 SES/KB/FILE/TASK 子需求 ID 拆分的实施步骤与依赖关系。
5. [environment_guide_zh.md](environment_guide_zh.md) —— Conda + uv 混合环境管理规范。

**任何模块完成（或对已完成模块做实质性改动）后，必须同步更新 [docs/progress.md](docs/progress.md)**：
- 把对应模块状态改为 ✅，填入完成日期
- 列出新增/修改的关键文件、交付内容、验证结果
- 更新底部"历史变更"区
- 若有新增的架构契约或关键设计决策，在该模块小节中明确写出

进度文档是后续 Claude 实例快速接手的唯一可靠入口，**禁止跳过更新**。

## 项目定位

**TyAgent / GeoAgent** —— 面向气象空间智能的 Agent 后端引擎。

- **V1.0（基础底座，已完成）**：ReAct Agent + Agentic RAG（Milvus）+ Graph RAG（Neo4j），10 条验收项全过。
- **V1.5（当前迭代）**：在底座之上构建**数据管理层** —— 会话生命周期、多知识库空间、文件上传异步入库、Celery 任务队列。详见 [docs/v1.5_dev_plan.md](docs/v1.5_dev_plan.md)。

## 目标技术栈（PRD 强制）

| 层 | 选型 |
| --- | --- |
| Web 框架 | **FastAPI**（异步、SSE 流式输出） |
| Agent 编排 | **LangGraph**（ReAct 状态机，`Thought → Action → Observation` 循环） |
| 模型网关 | **LiteLLM**（统一 OpenAI 规范，DeepSeek/Qwen/GLM 等仅靠 `.env` 切换） |
| 存储 | **PostgreSQL（会话/消息/知识库/文件元数据）+ Milvus（向量切片，按 KB 隔离 Collection）+ Neo4j（知识图谱，按 kb_id 隔离子图）** |
| 异步队列 | **Celery 5 + Redis 7**（V1.5 新增；文件解析入库 / 标题摘要生成） |
| 文档解析 | **PyMuPDF + python-docx + Unstructured + LangChain TextSplitter**（V1.5 新增） |
| 通信 | **Server-Sent Events (SSE)**，区分「文本流」与「控制流」 |

**明确不做**：Docker 动态沙盒、外部 MCP、前端 WebGIS、用户认证（JWT/RBAC，V1.5 预留 `allowed_roles` 但不激活）、嵌入模型微调或更换、实时协同编辑。脚本执行用 `subprocess.run` 子进程模式，**必须设置超时**（建议 30s）。

## 关键架构约束

1. **ReAct 熔断**：LangGraph 循环最大轮次 `max_iterations = 5`，超过强制终止并返回兜底回复（需求 ID `AGT-03`）。
2. **错误反思注入**：Tool 抛出异常时，必须捕获堆栈并以 `ToolMessage` 形式回传给模型，让模型自我修正后重试（`AGT-04`）。这是 ReAct 链路的核心质量指标，**不要静默吞掉异常**。
3. **Agentic RAG**：检索是大模型主动发起的 Tool（`search_knowledge_base(query, top_k, **kwargs)`），**不是**入站时硬塞 context。Milvus `knowledge_chunks` Collection 中的 `metadata`（JSON）、`allowed_roles`（Array）、`entity_tags`（Array）字段必须保留，用于标量过滤（`RAG-03`）、权限过滤（`RAG-04`）、与 Neo4j 图谱对齐（`RAG-05`）。
4. **Graph RAG 联合查询（KG-04）**：Agent 层先调用 Neo4j 图谱锚定实体上下文，再把实体标签注入 Milvus `entity_tags` 标量过滤做精准向量检索。两次调用都要走 SSE `tool_start` 控制流。
5. **Embedding 维度**：Milvus `knowledge_chunks.vector` 当前规划 **4096 维**（HNSW + COSINE）；改 Embedding 模型时需同步重建 Collection 与索引。

## 数据模型（V1.0 三库协同）

**PostgreSQL（会话状态）：**
- `chat_sessions`（id UUID PK / created_at / metadata JSONB）
- `chat_messages`（id / session_id FK / role: system|user|assistant|tool / content / tool_calls JSONB）

**Milvus Collection `knowledge_chunks`（向量切片，HNSW + COSINE，4096 维）：**
- `chunk_id`(INT64 PK) / `vector`(FLOAT_VECTOR, dim=4096) / `document_id`(VARCHAR 64) / `content`(VARCHAR 65535) / `allowed_roles`(ARRAY<VARCHAR>, cap=20) / `entity_tags`(ARRAY<VARCHAR>, cap=50) / `metadata`(JSON)

**Neo4j（知识图谱）：**
- Node `Entity`（name / type / document_id）
- Node `Document`（document_id / title / created_at）
- Relationship `RELATED_TO`（relation_type / weight）、`MENTIONED_IN`（chunk_id）

## 环境管理规范（强制）

本项目采用 **Conda + uv 混合管理**，环境名 `geo_agent`，Python `3.11`：

- **Conda（conda-forge）** 负责重型 C/C++ 底层库：`gdal`、`geopandas`、`rasterio`、`pyproj`。**禁止用 pip/uv 安装或升级这些包**，否则破坏 GDAL/PROJ/GEOS 链接。
- **uv** 负责所有纯 Python 包（Agent 框架、LLM SDK、FastAPI、SQLAlchemy 等）。

### Claude 操作铁律

1. **绝不使用** `pip install <pkg>`；统一改用 `uv pip install <pkg> -i https://pypi.tuna.tsinghua.edu.cn/simple`。
2. 若新依赖底层链接 GDAL/PROJ/GEOS，**必须**改用 `conda install <pkg> -c conda-forge`。
3. 批量加库时，写入 `requirements.txt` 后执行 `uv pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`。
4. 激活环境：`conda activate geo_agent`。

> 国内访问 PyPI 官方源不稳定，所有 `uv pip install` 命令必须在末尾追加 `-i https://pypi.tuna.tsinghua.edu.cn/simple` 走清华镜像。

### 环境导出（备份/迁移时）

```bash
conda env export --from-history > environment.yml   # 仅导出主动安装的 conda 包
uv pip freeze > requirements.txt                     # 锁定纯 Python 依赖
```

## 当前状态

- 平台 Windows 10 / bash shell。
- 已建立工具链：`ruff` + `pytest`（配置见 [pyproject.toml](pyproject.toml)），所有 Python 依赖在 `geo_agent` conda 环境内。
- 文档与代码注释**统一使用简体中文**。
- **当前实现进度查看 [docs/progress.md](docs/progress.md)** —— 该文档是判断"什么已完成、下一步做什么"的唯一真实来源。

## 用户操作约定（重要）

以下操作由**用户手动执行**，Claude 不要自动调用：

1. **依赖安装**（`uv pip install <pkg> -i https://pypi.tuna.tsinghua.edu.cn/simple`）—— Claude 只更新 `requirements.txt`，由用户执行安装命令。
2. **运行类命令**（`pytest`、`uvicorn`、联调脚本等）—— Claude 给出运行指令，由用户执行后把输出贴回。

Claude 可以读文件、写代码、写测试、写文档，但不要自作主张去 `pip install` 或启动长进程。
