"""模型接入。

只依赖 httpx，直接讲 OpenAI 兼容协议——国内厂商与中转站基本都支持。
刻意不引入各家 SDK：一旦引入就容易被单一厂商绑死，而本项目的目标
恰恰是「换个 base_url 就能换模型」。

支持两种协议：
  chat       /chat/completions —— 国内厂商普遍支持
  responses  /responses        —— 部分中转站支持，工具调用语义更清晰
"""

from __future__ import annotations

import asyncio
import time

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


def reply_from_payload(data: dict[str, Any]) -> Reply:
    """把 OpenAI 兼容的响应体解析成 Reply。

    抽出来是为了让「流式退回」和一次性请求走同一套解析逻辑——
    两处各写一遍迟早会不一致（工具调用参数的解析尤其容易写岔）。
    """
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    calls: list[ToolCall] = []
    for item in msg.get("tool_calls") or []:
        fn = item.get("function") or {}
        raw = fn.get("arguments")
        if isinstance(raw, str):
            try:
                args = json.loads(raw) if raw.strip() else {}
            except ValueError:
                args = {"_raw": raw}
        else:
            args = raw or {}
        calls.append(ToolCall(
            id=item.get("id") or "call",
            name=fn.get("name") or "",
            arguments=args,
        ))
    return Reply(
        content=msg.get("content") or "",
        tool_calls=calls,
        finish_reason=choice.get("finish_reason") or "",
        usage=data.get("usage") or {},
    )


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
            # 网关按机器限并发时会返回 429。实测这个限制很容易撞上：
            # 桌面上同时开着别的客户端、后台跑着定时任务、或一轮里多个子任务
            # 并发，都会把配额占满。等几秒通常就好，所以退避着多试几次，
            # 比一撞上就把错误甩给用户强。仍然失败才报错（下面的 429 提示）。
            for wait in (3, 6, 9):
                if resp.status_code != 429:
                    break
                await asyncio.sleep(wait)
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

    async def stream_reply(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ):
        """流式取回一次回复，产出事件字典。

        为什么要有这个方法（而不是只用 stream_chat）：
        流式下 tool_calls 是分片到达的——同一个 index 会被多次 delta 更新，
        第一次带 id 和函数名，后面只给 arguments 的片段。所以要边收边拼。
        收完之后才知道这轮到底是「纯文本回答」还是「要调工具」。

        产出的事件：
          {"type": "text",  "text": 增量文本}
          {"type": "tools", "tool_calls": [{"id","name","arguments"}]}
        一条都不产出时抛 ProviderError，由上层退回一次性请求。
        """
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.provider.timeout)

        base = self.provider.base_url.rstrip("/")
        if self.provider.wire_api == "responses":
            # responses 协议的事件结构和 chat 完全不同，暂时不做，
            # 直接抛出去让调用方退回一次性请求，好过吐出解析不了的东西。
            raise ProviderError("responses 协议暂不支持流式输出")

        url = f"{base}/chat/completions"
        payload = self._chat_payload(messages, tools)
        payload["stream"] = True

        pending: dict[int, dict[str, str]] = {}
        raw_lines: list[str] = []
        produced = False
        finished = False
        # 上一次「收到内容」的时刻。心跳行会把等待重置，所以不能按
        # 「上次收到任何东西」计时，只能按「上次收到真正内容」计时。
        last_content = 0.0

        async with self._http.stream(
            "POST", url, headers=self._headers(), json=payload
        ) as resp:
            if resp.status_code >= 400:
                raw = await resp.aread()
                raise ProviderError(
                    f"{url} 返回 {resp.status_code}："
                    f"{raw.decode('utf-8', 'replace')[:300]}"
                )

            lines = resp.aiter_lines()
            while True:
                # 拿内容之前给足时间（推理模型可能想很久才吐第一个字），
                # 拿到内容之后只要静默 6 秒就判定模型已经说完、主动收尾。
                # 实测网关会把 SSE 连接多挂 20 秒都不关也不发数据，
                # 傻等它只会让用户看到「字都显示完了还在转圈」。
                if not produced:
                    idle = 120.0
                else:
                    # 距上次内容超过 6 秒就认定说完了。用剩余时间当超时，
                    # 这样即使网关一直在发心跳，也不会被无限重置。
                    idle = max(0.2, 6.0 - (time.monotonic() - last_content))
                try:
                    line = await asyncio.wait_for(lines.__anext__(), timeout=idle)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    break
                line = line.strip()
                # SSE 里空行是分隔符，冒号开头是注释/心跳，都要跳过
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    # 不是 SSE 行，先存着：有的网关会无视 stream=true，
                    # 直接返回一份完整的 JSON 响应，那种情况可以拿来兜底。
                    raw_lines.append(line)
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    # 半行被切断时不该让整轮失败，跳过这条继续读
                    continue

                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    piece = delta.get("content") or ""
                    if piece:
                        produced = True
                        last_content = time.monotonic()
                        yield {"type": "text", "text": piece}

                    # tool_calls 分片：按 index 归并，arguments 逐片拼接
                    for frag in delta.get("tool_calls") or []:
                        idx = frag.get("index")
                        if idx is None:
                            idx = len(pending)
                        slot = pending.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if frag.get("id"):
                            slot["id"] = frag["id"]
                        fn = frag.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
                            last_content = time.monotonic()

                    # 模型用 finish_reason 明确说了「我说完了」。
                    # 不能等 [DONE] 或等连接关闭：实测网关会把 SSE 连接
                    # 多挂 9 秒才关，用户在文字吐完后又白等 9 秒。
                    if choice.get("finish_reason"):
                        finished = True

                if finished:
                    break

            if pending:
                produced = True
                yield {
                    "type": "tools",
                    "tool_calls": [
                        {"id": pending[i]["id"] or f"call_{i}",
                         "name": pending[i]["name"],
                         "arguments": pending[i]["arguments"]}
                        for i in sorted(pending)
                    ],
                }

            if not produced:
                # 有的网关会无视 stream=true，直接返回一份完整的 JSON。
                # 这种情况不该白跑一趟再重发一次请求（那会白白多消耗一次
                # 配额，网关按机器限并发时尤其明显），直接用这份响应。
                blob = "".join(raw_lines).strip()
                if blob.startswith("{"):
                    try:
                        data = json.loads(blob)
                    except ValueError:
                        data = None
                    if isinstance(data, dict) and data.get("choices"):
                        yield {"type": "full", "reply": reply_from_payload(data)}
                        return
                # 确实什么都没有才抛错，让调用方退回一次性请求。
                raise ProviderError("流式响应里没有任何内容")

    async def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ):
        """只取文本增量的便捷包装（纯文本回答用）。"""
        async for event in self.stream_reply(messages, tools):
            if event.get("type") == "text":
                yield event["text"]
            elif event.get("type") == "full":
                # 网关不认 stream 时会把整段回答一次给过来，照样吐出去，
                # 这样调用方不用为「支不支持流式」准备两套逻辑。
                text = event["reply"].content
                if text:
                    yield text

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
