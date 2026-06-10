"""Agent 编排引擎模块（3.3 阶段：基于 LangGraph 的 ReAct 状态机）。

公共入口：
  - app.agent.runner.run_stream  对外的流式执行函数（service 层使用）
  - app.agent.graph.get_compiled_graph  获取编译后的 LangGraph 实例
"""
