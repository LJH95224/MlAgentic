"""V2.0 可观测性模块（OBS-01/02）。

模块组织：
- tracer.py：Tracer 上下文管理器 + step 装饰器，自动计时并写入 PG agent_traces 表
- 被 V2 统一查询接口（T6 /v2/query）和其它需要 trace 的地方调用
"""
