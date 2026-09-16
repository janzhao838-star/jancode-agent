"""模型客户端测试。重点是不联网也能验证的协议构造与错误处理。"""

import json

import pytest

from jancode_agent.config import ProviderConfig
from jancode_agent.providers import Client, Message, ProviderError, ToolCall, _parse_arguments


def make(**kw) -> Client:
    base = {"name": "t", "base_url": "https://x.example/v1", "model": "m", "api_key": "sk-ok"}
    base.update(kw)
    return Client(ProviderConfig(**base))


def test_密钥含非_ascii_时给出可操作提示():
    """HTTP 头只能放 ASCII。密钥里混入中文是复制粘贴常见错误，
    底层会抛 'ascii' codec... 这种给程序员看的错误，必须拦下来换成人话。"""
    c = make(api_key="sk-含中文")
    with pytest.raises(ProviderError) as e:
        c._headers()
    msg = str(e.value)
    assert "非 ASCII" in msg
    assert "含中文" in msg            # 指出是哪些字符
    assert "codec" not in msg         # 不能把底层内部错误暴露出去


def test_正常密钥构造出_bearer_头():
    h = make()._headers()
    assert h["Authorization"] == "Bearer sk-ok"


def test_无密钥时不带_authorization_头():
    assert "Authorization" not in make(api_key="")._headers()


def test_chat_协议的消息结构():
    c = make(wire_api="chat")
    msgs = [
        Message(role="system", content="你是助手"),
        Message(role="user", content="你好"),
        Message(role="assistant", tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "a"})]),
        Message(role="tool", content="文件内容", tool_call_id="1", name="read_file"),
    ]
    payload = c._chat_payload(msgs, tools=None)
    assert payload["model"] == "m"
    body = payload["messages"]
    # 工具调用的 arguments 必须序列化成字符串，这是 OpenAI 协议要求
    assert isinstance(body[2]["tool_calls"][0]["function"]["arguments"], str)
    assert json.loads(body[2]["tool_calls"][0]["function"]["arguments"]) == {"path": "a"}
    assert body[3]["tool_call_id"] == "1"


def test_responses_协议把_system_拎出来():
    c = make(wire_api="responses")
    msgs = [
        Message(role="system", content="你是助手"),
        Message(role="user", content="你好"),
        Message(role="tool", content="结果", tool_call_id="c1", name="read_file"),
    ]
    payload = c._responses_payload(msgs, tools=None)
    assert payload["instructions"] == "你是助手"
    kinds = [i.get("type") for i in payload["input"]]
    assert "function_call_output" in kinds
    # system 不应再出现在 input 里
    assert all(i.get("role") != "system" for i in payload["input"])


def test_解析_chat_响应():
    reply = make()._parse_chat({
        "choices": [{
            "message": {
                "content": "好的",
                "tool_calls": [{"id": "a", "function": {"name": "list_dir", "arguments": '{"path":"."}'}}],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"total_tokens": 10},
    })
    assert reply.content == "好的"
    assert reply.tool_calls[0].name == "list_dir"
    assert reply.tool_calls[0].arguments == {"path": "."}


def test_解析_responses_响应():
    reply = make(wire_api="responses")._parse_responses({
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "答案"}]},
            {"type": "function_call", "call_id": "c", "name": "grep", "arguments": '{"pattern":"x"}'},
        ],
    })
    assert reply.content == "答案"
    assert reply.tool_calls[0].name == "grep"


def test_响应没有_choices_时报错而不是静默():
    with pytest.raises(ProviderError):
        make()._parse_chat({})


@pytest.mark.parametrize("raw,expected", [
    ('{"a":1}', {"a": 1}),      # 正常
    ({'a': 1}, {"a": 1}),       # 已经是字典
    ("", {}),                    # 空
    ("{坏的 json", {}),          # 模型偶尔会返回不合法 JSON，不能崩掉整个会话
    ("[1,2]", {}),               # 不是对象
    (None, {}),
])
def test_工具参数解析容错(raw, expected):
    assert _parse_arguments(raw) == expected
