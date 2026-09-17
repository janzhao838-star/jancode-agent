# -*- coding: utf-8 -*-
"""技能管理接口：/api/skills 的 HTTP 层行为。

库层（library）已有完整测试，这里盯接口层：路由接对、错误
信息回给界面、内置技能装/卸走通。
"""

import json
import urllib.request

import pytest

from jancode_agent.config import AgentConfig, ProviderConfig
import jancode_agent.library as lib
from jancode_agent.server import build_server


@pytest.fixture()
def api(tmp_path, monkeypatch):
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
        url + "/api/skills",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_新增技能落盘(api):
    d = post(api, {"name": "发版", "description": "发版流程", "content": "先跑测试再打包"})
    assert d["ok"] is True
    skills = lib.list_skills()
    assert [s.name for s in skills] == ["发版"]
    assert skills[0].content == "先跑测试再打包"


def test_缺名字或缺内容被拒(api):
    d = post(api, {"name": "", "content": "x"})
    assert d["ok"] is False and "名字" in d["error"]
    d = post(api, {"name": "只有名字"})
    assert d["ok"] is False and "内容" in d["error"]
    assert lib.list_skills() == []


def test_删除不存在报错(api):
    d = post(api, {"delete": "没这条"})
    assert d["ok"] is False and "没有这条技能" in d["error"]


def test_同名覆盖(api):
    post(api, {"name": "a", "content": "v1"})
    post(api, {"name": "a", "content": "v2"})
    assert [s.content for s in lib.list_skills()] == ["v2"]


def test_装内置技能再卸载(api):
    from jancode_agent.library import BUILTIN_SKILLS
    name = BUILTIN_SKILLS[0]["name"]
    d = post(api, {"install": name})
    assert d["ok"] is True
    assert name in [s.name for s in lib.list_skills()]
    d = post(api, {"uninstall": name})
    assert d["ok"] is True
    assert name not in [s.name for s in lib.list_skills()]


def test_装不存在的内置技能报错(api):
    d = post(api, {"install": "不存在的技能xyz"})
    assert d["ok"] is False and "没有这条内置技能" in d["error"]
