"""业务编排层：充当 API 与 Agent/DB 之间的胶水。

- session_service：会话生命周期 CRUD（V1.0 + V1.5 SES-01~06）
- chat_service：调度 Agent + 持久化消息 + 产出对外事件
- kb_service：知识库 CRUD + Milvus Collection 生命周期（V1.5 KB-01~05）
"""
