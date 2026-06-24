"""LLM 消息与工具定义构造器单测（纯函数，零外部依赖）。

覆盖 [app/llm/messages.py](../app/llm/messages.py) 的全部 5 个工厂函数：

- ``user(content)`` / ``system(content)``：单字段构造
- ``assistant(content=None, tool_calls=None)``：双分支（仅 content / 带 tool_calls）
- ``tool_result(tool_call_id, content, name=None)``：闭环引用 assistant 消息
- ``define_tool(name, description, parameters)``：function 工具定义

测试目标：
1. 锁住 OpenAI/LiteLLM 兼容 dict 结构（role/content/tool_calls/tool_call_id/...）
2. 防止重构时把可选字段误改为必填
3. 验证 assistant + tool 消息能正常闭环（id 引用一致）
"""

from __future__ import annotations

from app.llm.messages import (
    assistant,
    define_tool,
    system,
    tool_result,
    user,
)


# ───────────────────────── user / system ─────────────────────────


class TestSimpleMessages:
    """user / system 是单字段消息的工厂。"""

    def test_user_basic(self):
        """user 消息只有 role + content 两个键。"""
        msg = user("你好")
        assert msg == {"role": "user", "content": "你好"}

    def test_user_empty_string_allowed(self):
        """空字符串内容应能构造（OpenAI 协议允许，业务层自己拦截）。"""
        msg = user("")
        assert msg["role"] == "user"
        assert msg["content"] == ""

    def test_system_basic(self):
        """system 消息同样只有 role + content。"""
        msg = system("你是一个气象智能体。")
        assert msg == {"role": "system", "content": "你是一个气象智能体。"}


# ───────────────────────── assistant ─────────────────────────


class TestAssistant:
    """assistant 是两种形态：纯文本 / 工具调用 / 二者并存。"""

    def test_assistant_with_content_only(self):
        """仅文本回答：tool_calls 键不应出现。"""
        msg = assistant("根据检索结果……")
        assert msg["role"] == "assistant"
        assert msg["content"] == "根据检索结果……"
        assert "tool_calls" not in msg, (
            "无 tool_calls 时不应留空键，避免下游误判"
        )

    def test_assistant_with_tool_calls(self):
        """带 tool_calls 的 assistant 消息（典型 ReAct 第一步）。"""
        tcs = [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "search_knowledge_base",
                    "arguments": '{"query":"台风"}',
                },
            }
        ]
        msg = assistant(content=None, tool_calls=tcs)
        assert msg["role"] == "assistant"
        assert msg["content"] is None
        assert msg["tool_calls"] == tcs

    def test_assistant_with_both_content_and_tool_calls(self):
        """部分模型会同时输出 content 和 tool_calls（例如思考 + 调用）。"""
        tcs = [
            {
                "id": "call_xyz",
                "type": "function",
                "function": {"name": "ping", "arguments": "{}"},
            }
        ]
        msg = assistant(content="我先调用工具：", tool_calls=tcs)
        assert msg["content"] == "我先调用工具："
        assert msg["tool_calls"] == tcs

    def test_assistant_empty_tool_calls_list_treated_as_none(self):
        """tool_calls=[] 应当被视为"没有工具调用"，不应附加 tool_calls 键。

        这是个微妙但重要的契约：很多代码做 ``msg.get("tool_calls", [])``
        然后 ``if tool_calls:`` 判定；附加空列表会导致后续逻辑错乱。
        """
        msg = assistant("纯文本", tool_calls=[])
        assert "tool_calls" not in msg


# ───────────────────────── tool_result ─────────────────────────


class TestToolResult:
    """tool_result 是 assistant.tool_calls 的回环消息。"""

    def test_tool_result_basic(self):
        """name 不传时仅 role/content/tool_call_id 三个键。"""
        msg = tool_result(tool_call_id="call_abc", content='{"ok":true}')
        assert msg == {
            "role": "tool",
            "content": '{"ok":true}',
            "tool_call_id": "call_abc",
        }
        assert "name" not in msg

    def test_tool_result_with_name(self):
        """部分厂商（如老 OpenAI Function calling）需要 name 字段。"""
        msg = tool_result(
            tool_call_id="call_abc",
            content="42",
            name="add_two_numbers",
        )
        assert msg["name"] == "add_two_numbers"
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_abc"

    def test_assistant_tool_result_roundtrip(self):
        """assistant.tool_calls[i].id 与 tool_result.tool_call_id 必须能闭环。

        这是 LLM tool_use 协议最关键的契约：模型按 tool_call_id 把结果与
        请求配对。如果这两个工厂构造的 id 字段名漂移，整个 ReAct 链路就崩。
        """
        tcs = [
            {
                "id": "call_42",
                "type": "function",
                "function": {"name": "ping", "arguments": "{}"},
            }
        ]
        a_msg = assistant(content=None, tool_calls=tcs)
        t_msg = tool_result(tool_call_id="call_42", content="pong")
        # 通过 id 字段闭环引用
        assert a_msg["tool_calls"][0]["id"] == t_msg["tool_call_id"]


# ───────────────────────── define_tool ─────────────────────────


class TestDefineTool:
    """define_tool 是 LiteLLM tools=[] 参数的工厂。"""

    def test_define_tool_structure(self):
        """完整结构：type=function 嵌套 function:{name,description,parameters}。"""
        td = define_tool(
            name="search_kb",
            description="检索知识库",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        )
        assert td["type"] == "function"
        assert td["function"]["name"] == "search_kb"
        assert td["function"]["description"] == "检索知识库"
        assert td["function"]["parameters"]["properties"]["q"]["type"] == "string"
        assert td["function"]["parameters"]["required"] == ["q"]

    def test_define_tool_keeps_parameters_reference_safely(self):
        """parameters 内容应原样保留，不应被工厂内部 mutate。"""
        params = {"type": "object", "properties": {}, "required": []}
        td = define_tool("noop", "do nothing", params)
        # 工厂不应修改入参 dict
        assert params == {"type": "object", "properties": {}, "required": []}
        # 输出指向同一对象或等价 dict（这里只断等价，不强约束 deep copy）
        assert td["function"]["parameters"] == params


__all__: list[str] = []
