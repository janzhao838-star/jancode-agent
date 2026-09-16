"""本地 Web 服务。

为什么用标准库而不上 FastAPI：这个服务的职责只有一个——
把界面上的任务交给智能体并把过程推回去。引入框架会带来依赖与版本问题，
而标准库的 http.server 足够。启动零依赖，是刻意的取舍。

流式返回用 NDJSON（每行一个 JSON），而不是 WebSocket：
单向推送不需要双向通道，NDJSON 在浏览器里用 fetch + ReadableStream
就能消费，不需要额外库。
"""

from __future__ import annotations

import asyncio
import json
import threading
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .agent import Agent
from .config import load_config
from .providers import ProviderError

WEB_DIR = Path(__file__).parent / "web"


def _index_html() -> bytes:
    return (WEB_DIR / "index.html").read_bytes()


class Handler(BaseHTTPRequestHandler):
    config = None  # 由 serve() 注入
    server_version = "JanCodeAgent"

    def log_message(self, fmt: str, *args: object) -> None:
        # 默认会把每个请求打到 stderr，界面上会刷屏。静音。
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 本地工具，禁止被外部页面嵌入
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, _index_html(), "text/html; charset=utf-8")
            return
        if self.path == "/api/info":
            p = self.config.provider
            payload = {
                "model": p.model,
                "base_url": p.base_url,
                "label": p.label or p.name,
                "workspace": str(self.config.workspace),
                "has_key": bool(p.api_key),
            }
            self._send(200, json.dumps(payload, ensure_ascii=False).encode(), "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if self.path != "/api/run":
            self._send(404, b"not found", "text/plain")
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            self._send(400, b'{"error":"\\u8bf7\\u6c42\\u4f53\\u4e0d\\u662f JSON"}', "application/json")
            return

        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            self._send(400, b'{"error":"\\u4efb\\u52a1\\u4e0d\\u80fd\\u4e3a\\u7a7a"}', "application/json")
            return

        history = payload.get("history") or []

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def emit(obj: dict) -> None:
            self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()

        async def drive() -> None:
            async with Agent(self.config) as agent:
                # 把界面上的历史对话恢复进上下文，保证多轮连续
                from .providers import Message
                for turn in history[-20:]:
                    role = turn.get("role")
                    content = str(turn.get("content") or "")
                    if role in ("user", "assistant") and content:
                        agent.messages.append(Message(role=role, content=content))

                async for step in agent.run(prompt):
                    emit({"kind": step.kind, "text": step.text,
                          "tool": step.tool_name, "ok": step.tool_ok,
                          "subagent": step.subagent})

        try:
            asyncio.run(drive())
        except ProviderError as exc:
            emit({"kind": "error", "text": str(exc)})
        except Exception as exc:  # 兜底：任何异常都要让界面看到，而不是静默断流
            emit({"kind": "error", "text": f"内部错误：{exc}"})
        emit({"kind": "done"})


def serve(host: str = "127.0.0.1", port: int = 8765, workspace: Path | None = None,
          provider: str | None = None, open_browser: bool = True) -> int:
    """启动本地服务。只监听回环地址——这是本机工具，不该暴露到局域网。"""
    Handler.config = load_config(provider_name=provider, workspace=workspace or Path.cwd())
    if not Handler.config.provider.api_key:
        print("未提供 API 密钥。请设置环境变量 JANCODE_API_KEY 后重试。")
        return 2

    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"JanCode Agent 界面已启动：{url}")
    print(f"工作目录 {Handler.config.workspace}")
    print(f"模型 {Handler.config.provider.model} @ {Handler.config.provider.base_url}")
    print("按 Ctrl+C 停止。\n")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0
