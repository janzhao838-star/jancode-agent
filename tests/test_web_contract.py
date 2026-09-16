"""前后端数据契约测试。

存在理由：网页界面和服务端是两个文件，很容易各改各的。
之前钧子AI 上就踩过一次——后端写 'detail'、前端读 'a'，页面静默不显示内容，
接口测试全绿却没人发现。

这类问题的共同点：**两边单独测都没错，错在接缝上。**
所以这里把前端真正读的字段名从 HTML 里抠出来，再验证服务端确实发了。
"""

import json
import re
import socket
import threading
import urllib.request
from pathlib import Path

import pytest

from jancode_agent.server import WEB_DIR, Handler, _index_html

INDEX = (WEB_DIR / "index.html").read_text(encoding="utf-8")


def test_前端读的事件字段服务端都发了():
    """从 JS 里抠出 ev.xxx 的取值，和服务端实际发出的字段比对。"""
    # JS 里所有 ev.<字段>
    read_fields = set(re.findall(r"\bev\.([A-Za-z_][A-Za-z0-9_]*)", INDEX))
    assert read_fields, "没能从界面里解析出任何字段引用，测试本身可能失效了"

    # 服务端 /api/run 实际发出的字段。
    # 刻意从 server.py 源码里抠，而不是写死一份名单——写死的话，
    # 服务端加了字段、界面也读了，这个测试却还盯着旧名单，等于白测。
    import inspect
    from jancode_agent import server as server_module
    from jancode_agent.agent import Step
    step_fields = set(Step.__dataclass_fields__)
    payload_fields: set[str] = set()
    for call_site in re.finditer(r"emit\(\{(.*?)\}\)", inspect.getsource(server_module), re.S):
        payload_fields |= set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', call_site.group(1)))
    assert payload_fields, "没能从 server.py 里解析出 emit 的字段，测试本身可能失效了"

    missing = read_fields - payload_fields
    assert not missing, (
        f"界面读取了这些字段，但服务端没有发送：{sorted(missing)}。"
        f"服务端目前发送的是 {sorted(payload_fields)}。"
    )
    # 顺便确认 Step 的字段没有和 emit 的键脱节
    assert {"text", "tool_name", "tool_ok"} <= step_fields


def test_前端认识的_kind_服务端都会发():
    """界面按 kind 分支处理。服务端要是发了界面不认识的 kind，那条事件会被静默丢弃。"""
    handled = set(re.findall(r"ev\.kind\s*===\s*'([a-z]+)'", INDEX))
    assert handled, "没能解析出界面处理的 kind"

    import inspect
    from jancode_agent import agent as agent_module
    # 扫整个 agent 模块而不是只看 Agent.run：步骤是在哪一层产出的属于实现细节，
    # 契约只关心「这个模块会不会发出界面不认识的 kind」。
    emitted = set(re.findall(r'Step\("([a-z]+)"', inspect.getsource(agent_module)))
    emitted |= {"done"}   # server.py 结束时会补一个 done

    unhandled = emitted - handled
    assert not unhandled, (
        f"服务端会发送这些 kind，但界面没有处理：{sorted(unhandled)}。"
        f"界面认识的是 {sorted(handled)}。"
    )
    # 反过来：界面处理的 kind 服务端也得真会发，否则是死代码
    assert not (handled - emitted), f"界面处理了服务端不会发的 kind：{sorted(handled - emitted)}"


def test_pyproject_把界面文件声明成了包数据():
    """回归的是一个很隐蔽的打包 bug。

    setuptools 默认只打包 .py，web/index.html 属于数据文件，不显式声明就会从 wheel 里
    丢掉。而安装脚本用的是 editable 安装（-e），它直接读源码目录，所以本地怎么试都是好的；
    只有普通安装之后才会发现 site-packages 里没有这个文件，
    于是每个界面请求都抛 FileNotFoundError——服务起来了，页面却打不开。
    """
    import tomllib
    root = Path(__file__).resolve().parent.parent
    cfg = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    data = cfg["tool"]["setuptools"]["package-data"]
    assert any("web" in pattern for pattern in data["jancode_agent"]), data


def test_界面文件确实被打包进服务():
    body = _index_html()
    assert b"<!DOCTYPE html>" in body
    assert "JanCode Agent" in body.decode("utf-8")


def test_serve_用传进来的配置而不是重新读一遍(monkeypatch, tmp_path):
    """服务端必须用命令行装配好的配置。

    回归的是这个 bug：--web 以前只把供应商**名字**传过去，服务端于是重新读环境变量
    和配置文件，结果命令行给的 --api-key/--base-url/--model 全部失效，
    --no-bash/--no-subagents 也被丢掉——用户以为禁掉了 shell，其实没禁。
    """
    from jancode_agent import server as sm
    from jancode_agent.config import AgentConfig, ProviderConfig

    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://127.0.0.1:1/v1",
                                model="cli-模型", api_key="sk-cli"),
        workspace=tmp_path, max_steps=1, allow_bash=False, allow_subagents=False,
    )

    class FakeHTTPD:
        def __init__(self, addr, handler):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt      # 立刻结束，不真的监听

        def server_close(self):
            pass

    monkeypatch.setattr(sm, "ThreadingHTTPServer", FakeHTTPD)
    assert sm.serve(port=0, config=cfg, open_browser=False) == 0
    assert sm.Handler.config is cfg, "服务端没有采用传入的配置"
    assert sm.Handler.config.allow_bash is False
    assert sm.Handler.config.allow_subagents is False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_流式输出真的是逐行_JSON(tmp_path):
    """界面用 NDJSON：每行一个 JSON。要是服务端改成整块返回，
    JS 的按行解析会拿到半截 JSON 然后静默跳过——界面看起来就是「没有任何反应」。"""
    from dataclasses import replace
    from jancode_agent.config import AgentConfig, ProviderConfig

    port = _free_port()
    Handler.config = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://127.0.0.1:1/v1",
                                model="m", api_key="sk-x"),
        workspace=tmp_path, max_steps=1,
    )
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/run",
            data=json.dumps({"prompt": "测试"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8")
    finally:
        httpd.shutdown()

    assert "ndjson" in ctype, f"内容类型应是 ndjson，实际是 {ctype}"
    lines = [l for l in raw.split("\n") if l.strip()]
    assert lines, "没有任何输出"
    for line in lines:
        obj = json.loads(line)          # 每行都必须是完整合法 JSON
        assert "kind" in obj
    assert lines[-1] and json.loads(lines[-1])["kind"] == "done", "最后一行应是 done"
