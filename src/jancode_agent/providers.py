"""模型接入。

只依赖 httpx，直接讲 OpenAI 兼容协议——国内厂商与中转站基本都支持。
刻意不引入各家 SDK：一旦引入就容易被单一厂商绑死，而本项目的目标
恰恰是「换个 base_url 就能换模型」。

支持两种协议：
  chat       /chat/completions —— 国内厂商普遍支持
  responses  /responses        —— 部分中转站支持，工具调用语义更清晰
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import ProviderConfig


class ProviderError(RuntimeError):
    """模型调用失败。消息里带上状态码与响应体，便于定位是中转站还是模型的问题。"""


@dataclass
class ToolCall:
    """模型请求调用的一次工具。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    """对话中的一条消息。role 为 assistant 且带 tool_calls 时表示模型要调工具。"""

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # 工具结果消息需要回指是哪次调用
    tool_call_id: str = ""
    name: str = ""

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role}
        # 有 tool_calls 时 content 允许为空，但部分中转站要求该字段存在
        out["content"] = self.content
        if self.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        if self.name:
            out["name"] = self.name
        return out


@dataclass
class Reply:
    """模型的一次回复。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """工具参数解析。

    模型偶尔会返回不合法的 JSON（多余逗号、中文引号等），这里做兜底：
    解析失败时返回空参数而不是抛异常——让模型看到「参数为空」自行纠正，
    比整个会话崩掉更符合预期。
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class Client:
    """OpenAI 兼容协议的异步客户端。"""

    def __init__(self, provider: ProviderConfig, http: httpx.AsyncClient | None = None):
        self.provider = provider
        self._http = http
        self._owns_http = http is None

    async def __aenter__(self) -> "Client":
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.provider.timeout)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.provider.api_key:
            key = self.provider.api_key
            # HTTP 头只能放 ASCII。密钥里混入中文（复制粘贴常见）时，
            # 底层会抛 'ascii' codec can't encode... 这种给程序员看的错误。
            # 在这里拦住，换成用户能照着做的提示。
            try:
                key.encode("ascii")
            except UnicodeEncodeError:
                bad = "".join(c for c in key if ord(c) > 127)
                raise ProviderError(
                    f"API 密钥含非 ASCII 字符：{bad!r}。"
                    f"密钥应为纯英文数字，请检查是否误复制了中文说明文字。"
                ) from None
            h["Authorization"] = f"Bearer {key}"
        return h

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Reply:
        """发起一次对话补全。按 provider.wire_api 选择协议。"""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.provider.timeout)

        base = self.provider.base_url.rstrip("/")
        if self.provider.wire_api == "responses":
            url = f"{base}/responses"
            payload = self._responses_payload(messages, tools)
        else:
            url = f"{base}/chat/completions"
            payload = self._chat_payload(messages, tools)

        try:
            resp = await self._http.post(url, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"连接 {url} 失败：{exc}") from exc

        if resp.status_code >= 400:
            # 把中转站返回的原始错误带出来。国内中转站的错误信息通常很有用，
            # 例如 {"code":"API_KEY_REQUIRED", ...}——直接吞掉会让排查变得很痛苦。
            body = resp.text[:600]

            # 429 不一定是「你被限流了」这么笼统——自建网关常按机器限制并发，
            # 报错里会写清楚（例如「这台电脑已有两个进行中的请求」）。
            # 把它翻译成用户能自己处理的说明，比甩原始 JSON 有用得多。
            if resp.status_code == 429:
                raise ProviderError(
                    f"网关说这台电脑的并发额度用满了（429）。\n"
                    f"原始说明：{body[:200]}\n"
                    f"处理办法：等十几秒重发；或者换一套接入配置"
                    f"（左下角「设置与微信推送」里切）。\n"
                    f"有些自建网关限制每台机器同时只有 2 个请求，"
                    f"同时开多个窗口或后台还跑着任务时容易撞上。"
                )

            raise ProviderError(f"{url} 返回 {resp.status_code}：{body}")

        try:
            data = resp.json()
        except ValueError as exc:
            # 地址少写 /v1 是最常见的错误，此时对方通常返回一个网页（404/首页），
            # 直接甩一段 HTML 给用户等于没说。这里做成能照着修的提示。
            head = resp.text.lstrip()[:200]
            looks_like_html = head[:1] == "<" or "<!doctype" in head[:40].lower()
            if looks_like_html:
                raise ProviderError(
                    f"地址 {url} 返回的是网页，不是接口。\n"
                    f"多半是接口地址少了 /v1（或地址填成了网站首页）。\n"
                    f"正确写法一般是 https://你的中转站域名/v1\n"
                    f"当前配置的地址是：{self.provider.base_url}"
                ) from exc
            raise ProviderError(f"{url} 返回的不是 JSON：{resp.text[:300]}") from exc

        if self.provider.wire_api == "responses":
            return self._parse_responses(data)
        return self._parse_chat(data)

    def _chat_payload(self, messages: list[Message], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.provider.model,
            "messages": [m.to_wire() for m in messages],
            "temperature": self.provider.temperature,
        }
        if tools:
            if getattr(self.provider, "effort", ""):
                payload["reasoning"] = {"effort": self.provider.effort}
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # 推理强度：只有用户选了才发，空着就用服务端默认。
        # 不要在没选的时候硬塞一个值——不支持的模型收到会直接报错。
        if getattr(self.provider, "effort", ""):
            payload["reasoning_effort"] = self.provider.effort
        return payload

    def _responses_payload(self, messages: list[Message], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        # responses 协议把 system 单独拎出来，其余按 input 传入
        instructions = ""
        items: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                instructions = (instructions + "\n" + m.content).strip()
            elif m.role == "tool":
                items.append({
                    "type": "function_call_output",
                    "call_id": m.tool_call_id,
                    "output": m.content,
                })
            elif m.role == "assistant" and m.tool_calls:
                if m.content:
                    items.append({"role": "assistant", "content": m.content})
                for tc in m.tool_calls:
                    items.append({
                        "type": "function_call",
                        "call_id": tc.id,
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    })
            else:
                items.append({"role": m.role, "content": m.content})

        payload: dict[str, Any] = {
            "model": self.provider.model,
            "input": items,
            "temperature": self.provider.temperature,
        }
        if instructions:
            payload["instructions"] = instructions
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": t["function"].get("parameters", {}),
                }
                for t in tools
            ]
        return payload

    def _parse_chat(self, data: dict[str, Any]) -> Reply:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"响应里没有 choices：{json.dumps(data, ensure_ascii=False)[:300]}")
        msg = choices[0].get("message") or {}
        calls = [
            ToolCall(
                id=tc.get("id") or f"call_{i}",
                name=(tc.get("function") or {}).get("name", ""),
                arguments=_parse_arguments((tc.get("function") or {}).get("arguments")),
            )
            for i, tc in enumerate(msg.get("tool_calls") or [])
        ]
        return Reply(
            content=msg.get("content") or "",
            tool_calls=[c for c in calls if c.name],
            finish_reason=choices[0].get("finish_reason", ""),
            usage=data.get("usage") or {},
        )

    def _parse_responses(self, data: dict[str, Any]) -> Reply:
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for item in data.get("output") or []:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") in ("output_text", "text"):
                        text_parts.append(part.get("text", ""))
            elif kind == "function_call":
                calls.append(ToolCall(
                    id=item.get("call_id") or item.get("id") or f"call_{len(calls)}",
                    name=item.get("name", ""),
                    arguments=_parse_arguments(item.get("arguments")),
                ))
        return Reply(
            content="".join(text_parts),
            tool_calls=[c for c in calls if c.name],
            finish_reason=data.get("status", ""),
            usage=data.get("usage") or {},
        )
