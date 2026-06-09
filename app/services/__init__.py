"""业务编排层：充当 API 与 Agent/DB 之间的胶水。

- session_service：会话生命周期 CRUD
- chat_service：调度 Agent + 持久化消息 + 产出对外事件
"""