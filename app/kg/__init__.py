"""知识图谱模块（3.6 阶段引入，基于 Neo4j）。

按新版 PRD：
- 通过 Neo4j AsyncDriver 接入（连接配置写在 .env）
- 节点：Entity（name + type 复合唯一）/ Document（document_id 唯一）
- 关系：MENTIONED_IN（chunk_id 属性）/ RELATED_TO（V1.0 未抽取）
- 与 Milvus 通过 document_id 和 entity_tags 字段串联

对外入口：
- init_neo4j() / close_neo4j()：lifespan 接入
- get_neo4j_driver()：业务代码获取单例
- upsert_document / upsert_entity / link_entity_to_chunk + 批量版本：写入
- run_ner：LLM Prompt 驱动的 NER
- query_knowledge_graph：注册到 Agent 工具集的 LangChain @tool
"""

from app.kg.ner import run_ner
from app.kg.neo4j_client import close_neo4j, get_neo4j_driver, init_neo4j
from app.kg.tool import query_knowledge_graph
from app.kg.writer import (
    bulk_link_entities_to_chunk,
    bulk_upsert_entities,
    link_entity_to_chunk,
    upsert_document,
    upsert_entity,
)

__all__ = [
    # lifespan
    "init_neo4j",
    "get_neo4j_driver",
    "close_neo4j",
    # writer
    "upsert_document",
    "upsert_entity",
    "link_entity_to_chunk",
    "bulk_upsert_entities",
    "bulk_link_entities_to_chunk",
    # NER
    "run_ner",
    # Agent tool
    "query_knowledge_graph",
]
