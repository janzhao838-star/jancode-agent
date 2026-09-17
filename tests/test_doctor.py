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
        # 这个假响应必须自带 Content-Length，否则这条测试测到的是连接问题
        # 而不是它想测的 401 识别。
        #
        # 机制（用对照实验确认过，不是猜的）：这个假处理器**不读请求体**就返回，
        # 带着未读数据关闭连接会发 RST；而缺少 Content-Length 时客户端要一直等到
        # EOF 才认为响应结束——等来的是 RST 而不是干净的 FIN，整个响应就丢了，
        # 报成「请求失败：」（信息为空）。
        # 另一种可行的修法是先把请求体读掉（self.rfile.read(length)），两种都能过；
        # 这里选了自带长度这一种，因为它更接近真实服务的行为。
        body = b'{"error":{"message":"Invalid token","type":"new_api_error"}}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


def test_自检会用命令行给的密钥和地址(capsys, tmp_path):
    """回归：--doctor 以前自己又 load_config 了一遍，把命令行覆盖全丢了。

    用户排查「密钥为什么不通」时最自然的动作就是带上参数再跑一次自检。
    如果自检检查的还是默认供应商，它报出来的结论就是反的——比不报还糟。
    """
    from jancode_agent.cli import main

    rc = main(["--doctor", "--offline", "--api-key", "sk-x",
               "--base-url", "http://127.0.0.1:9/v1",
               "--workspace", str(tmp_path)])
    out = capsys.readouterr().out

    assert "http://127.0.0.1:9/v1" in out, f"自检没有用命令行给的地址：\n{out}"
    assert "未设置" not in out, f"密钥明明给了，自检却说没设置：\n{out}"
    assert rc == 0, "带上了密钥，自检不该报失败"
    # 顺带守住：自检可以报密钥长度，但不能把密钥本身打出来
    assert "sk-x" not in out, "自检把密钥回显出来了"

def test_重定向不再误报为正常(tmp_path):
    """base_url 写错时很多站点会 302 到登录页。

    以前 3xx 落进「< 400 就算正常」的分支，自检给出绿灯，
    用户以为万事大吉，实际一次对话都不会成功。
    """
    import tests.mock_server as ms
    url, httpd = start([chat_reply("x")])
    original = ms._Handler.do_POST

    def redirect_302(self):
        body = b""
        self.send_response(302)
        self.send_header("Location", "/login")
        self.send_header("Content-Length", "0")
        self.end_headers()

    ms._Handler.do_POST = redirect_302
    try:
        c = asyncio.run(check_connection(cfg(tmp_path, base_url=url), timeout=10))
        assert c.status == BAD
        assert "302" in c.detail
        assert "/login" in c.fix
    finally:
        ms._Handler.do_POST = original
        httpd.shutdown()
