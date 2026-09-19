# -*- coding: utf-8 -*-
"""本地服务防跨站：浏览器里别的网页不能悄悄指挥智能体。"""

import json
import socket
import threading
import urllib.request

from http.server import ThreadingHTTPServer

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.server import Handler


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server(tmp_path, port):
    Handler.config = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://127.0.0.1:1/v1", model="m", api_key="k"),
        workspace=tmp_path, max_steps=1,
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _post(port, host, ctype):
    req = urllib.request.Request(
        "http://127.0.0.1:%d/api/run" % port,
        data=json.dumps({"prompt": "x"}).encode(),
        headers={"Content-Type": ctype, "Host": host},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_恶意的_Host_被拒(tmp_path):
    """DNS 重绑定会把 Host 换成攻击者域名：必须 403。"""
    port = _free_port()
    httpd = _server(tmp_path, port)
    try:
        assert _post(port, "evil.example.com:1", "application/json") == 403
    finally:
        httpd.shutdown()


def test_跨站简单请求被拒(tmp_path):
    """跨站表单只能发 text/plain：没有 JSON 内容类型就 415。"""
    port = _free_port()
    httpd = _server(tmp_path, port)
    try:
        assert _post(port, "127.0.0.1:%d" % port, "text/plain") == 415
    finally:
        httpd.shutdown()


def test_本机_Host_正常放行(tmp_path):
    """正常界面（回环地址 + JSON）不受影响：防线不能误伤。"""
    port = _free_port()
    httpd = _server(tmp_path, port)
    try:
        code = _post(port, "127.0.0.1:%d" % port, "application/json")
        # 不是 403/415 就说明守卫放行了（后续会因连不上网关而流式报错）
        assert code == 200
    finally:
        httpd.shutdown()