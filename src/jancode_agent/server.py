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

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json")

    def _notify(self) -> None:
        """把一段文本推到企业微信群机器人。

        只收 https：http 会把任务结论明文发到网上。这种「图省事」的写法一旦被
        抄进教程就到处流传，所以这里直接拒绝，而不是加一句注释提醒。
        """
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except (ValueError, UnicodeDecodeError):
            self._json({"ok": False, "error": "请求体不是 JSON"}, 400)
            return

        url = str(payload.get("webhook") or "").strip()
        text = str(payload.get("text") or "").strip()[:4000]
        if not url.startswith("https://"):
            self._json({"ok": False, "error": "webhook 必须是 https 地址"})
            return
        if not text:
            self._json({"ok": False, "error": "推送内容为空"})
            return

        try:
            import httpx

            resp = httpx.post(
                url,
                json={"msgtype": "markdown", "markdown": {"content": text}},
                timeout=10.0,
            )
        except Exception as exc:
            self._json({"ok": False, "error": f"推送失败：{exc}"})
            return

        if resp.status_code >= 400:
            self._json({"ok": False, "error": f"对方返回 {resp.status_code}"})
            return
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if data.get("errcode"):
            self._json({"ok": False, "error": f"企业微信返回 {data.get('errcode')}：{data.get('errmsg', '')}"})
            return
        self._json({"ok": True})

    def do_POST(self) -> None:
        if self.path == "/api/notify":
            self._notify()
            return
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


class MissingApiKey(RuntimeError):
    """没配密钥。

    单独一个异常类型，是为了让调用方分辨「配置没弄好」和「端口被占了」
    这两类完全不同的失败——它们的处理方式不一样。
    """


def build_server(host: str = "127.0.0.1", port: int = 8765, workspace: Path | None = None,
                 provider: str | None = None,
                 config: AgentConfig | None = None) -> ThreadingHTTPServer:
    """装配好配置、建好服务，但**不启动**。

    把「建」和「跑」分开是为了桌面版：它要在后台线程里跑 serve_forever，
    并在窗口关掉时自己 shutdown。两者揉在一起的话，桌面版只能另抄一份出来，
    以后改一处忘一处。
    """
    Handler.config = config or load_config(
        provider_name=provider, workspace=workspace or Path.cwd())
    if not Handler.config.provider.api_key:
        raise MissingApiKey(
            "未提供 API 密钥。请设置环境变量 JANCODE_API_KEY 后重试。")
    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str = "127.0.0.1", port: int = 8765, workspace: Path | None = None,
          provider: str | None = None, open_browser: bool = True,
          config: AgentConfig | None = None) -> int:
    """启动本地服务。只监听回环地址——这是本机工具，不该暴露到局域网。

    config 是命令行已经装配好的配置（含 --api-key/--base-url/--model 等覆盖），
    有就直接用；没有才按 provider 名字现查一遍。
    两种都留着是为了不破坏直接调 serve() 的用法。

    为什么必须能传：以前这里只会拿到供应商**名字**，于是界面重新去读环境变量和
    配置文件——用参数给密钥的人看到的是「未提供 API 密钥」，而 --no-bash、
    --no-subagents 这些开关更是被静默丢掉，用户以为禁掉了其实没有。
    """
    try:
        httpd = build_server(host, port, workspace, provider, config)
    except MissingApiKey as exc:
        print(str(exc))
        return 2

    url = f"http://{host}:{port}/"
    # 每行都 flush：输出重定向到文件时 stdout 是块缓冲，不 flush 的话
    # 用户（和读日志的脚本）要等缓冲区满才看得到"已启动"。
    print(f"JanCode Agent 界面已启动：{url}", flush=True)
    print(f"工作目录 {Handler.config.workspace}", flush=True)
    print(f"模型 {Handler.config.provider.model} @ {Handler.config.provider.base_url}", flush=True)
    print("按 Ctrl+C 停止。\n", flush=True)

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0
