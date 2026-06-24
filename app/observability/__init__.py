"""可观测性模块（OBS-01/02）。

模块组织：
- tracer.py：Tracer 上下文管理器 + step 装饰器，自动计时并写入 PG agent_traces 表
- 被统一查询接口（/v2/query）和其它需要 trace 的地方调用
"""
