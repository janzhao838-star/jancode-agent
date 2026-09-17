# -*- coding: utf-8 -*-
"""文件浏览接口：/api/files、/api/file 的边界与安全回绝。

越界路径必须被回绝（不崩、不泄露），错误信息要能回到界面。
此前回绝对象是 ToolResult，处理代码读了不存在的 .text 属性，
越界请求直接把连接崩掉——冒烟测试抓到的真 bug。
"""

import json
import urllib.request
import urllib.error

import pytest

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.server import build_server


@pytest.fixture()
def api(tmp_path):
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x:1/v1", model="m", api_key="k"),
        workspace=tmp_path,
    )
    httpd = build_server(port=0, config=cfg, require_key=False)
    import threading
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()


def get(api, path):
    try:
        with urllib.request.urlopen(api + path) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_列目录正常(api, tmp_path):
    (tmp_path / "子目录").mkdir()
    (tmp_path / "hi.txt").write_text("x", encoding="utf-8")
    status, d = get(api, "/api/files")
    assert status == 200 and d["ok"] is True
    names = [e["name"] for e in d["entries"]]
    assert "子目录" in names and "hi.txt" in names


def test_读文件预览(api, tmp_path):
    (tmp_path / "a.md").write_text("# 内容", encoding="utf-8")
    status, d = get(api, "/api/file?path=a.md")
    assert d["ok"] is True and "内容" in d["text"]


def test_越界路径回绝不崩不泄露(api):
    """工作目录外的路径：返回 200 + ok=false + 中文原因，绝不 500/断连。"""
    for url in ["/api/files?path=../../", "/api/file?path=../../etc/passwd",
                "/api/file?path=/etc/passwd", "/api/file?path="]:
        status, d = get(api, url)
        assert status == 200, url
        assert d["ok"] is False and d["error"], url
        assert "etc/passwd" not in json.dumps(d, ensure_ascii=False) or "超出工作目录" in d["error"], url
