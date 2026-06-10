"""Mock 气象数据解析工具（TOL-02）。

V1.0 阶段的 dummy 工具，用于验证：
- 模型能否根据描述正确推断参数
- ReAct 循环中工具调用 → 观测 → 二次推理的链路是否打通
"""

import json

from langchain_core.tools import tool


@tool
def mock_weather_parser(station_id: str, date: str) -> str:
    """读取指定气象站点在某一天的实测气象数据。

    适用于查询单点、单日的温度/湿度/风速等基础气象要素。

    Args:
        station_id: 气象站 ID，例如 "54511"（北京）、"58367"(上海)。
        date: 日期，格式 "YYYY-MM-DD"，例如 "2026-06-09"。

    Returns:
        JSON 字符串，包含 station_id、date、temperature、humidity、wind_speed 字段。
    """
    # 返回固定的假数据 —— PRD 明确要求"返回固定的气象 JSON 测试用"
    payload = {
        "station_id": station_id,
        "date": date,
        "temperature_c": 25.3,
        "humidity_pct": 78,
        "wind_speed_mps": 3.2,
        "weather": "多云转晴",
    }
    return json.dumps(payload, ensure_ascii=False)
