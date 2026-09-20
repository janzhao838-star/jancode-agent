# -*- coding: utf-8 -*-
"""视觉与技能安装：模型真正看得见图、粘贴 SKILL.md 能直接装。

之前图片工具结果被当成文本塞进上下文：一张 jpg 几 MB 的 base64
把中转站打到拒单，表现是「流式响应里没有任何内容」。现在图片走
多模态分段。技能这边，用户从别处复制整份 SKILL.md（带 YAML
frontmatter）粘贴进来要能直接装，而不是把 --- 头原样入库。
"""

import base64
import json
import urllib.request

import pytest

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.mcp import MCPClient
from jancode_agent.providers import Message
from jancode_agent.server import build_server
from jancode_agent.tools import Toolbox


# ---------- wire 格式 ----------

def test_无图消息wire仍是纯字符串():
    m = Message(role="user", content="你好")
    wire = m.to_wire()
    assert wire["content"] == "你好"

def test_带图消息wire是多模态分段():
    uri = "data:image/png;base64,aGVsbG8="
    m = Message(role="tool", content="看这张图", images=[uri], tool_call_id="c1")
    wire = m.to_wire()
    assert isinstance(wire["content"], list)
    assert wire["content"][0] == {"type": "text", "text": "看这张图"}
    assert wire["content"][1]["type"] == "image_url"
    assert wire["content"][1]["image_url"]["url"] == uri
    assert wire["tool_call_id"] == "c1"

def test_带图无文本也合法():
    uri = "data:image/jpeg;base64,x="
    wire = Message(role="user", images=[uri]).to_wire()
    assert wire["content"][0]["type"] == "text"  # 占位文本段
    assert wire["content"][1]["type"] == "image_url"


# ---------- 内置 read_image 工具 ----------

def _png(tmp_path, name="a.png", blob=b"\x89PNG fake"):
    p = tmp_path / name
    p.write_bytes(blob)
    return p


@pytest.fixture()
def box(tmp_path):
    return Toolbox(tmp_path)


@pytest.mark.asyncio
async def test_read_image把图作为多模态结果带回(box, tmp_path):
    p = _png(tmp_path)
    r = await box.read_image(str(p))
    assert r.ok is True
    assert len(r.images) == 1
    assert r.images[0].startswith("data:image/png;base64,")
    assert base64.b64decode(r.images[0].split(",", 1)[1]) == b"\x89PNG fake"


@pytest.mark.asyncio
async def test_read_image拒绝不认识的格式(box, tmp_path):
    p = tmp_path / "doc.txt"
    p.write_text("纯文本")
    r = await box.read_image(str(p))
    assert r.ok is False
    assert "不是支持的图片格式" in r.output
    assert r.images == []


@pytest.mark.asyncio
async def test_read_image文件不存在(box, tmp_path):
    r = await box.read_image(str(tmp_path / "nope.png"))
    assert r.ok is False


@pytest.mark.asyncio
async def test_read_image在工具表里且算只读(box):
    names = {t["function"]["name"] for t in box.specs()}
    assert "read_image" in names
    from jancode_agent.tools import READ_ONLY_TOOLS
    assert "read_image" in READ_ONLY_TOOLS


# ---------- MCP 图片块 ----------

def test_mcp图片块转多模态而不是base64文本(monkeypatch):
    client = MCPClient("fs", "noop", [])
    raw = {"content": [
        {"type": "text", "text": "截图如下"},
        {"type": "image", "mimeType": "image/jpeg", "data": "aGVsbG8="},
    ], "isError": False}
    monkeypatch.setattr(client, "_rpc", lambda method, args: raw)
    text, images = client.call_tool_full("read_media_file", {})
    assert text == "截图如下"
    assert images == ["data:image/jpeg;base64,aGVsbG8="]


def test_mcp纯文本结果不受影响(monkeypatch):
    client = MCPClient("fs", "noop", [])
    raw = {"content": [{"type": "text", "text": "ok"}], "isError": False}
    monkeypatch.setattr(client, "_rpc", lambda method, args: raw)
    text, images = client.call_tool_full("t", {})
    assert text == "ok" and images == []


# ---------- 技能：粘贴 SKILL.md 直接装 ----------

@pytest.fixture()
def server(tmp_path, monkeypatch):
    from jancode_agent import library as lib
    monkeypatch.setattr("jancode_agent.server.SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(lib, "SKILLS_PATH", tmp_path / "skills.json")
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://old:1234/v1", model="m1", api_key="k"),
        workspace=tmp_path,
    )
    httpd = build_server(port=0, config=cfg, require_key=False,
                         settings_path=tmp_path / "settings.json")
    port = httpd.server_address[1]
    import threading
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _post_skill(base, body):
    req = urllib.request.Request(
        base + "/api/skills",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_粘贴整份SKILL_MD自动抽名和描述(server):
    md = "\n".join([
        "---",
        "name: pdf-export",
        "description: 把文档导出为 PDF",
        "---",
        "",
        "第一步：打开文档。",
        "第二步：点导出。",
    ])
    out = _post_skill(server, {"content": md})
    assert out["ok"] is True
    from jancode_agent import library as lib
    skills = {s.name: s for s in lib.list_skills()}
    assert "pdf-export" in skills
    assert skills["pdf-export"].description == "把文档导出为 PDF"
    assert skills["pdf-export"].content.startswith("第一步")
    assert "---" not in skills["pdf-export"].content


def test_无frontmatter时首行标题当名字(server):
    out = _post_skill(server, {"content": "# 周报套路\n按这个结构写。"})
    assert out["ok"] is True
    from jancode_agent import library as lib
    names = {s.name for s in lib.list_skills()}
    assert "周报套路" in names


def test_空内容仍被拒绝(server):
    out = _post_skill(server, {"name": "x", "content": ""})
    assert out["ok"] is False


# ---------- 归档卸图 ----------

def test_归档时旧图片一并卸下():
    from jancode_agent.agent import Agent
    from jancode_agent.config import AgentConfig, ProviderConfig
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x:1/v1", model="m", api_key="k"),
        workspace=None,
    )
    agent = Agent.__new__(Agent)  # 不跑 __init__，只测纯函数段
    uri = "data:image/png;base64," + "A" * 4000
    big = "x" * 6000
    from jancode_agent.providers import Message as M
    agent.messages = [M(role="tool", content=big, images=[uri], name="read_image", tool_call_id="c")]
    n = agent._maybe_archive(budget=1000, keep_recent=0)
    assert n == 1
    assert agent.messages[0].images == []
    assert len(agent.messages[0].content) < len(big)

def test_没超预算不归档也不卸图():
    from jancode_agent.agent import Agent
    from jancode_agent.providers import Message as M
    agent = Agent.__new__(Agent)
    agent.messages = [M(role="tool", content="short", images=["data:image/png;base64,x"])]
    assert agent._maybe_archive(budget=100000, keep_recent=0) == 0
    assert agent.messages[0].images == ["data:image/png;base64,x"]

