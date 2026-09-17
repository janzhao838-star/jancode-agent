# -*- coding: utf-8 -*-
"""智能体管理接口：/api/agents 的写入路径。

库层已有测试；这里盯 HTTP 层的错误回传与落盘。
"""

import json
import urllib.request

import pytest

from jancode_agent.config import AgentConfig, ProviderConfig
import jancode_agent.library as lib
from jancode_agent.server import build_server


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "AGENTS_PATH", tmp_path / "agents.json")
    monkeypatch.setattr(lib, "SKILLS_PATH", tmp_path / "skills.json")
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


def post(url, body):
    req = urllib.request.Request(
        url + "/api/agents",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_新增智能体落盘(api):
    d = post(api, {"name": "前端手", "system_prompt": "只改前端", "model": "m2", "skills": []})
    assert d["ok"] is True
    assert lib.find_agent("前端手") is not None


def test_缺名字被拒(api):
    d = post(api, {"name": "  ", "system_prompt": "x"})
    assert d["ok"] is False and "名字" in d["error"]


def test_删除不存在报错(api):
    d = post(api, {"delete": "没这人"})
    assert d["ok"] is False and "没有这个智能体" in d["error"]


def test_删除存在的智能体(api):
    post(api, {"name": "甲", "system_prompt": ""})
    d = post(api, {"delete": "甲"})
    assert d["ok"] is True and lib.find_agent("甲") is None
