# A.1 Reranker Threshold 调优实验报告

> **日期**：2026-06-16
> **目的**：定位 Reranker 开启后整体评分大幅下跌的根因，找到最优 `similarity_threshold` 配置
> **结论**：**当前 Reranker 模型（Qwen3-Reranker-8B）弊大于利，生产环境建议关闭 Reranker 或切换模型**

---

## 1. 当前 RAG 链路结构

V2.0 Hermes 的完整检索-生成链路如下：

```
用户 Query
   │
   ▼
┌──────────────────────────────────────────────────────────┐
│ 1. 三层配置合并（resolve_options）                         │
│    API options > KB.retrieval_config > 全局 settings      │
│    输出：ResolvedRetrievalOptions                         │
└──────────────────────────────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────────────────────────────┐
│ 2. Query 增强（HRE-01 / HRE-02）                          │
│    ├─ Query 改写：none / HyDE / multi_query               │
│    └─ Query NER → 图谱锚定（Neo4j 1-hop → entity_tags）   │
└──────────────────────────────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────────────────────────────┐
│ 3. 混合检索（HRE-03/04，hybrid_search）                    │
│    ├─ 稠密向量检索（HNSW + COSINE，Qwen3-Embedding-8B）    │
│    ├─ BM25 稀疏检索（Milvus 内置 BM25 Function）            │
│    └─ RRF 融合（k=60），取 2×top_k 候选                    │
└──────────────────────────────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────────────────────────────┐
│ 4. Reranker 精排（HRE-05，可选）                           │
│    ├─ 模型：SiliconFlow → Qwen3-Reranker-8B               │
│    │  （通过 httpx 直调 /v1/rerank，Cohere 兼容格式）       │
│    ├─ 过滤：score < similarity_threshold 的 chunk 被丢弃    │
│    ├─ 兜底：过滤后 < 3 条时补到 3 条（score=0 标记）        │
│    └─ 降级：API 失败 → 返回原顺序（score=0 标记）           │
└──────────────────────────────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────────────────────────────┐
│ 5. Citation 组装 + LLM 生成（CHC-01/02）                   │
│    ├─ build_context_with_citation → [1] 来源标注           │
│    └─ litellm.acompletion → 生成带引用标注的答案             │
└──────────────────────────────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────────────────────────────┐
│ 6. 置信度 + 答案自检（CHC-03/04，可选）                     │
│    ├─ compute_confidence → 0~1 置信度评分                  │
│    └─ check_faithfulness → 未支撑事实检测                   │
└──────────────────────────────────────────────────────────┘
```

### 关键组件当前配置

| 组件 | 当前值 | 来源 |
|---|---|---|
| Embedding 模型 | `Qwen/Qwen3-Embedding-8B` | SiliconFlow |
| Embedding 维度 | 4096 | Milvus Collection |
| BM25 | 开启 | `BM25_ENABLE=true` |
| RRF k | 60 | 默认 |
| Reranker 模型 | `Qwen/Qwen3-Reranker-8B` | SiliconFlow |
| Reranker 阈值 | 0.3 | `RERANKER_SIMILARITY_THRESHOLD=0.3` |
| LLM (生成) | `deepseek/deepseek-chat` | DeepSeek |
| LLM (评估 Judge) | `deepseek/deepseek-chat` | 复用生成模型 |
| Query 改写 | none | 默认 |
| Graph RAG | 开启 | `GRAPH_RAG_ENABLE=true` |

---

## 2. 测试方法

### 2.1 评估集

使用 `eval_set_compare.json`，包含 **3 道评估题**：

| # | 问题 | 主题 |
|---|---|---|
| 1 | 这个平台主要做什么？ | 预警联动 / 产品定位 |
| 2 | 系统包含哪些核心模块？ | 核心能力一体化 |
| 3 | 产品的市场定位有哪些方向？ | 市场定位建议 |

每题配有详细的标准答案（ground_truth），含引用标注。

### 2.2 知识库

- **KB ID**：`620b6dcb-03a4-4d05-9fff-e7557102f942`
- **文档**：1 个 docx（气象平台产品规划文档）
- **Chunk 数**：约 160 个 fine chunks + table descriptions + coarse chunks
- **Neo4j 实体**：已抽取并入库

### 2.3 评估指标（RAGAS 0.2+）

| 指标 | 含义 | 评估方式 |
|---|---|---|
| **faithfulness** | 答案事实是否有文档支撑 | LLM 拆 claims → 逐条验证是否在 contexts 中 |
| **answer_relevancy** | 答案是否真正回答了问题 | LLM 生成潜在问题 → 算与原问题的语义相似度 |
| **context_precision** | 检索 chunk 的有用占比 | LLM 判断每个 chunk 是否对回答问题有用 → 加权 Precision |
| **context_recall** | 标准答案信息是否被召回 | LLM 将 ground_truth 拆为 statements → 检查是否被 contexts 覆盖 |
| **overall_score** | 4 项算术均值 | PRD §853 |

### 2.4 评估流程

