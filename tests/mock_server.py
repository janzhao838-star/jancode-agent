"""一个最小的 OpenAI 兼容服务，用于端到端测试。

为什么要它：单元测试里用假客户端替换掉了整个 HTTP 层，
协议构造、响应解析、流式推送这些真实路径其实没被验证过。
这个服务在本地端口上讲真实协议，让智能体真的发 HTTP 请求。

它按脚本回答：第一轮要求调用工具，第二轮给出最终答案。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    script: list[dict] = []
    seen: list[dict] = []

    def log_message(self, *a: object) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        _Handler.seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})

        if not self.headers.get("Authorization", "").startswith("Bearer "):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": {"code": "", "message": "Invalid token", "type": "new_api_error"}
            }).encode())
            return

        reply = _Handler.script.pop(0) if _Handler.script else {"content": "（脚本用尽）"}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(reply, ensure_ascii=False).encode())


def start(script: list[dict]) -> tuple[str, "ThreadingHTTPServer"]:
    _Handler.script = list(script)
    _Handler.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address
    return f"http://{host}:{port}/v1", httpd


def chat_reply(content: str = "", tool_calls: list[dict] | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"total_tokens": 1}}


def call(cid: str, name: str, args: dict) -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
