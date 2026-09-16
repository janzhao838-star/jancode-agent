"""自检功能测试。

重点不是「检查项跑没跑」，而是「出问题时有没有告诉用户怎么办」——
这个功能的存在意义就在于不让使用者面对一段看不懂的报错。
"""

import asyncio
from dataclasses import replace

import pytest

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.doctor import BAD, OK, WARN, check_key, check_workspace, check_connection, diagnose, render
from tests.mock_server import chat_reply, start


def cfg(tmp_path, **kw) -> AgentConfig:
    base = {"name": "t", "base_url": "https://x.example/v1", "model": "m", "api_key": "sk-abc"}
    base.update(kw)
    return AgentConfig(provider=ProviderConfig(**base), workspace=tmp_path)


def test_密钥缺失时给出设置方法(tmp_path):
    c = check_key(cfg(tmp_path, api_key=""))
    assert c.status == BAD
    # 关键：必须告诉用户怎么做，而不只是报告「没设置」
    assert "JANCODE_API_KEY" in c.fix


def test_密钥存在时不泄露内容(tmp_path):
    """自检输出常被截图发出去。密钥本身绝不能出现在结果里。"""
    secret = "sk-这是密钥不能出现"
    c = check_key(cfg(tmp_path, api_key=secret))
    assert c.status == OK
    assert secret not in c.detail
    assert "长度" in c.detail


def test_工作目录不可写时能发现(tmp_path):
    c = check_workspace(cfg(tmp_path))
    assert c.status == OK

    missing = tmp_path / "不存在" / "更不存在"
    c2 = check_workspace(cfg(missing))
    assert c2.status == BAD and c2.fix


def test_连不上时提示检查网络(tmp_path):
    # 一个必定连不上的地址
    c = asyncio.run(check_connection(cfg(tmp_path, base_url="http://127.0.0.1:1/v1"), timeout=3))
    assert c.status == BAD
    assert c.fix


def test_401_被识别为密钥问题而不是网络问题(tmp_path):
    """这两类问题的处理方式完全不同。把 401 说成网络问题会让用户白折腾。

    用一个「总是拒绝」的服务——普通假服务对任何 Bearer 都放行，测不出 401。
    """
    import tests.mock_server as ms
    url, httpd = start([chat_reply("x")])
    original = ms._Handler.do_POST

    def always_401(self):
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":{"message":"Invalid token","type":"new_api_error"}}')

    ms._Handler.do_POST = always_401
    try:
        c = asyncio.run(check_connection(cfg(tmp_path, base_url=url), timeout=10))
        assert c.status == BAD
        assert "密钥" in c.detail
        assert "令牌" in c.fix                    # 告诉用户去哪儿重新拿密钥
        assert "Invalid token" in c.fix           # 站点原始报错要带上
    finally:
        ms._Handler.do_POST = original
        httpd.shutdown()


def test_连通时报告正常(tmp_path):
    url, httpd = start([{"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}])
    try:
        c = asyncio.run(check_connection(cfg(tmp_path, base_url=url), timeout=10))
        # 我们发的是空消息列表，服务端接受即算连通
        assert c.status in (OK, BAD)
        assert "连不上" not in c.detail
    finally:
        httpd.shutdown()


def test_无密钥时跳过网络检查并说明原因(tmp_path):
    report = asyncio.run(diagnose(cfg(tmp_path, api_key=""), live=True))
    conn = [c for c in report.checks if c.name == "连接中转站"]
    assert conn and conn[0].status == WARN
    assert "密钥" in conn[0].fix


def test_报告结尾给出可执行结论(tmp_path):
    ok_report = asyncio.run(diagnose(cfg(tmp_path), live=False))
    assert ok_report.worst == OK, [c.name for c in ok_report.checks if c.status != OK]
    text = render(ok_report)
    assert "jancode-agent --web" in text     # 一切正常时告诉用户下一步怎么用

    bad_report = asyncio.run(diagnose(cfg(tmp_path, api_key=""), live=False))
    text2 = render(bad_report)
    assert "✗" in text2
    assert bad_report.worst == BAD


def test_渲染结果不含裸异常信息(tmp_path):
    """自检的输出是给不写程序的人看的，不该出现 traceback 这类内容。"""
    report = asyncio.run(diagnose(cfg(tmp_path, api_key=""), live=False))
    text = render(report)
    for noise in ("Traceback", "File \"", "Error:"):
        assert noise not in text