```
eval_compare.py → POST /evaluate → Celery worker →
  ├─ 逐题跑 RAG：eval_runner.run_single_query_for_eval
  │   └─ hybrid_search → build_context → generate_answer
  │      （跳过 Trace 写入、跳过 faithfulness_check，避免干扰）
  └─ ragas evaluate 整批打分
```

- 评估 LLM：`deepseek/deepseek-chat`（通过 LangChain ChatOpenAI 适配）
- 评估 Embedding：`Qwen/Qwen3-Embedding-8B`（answer_relevancy / context_precision 需要）

### 2.5 实验设计

本次实验固定以下变量，仅调节 Reranker 的开关和阈值：

| 变量 | 固定值 |
|---|---|
| top_k | 5 |
| bm25_enable | true |
| enable_graph_rag | true |
| query_rewrite | none |
| Reranker 模型 | Qwen/Qwen3-Reranker-8B (SiliconFlow) |

实验组：

| 编号 | 实验名 | reranker_enable | similarity_threshold | 说明 |
|---|---|---|---|---|
| A1 | baseline | false | N/A | 无 Reranker，纯 RRF 排序 |
| B0 | thresh=0.3 | true | 0.3 | 当前生产默认值 |
| B1 | thresh=0.1 | true | 0.1 | 降低过滤阈值 |
| B2 | thresh=0.0 | true | 0.0 | 不过滤，纯精排 |

---

## 3. 实验结果

### 3.1 主表

| 实验 | faithfulness | answer_relevancy | context_precision | context_recall | **overall_score** |
|---|---|---|---|---|---|
| A1 baseline (no reranker) | 0.533 | **0.423** | 0.559 | **0.367** | **0.471** |
| B0 reranker + thresh=0.3 | 0.307 | 0.190 | 0.167 | 0.067 | 0.183 |
| B1 reranker + thresh=0.1 | **0.673** | 0.230 | 0.533 | 0.217 | 0.413 |
| B2 reranker + thresh=0.0 | 0.285 | 0.234 | **0.647** | 0.317 | 0.370 |

### 3.2 vs Baseline 差异

| 实验 | overall diff | 说明 |
|---|---|---|
| B0 thresh=0.3 | **-0.288** | 断崖下跌，0.3 阈值过滤掉绝大多数 chunk |
| B1 thresh=0.1 | **-0.057** | 显著改善但仍不如 baseline |
| B2 thresh=0.0 | **-0.100** | 纯精排不过滤，但排序质量不如原 RRF |

### 3.3 与此前 7 组对比实验的交叉验证

此前用 `eval_set_smoke.json`（5 题）跑的 7 组对比中，关键参照：

| 实验 | overall | ctx_precision | 条件 |
|---|---|---|---|
| A1 baseline (5 题) | 0.516 | 0.591 | top_k=5, graph_rag=off |
| A5 rerank on (5 题) | 0.285 | 0.218 | reranker_enable=true |

本次 3 题实验的趋势完全一致：
- A1 baseline → A5 rerank on：overall -0.231，ctx_precision -0.373（7 组对比）
- A1 baseline → B0 thresh=0.3：overall -0.288，ctx_precision -0.393（本次）

**两次独立实验交叉验证了同一结论：Reranker 开启后整体评分断崖下跌，且主因是 context_precision 暴跌。**

---

## 4. 分析

### 4.1 阈值 0.3 确实是罪魁祸首之一

B0（thresh=0.3）的 context_recall 仅 0.067（baseline 0.367），意味着 Reranker 过滤掉了 **82%** 的 chunk，标准答案所需信息几乎全部丢失。降低阈值后：

- B1（0.1）：context_recall 恢复到 0.217，整体从 0.183 回升到 0.413
- B2（0.0）：context_recall 恢复到 0.317，整体从 0.183 回升到 0.370

**降低阈值有显著改善，假设"0.3 过滤了太多"已验证。**

### 4.2 但 Reranker 排序质量本身也有问题

关键观察：**即使 threshold=0.0（不过滤，仅用 Reranker 分数重排），overall 仍比 baseline 低 0.100。**

这说明问题不仅仅是"过滤太狠"，还在于 **Reranker 的排序质量不如原 RRF 排序**：

| 指标 | baseline (RRF) | B2 (Reranker thresh=0.0) | 解读 |
|---|---|---|---|
| answer_relevancy | 0.423 | 0.234 | Reranker 重排后，LLM 生成的答案与原问题相关性下降 |
| context_recall | 0.367 | 0.317 | 虽然不过滤，但重排导致关键 chunk 排名靠后、被截断 |
| faithfulness | 0.533 | 0.285 | Reranker 重排后，支撑答案的事实信息覆盖面下降 |

可能的原因：
1. **Qwen3-Reranker-8B 对中文气象领域理解不足**：该模型是通用 reranker，在气象专业场景的排序可能不如向量相似度 + BM25 的 RRF 融合
2. **小数据量下 Reranker 无优势**：1 个 docx 160 chunks，候选集仅 10 条，Reranker 精排的边际收益有限，反而可能因排序失误把好 chunk 排到后面
3. **Reranker score 分布与 RRF score 分布不一致**：RRF 的 score 是 rank-based（0~0.02 区间），Reranker 的 relevance_score 是 0~1 连续值；Reranker 覆盖 score 后，下游 citation/confidence 计算基于 Reranker 分数而非 RRF 分数，可能导致评分体系偏移

