# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

**TyAgent / GeoAgent V1.0** —— 一个面向气象空间智能的 Agent 后端引擎基础底座。当前仓库处于初始化阶段，**尚未有任何源代码**，仅包含两份文档：

- [environment_guide_zh.md](environment_guide_zh.md) —— Conda + uv 混合环境管理规范
- [docs/GeoAgent V1.0 (基础底座) 需求规格说明书.md](docs/GeoAgent%20V1.0%20%28%E5%9F%BA%E7%A1%80%E5%BA%95%E5%BA%A7%29%20%E9%9C%80%E6%B1%82%E8%A7%84%E6%A0%BC%E8%AF%B4%E6%98%8E%E4%B9%A6.md) —— V1.0 PRD

后续开发须围绕该 PRD 推进；在搭建任何模块前请先阅读这两份文档。

## 目标技术栈（PRD 强制）

| 层 | 选型 |
| --- | --- |
| Web 框架 | **FastAPI**（异步、SSE 流式输出） |
| Agent 编排 | **LangGraph**（ReAct 状态机，需实现 `Thought → Action → Observation` 循环） |
| 模型网关 | **LiteLLM**（统一 OpenAI 规范，支持 DeepSeek/Qwen/GLM 等切换，仅靠 `.env` 配置） |
| 存储 | **PostgreSQL + pgvector**（向量召回 + 关系数据，使用 SQLAlchemy ORM） |
| 通信 | **Server-Sent Events (SSE)**，需区分「文本流」与「控制流」（如 `{"type":"tool_start","tool":"search"}`） |

V1.0 **明确不做**：Docker 动态沙盒、外部 MCP 接口、前端 WebGIS（Cesium/OpenLayers）联动渲染。脚本执行使用 `subprocess.run` 子进程模式，**必须设置超时**（建议 30s）。

## 关键架构约束

1. **ReAct 熔断**：LangGraph 循环最大轮次 `max_iterations = 5`，超过强制终止并返回兜底回复（需求 ID `AGT-03`）。
2. **错误反思注入**：Tool 抛出异常时，必须捕获堆栈并以 `ToolMessage` 形式回传给模型，让模型自我修正后重试（`AGT-04`）。这是 ReAct 链路的核心质量指标，**不要静默吞掉异常**。
3. **Agentic RAG**：检索是大模型主动发起的 Tool（`search_knowledge_base(query, top_k)`），**不是**入站时硬塞 context。`knowledge_chunks.metadata` 字段（JSONB）必须保留，用于 `WHERE metadata->>'type' = 'report'` 形式的硬过滤（`RAG-03`）。
4. **Embedding 维度**：`knowledge_chunks.embedding` 当前规划 `Vector(1536)`，但实际维度取决于最终选用的 Embedding 模型 —— 改模型时必须同步迁移表结构。

## 数据模型（V1.0 三张核心表）

- `chat_sessions`（id UUID PK / created_at / metadata JSONB）
- `chat_messages`（id / session_id FK / role: system|user|assistant|tool / content / tool_calls JSONB）
- `knowledge_chunks`（id / document_id / content / embedding Vector(N) / metadata JSONB）

## 环境管理规范（强制）

本项目采用 **Conda + uv 混合管理**，环境名 `geo_agent`，Python `3.11`：

- **Conda（conda-forge）** 负责重型 C/C++ 底层库：`gdal`、`geopandas`、`rasterio`、`pyproj`。**禁止用 pip/uv 安装或升级这些包**，否则破坏 GDAL/PROJ/GEOS 链接。
- **uv** 负责所有纯 Python 包（Agent 框架、LLM SDK、FastAPI、SQLAlchemy 等）。

### Claude 操作铁律

1. **绝不使用** `pip install <pkg>`；统一改用 `uv pip install <pkg>`。
2. 若新依赖底层链接 GDAL/PROJ/GEOS，**必须**改用 `conda install <pkg> -c conda-forge`。
3. 批量加库时，写入 `requirements.txt` 后执行 `uv pip install -r requirements.txt`。
4. 激活环境：`conda activate geo_agent`。

### 环境导出（备份/迁移时）

```bash
conda env export --from-history > environment.yml   # 仅导出主动安装的 conda 包
uv pip freeze > requirements.txt                     # 锁定纯 Python 依赖
```

## 当前状态

- 仓库**不是** Git 仓库（无 `.git`），平台 Windows 10 / bash shell。
- 尚无构建、测试、lint 配置 —— 这些工具链需要在第一次落代码时一并建立（建议 `ruff` + `pytest`，仍走 `uv pip install` 安装）。
- 文档与代码注释**统一使用简体中文**。
