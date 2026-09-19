# -*- coding: utf-8 -*-
"""web_get / browser_read 的行为测试。

不 mock httpx 也不 mock Chrome：起一个真实本地 HTTP 服务，
让工具真的发请求、真的渲染。唯一的例外是 HTML 抽取器，
它是纯函数，直接喂字符串。
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jancode_agent.browser import (
    MAX_PAGE_CHARS,
    _check_url,
    _html_to_text,
    browser_read,
    web_get,
)
from jancode_agent.tools import READ_ONLY_TOOLS, Toolbox


PAGE = (
    "<html><head><title>测试页</title>"
    "<style>body{color:red}</style></head>"
    "<body><script>var secret=1;</script>"
    "<h1>标题一</h1><p>第一段正文</p><div>第二段正文</div></body></html>"
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默访问日志
        pass

    def do_GET(self):
        if self.path == "/page":
            body = PAGE.encode("utf-8")
            ctype = "text/html; charset=utf-8"
        elif self.path == "/api":
            body = json.dumps({"ok": True, "n": 1}).encode("utf-8")
            ctype = "application/json"
        elif self.path == "/big":
            body = ("<p>" + "字" * (MAX_PAGE_CHARS + 500) + "</p>").encode("utf-8")
            ctype = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server_url():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


# ---------- 纯函数 ----------

def test_extract_drops_script_and_style():
    title, text = _html_to_text(PAGE)
    assert title == "测试页"
    assert "标题一" in text and "第一段正文" in text
    assert "secret" not in text and "color:red" not in text


def test_extract_survives_bad_html():
    title, text = _html_to_text("<p>残缺")
    assert "残缺" in text


# ---------- URL 守卫 ----------

def test_url_guard_rejects_non_http():
    for bad in ("", "file:///etc/hosts", "ftp://x", "example.com"):
        ok, msg = asyncio.run(web_get(bad))
        assert not ok and msg  # 拒绝且说明原因（空网址、非 http(s) 各有专属提示）


def test_url_guard_in_readonly_mode():
    # 只读模式下 web_get 放行（读网页无副作用），write_file 照旧拦截
    tb = Toolbox("/tmp", mode="readonly")
    assert tb.policy_error("web_get") is None
    assert tb.policy_error("browser_read") is None
    assert "不允许执行" in tb.policy_error("write_file")


# ---------- web_get（真实 HTTP） ----------

def test_web_get_html(server_url):
    ok, msg = asyncio.run(web_get(server_url + "/page"))
    assert ok and "HTTP 200" in msg and "测试页" in msg and "第一段正文" in msg


def test_web_get_json(server_url):
    ok, msg = asyncio.run(web_get(server_url + "/api"))
    assert ok and '"ok": true' in msg


def test_web_get_truncates(server_url):
    ok, msg = asyncio.run(web_get(server_url + "/big"))
    assert ok
    body = msg.split(chr(10)*2, 1)[1] if chr(10)*2 in msg else msg
    assert len(body) <= MAX_PAGE_CHARS


def test_web_get_404(server_url):
    ok, msg = asyncio.run(web_get(server_url + "/missing"))
    assert not ok and "404" in msg


# ---------- browser_read（真实无头 Chrome） ----------

def test_browser_read_renders(server_url):
    ok, msg = asyncio.run(browser_read(server_url + "/page"))
    assert ok and "测试页" in msg and "第一段正文" in msg
    assert "secret" not in msg  # 抽取器把脚本剥掉了


def test_browser_read_guard():
    ok, msg = asyncio.run(browser_read("file:///etc/hosts"))
    assert not ok and "http(s)" in msg


def test_browser_read_no_chrome(monkeypatch):
    import jancode_agent.browser as b
    monkeypatch.setattr(b, "_find_chrome", lambda: None)
    ok, msg = asyncio.run(browser_read("https://example.com"))
    assert not ok and "web_get" in msg  # 报错里给出替代方案


def test_toolbox_forwards(server_url):
    tb = Toolbox("/tmp")
    res = asyncio.run(tb.call("web_get", {"url": server_url + "/api"}))
    assert res.ok and res.render()


def test_new_tools_listed_in_specs():
    tb = Toolbox("/tmp")
    names = {t["function"]["name"] for t in tb.specs()}
    assert {"web_get", "browser_read"} <= names
    assert {"web_get", "browser_read"} <= READ_ONLY_TOOLS
