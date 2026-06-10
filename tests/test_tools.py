"""本地工具单元测试。"""

import json

from app.tools import get_tool_map, get_tools
from app.tools.weather_parser import mock_weather_parser


def test_registry_contains_weather_parser():
    """工具注册中心应包含 mock_weather_parser。"""
    names = [t.name for t in get_tools()]
    assert "mock_weather_parser" in names

    tool_map = get_tool_map()
    assert tool_map["mock_weather_parser"] is mock_weather_parser


def test_mock_weather_parser_returns_valid_json():
    """工具应返回包含必填字段的 JSON 字符串。"""
    # langchain 的 @tool 装饰器对象通过 .invoke({...}) 调用
    raw = mock_weather_parser.invoke({"station_id": "54511", "date": "2026-06-09"})
    data = json.loads(raw)

    assert data["station_id"] == "54511"
    assert data["date"] == "2026-06-09"
    assert "temperature_c" in data
    assert "humidity_pct" in data
    assert "wind_speed_mps" in data


def test_mock_weather_parser_has_schema():
    """LangChain 工具应正确暴露参数 schema，供 LLM 推断入参。"""
    schema = mock_weather_parser.args_schema.model_json_schema()
    props = schema["properties"]
    assert "station_id" in props
    assert "date" in props