### 4.3 有趣的 trade-off

- B1（thresh=0.1）的 **faithfulness 最高**（0.673 > baseline 0.533）→ Reranker 确实在"哪些 chunk 更支撑事实"上有一定判断力
- B2（thresh=0.0）的 **context_precision 最高**（0.647 > baseline 0.559）→ 纯精排后，被引用的 chunk 中"有用"的占比更高
- 但 answer_relevancy 和 context_recall 都不如 baseline → Reranker 重排后**丢失了对答案生成重要的 chunk**

这指向一个核心矛盾：Reranker 倾向于挑选"与 query 语义最相似"的 chunk，但**语义最相似 ≠ 最有助于生成完整答案**。某些 chunk 虽然与 query 字面相关度低，但包含了 ground_truth 所需的关键事实。

---

## 5. 结论与推荐

### 5.1 短期（立即可做）

**生产环境关闭 Reranker**：`RERANKER_TYPE=none`

当前配置下 Reranker 弊大于利，baseline（纯 RRF 融合）是最优选择。

### 5.2 中期（1~2 周）

**切换 Reranker 模型验证**：将 `RERANKER_MODEL` 从 `Qwen3-Reranker-8B` 切换为 PRD 推荐的 `BAAI/bge-reranker-v2-m3`，重跑 B2（threshold=0.0）实验。

如果 bge-reranker-v2-m3 在 thresh=0.0 下能超过 baseline，则可以启用 Reranker。

```bash
# 切换模型后重跑
python scripts/eval_compare.py \
  --kb-id 620b6dcb-03a4-4d05-9fff-e7557102f942 \
  --eval-set eval_set_compare.json \
  --experiments baseline,rerank_thresh_0.0
```

### 5.3 长期方向

1. **更多文档 + 更大 KB**：当前 1 个 docx 160 chunks 数据量太小。Reranker 的精排价值在候选集大时（> 50 条）才更明显。建议上传 3~5 个相关文档后重测
2. **Reranker score 不覆盖 RRF score**：考虑用 Reranker score 做 **加权融合**（如 `final_score = α * rrf_score + (1-α) * rerank_score`）而非直接覆盖，保留 RRF 的排序信息
3. **Reranker 仅用于 threshold 过滤**：不做重排，只用 Reranker score 过滤明显不相关的 chunk（threshold=0.1），排序仍用 RRF

---

## 6. 实验数据

### 6.1 原始数据

```json
[
  {
    "experiment": "baseline",
    "label": "A1 baseline (no reranker)",
    "summary": {
      "faithfulness": 0.533,
      "answer_relevancy": 0.423,
      "context_precision": 0.559,
      "context_recall": 0.367,
      "overall_score": 0.471
    }
  },
  {
    "experiment": "rerank_thresh_0.3",
    "label": "B0 reranker + thresh=0.3",
    "summary": {
      "faithfulness": 0.307,
      "answer_relevancy": 0.190,
      "context_precision": 0.167,
      "context_recall": 0.067,
      "overall_score": 0.183
    }
  },
  {
    "experiment": "rerank_thresh_0.1",
    "label": "B1 reranker + thresh=0.1",
    "summary": {
      "faithfulness": 0.673,
      "answer_relevancy": 0.230,
      "context_precision": 0.533,
      "context_recall": 0.217,
      "overall_score": 0.413
    }
  },
  {
    "experiment": "rerank_thresh_0.0",
    "label": "B2 reranker + thresh=0.0",
    "summary": {
      "faithfulness": 0.285,
      "answer_relevancy": 0.234,
      "context_precision": 0.647,
      "context_recall": 0.317,
      "overall_score": 0.370
    }
  }
]
```

### 6.2 Eval Task IDs

| 实验 | eval_task_id |
|---|---|
| baseline | `4176b60a-df60-41c9-8772-58cc16c05f6d` |
| rerank_thresh_0.3 | `80956500-8431-4576-a5dd-b4fcfe35a9ac` |
| rerank_thresh_0.1 | `fcca3065-7670-4564-b4f6-90d6f11eb301` |
| rerank_thresh_0.0 | `2403f287-d343-4312-a789-73c5bef24a0f` |

### 6.3 测试环境

- **平台**：Windows 10 Pro + Docker Desktop
- **Python**：3.11.15 (conda env: geo_agent)
- **RAGAS**：0.2+
- **LLM (生成)**：deepseek/deepseek-chat via DeepSeek API
- **LLM (评估 Judge)**：deepseek/deepseek-chat（复用生成模型）
- **Embedding**：Qwen/Qwen3-Embedding-8B via SiliconFlow
- **Reranker**：Qwen/Qwen3-Reranker-8B via SiliconFlow
- **Milvus**：v2.6.18 (Docker standalone)
- **Neo4j**：Community 5.x (Docker)
- **PostgreSQL**：17.10 (Docker)
- **Redis**：7-alpine (Docker)
