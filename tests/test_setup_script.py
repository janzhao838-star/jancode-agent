# -*- coding: utf-8;
"""中转站一键接入脚本：真实运行验证。

脚本承诺的事必须真的发生：写出的配置能被 load_config 读回、
--list 能列出模型、401 时给出可操作的提示。全部用真实 HTTP 服务验。
"""
import json
import os
import pathlib
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jancode_agent.config import load_config


@pytest.fixture()
def relay(tmp_path):
    """一个假中转站：/v1/models 与 /v1/chat/completions。

    密钥是 sk-good，其他密钥回 401——验证脚本的密钥错误提示。
    """
    class H(BaseHTTPRequestHandler):
        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/v1/models":
                if self.headers.get("Authorization") != "Bearer sk-good":
                    return self._send(401, {"error": {"message": "Invalid token"}})
                return self._send(200, {"data": [{"id": "model-a"}, {"id": "model-b"}]})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path == "/v1/chat/completions":
                if self.headers.get("Authorization") != "Bearer sk-good":
                    return self._send(401, {"error": {"message": "Invalid token"}})
                return self._send(200, {"choices": [{"message": {"content": "pong"}}]})
            self._send(404, {"error": "not found"})

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """隔离的 HOME + 假 jancode-agent 命令，脚本跑在沙箱里不碰真实环境。"""
    # 开发机上常驻 OPENAI_*/JANCODE_* 变量（接别的客户端用的）。
    # load_config 在测试进程内运行，环境覆盖会把它们抬到配置文件之上——
    # 不清掉的话，脚本明明写对了也会被读成别的站。
    for k in [k for k in os.environ if k.upper().startswith(("OPENAI_", "JANCODE_"))]:
        monkeypatch.delenv(k, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "jancode-agent"
    stub.write_text("#!/bin/sh\necho \"jancode-agent 0.1.0 (stub)\"\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, HOME=str(home), PATH=f"{bin_dir}:{os.environ["PATH"]}")
    # 开发机上常驻 OPENAI_*/JANCODE_* 变量（接别的客户端用的）。
    # load_config 的环境覆盖会把它们抬到配置文件之上，测试必须隔离，
    # 否则脚本明明写对了也会被读成别的站。
    for k in list(env):
        if k.upper().startswith(("OPENAI_", "JANCODE_")):
            env.pop(k)
    # 中文输出的脚本在非 UTF-8 locale 下 decode 会炸，钉死编码
    env["LC_ALL"] = "en_US.UTF-8"
    return home, env


def run_script(env, *args):
    root = pathlib.Path(__file__).resolve().parent.parent
    return subprocess.run(
        ["bash", str(root / "scripts/setup-jancode.sh"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=60,
    )


def test_full_run_writes_valid_config(relay, sandbox):
    home, env = sandbox
    r = run_script(env, "--url", relay + "/v1", "--key", "sk-good")
    assert r.returncode == 0, r.stderr
    conf = home / ".jancode-agent/config.toml"
    assert conf.is_file()
    # 写出的配置必须能被 load_config 直接读回——这是脚本的硬承诺
    cfg = load_config(conf)
    assert cfg.provider.base_url == relay + "/v1"
    assert cfg.provider.api_key == "sk-good"
    assert cfg.provider.model == "model-a"  # 清单第一个
    # 沙箱内 HOME 隔离，不应碰真实用户目录
    assert "model-a" in conf.read_text()


def test_list_prints_models(relay, sandbox):
    _, env = sandbox
    r = run_script(env, "--list", "--url", relay, "--key", "sk-good")
    assert r.returncode == 0, r.stderr
    assert "model-a" in r.stdout and "model-b" in r.stdout


def test_bad_key_gives_actionable_error(relay, sandbox):
    home, env = sandbox
    r = run_script(env, "--url", relay + "/v1", "--key", "sk-bad")
    assert r.returncode == 1
    # 报错要指出去哪换密钥，而不是甩一个 exit code
    assert "令牌" in (r.stdout + r.stderr)
    assert "401" in (r.stdout + r.stderr)


def test_url_without_v1_gets_it_appended(relay, sandbox):
    home, env = sandbox
    r = run_script(env, "--url", relay, "--key", "sk-good")
    assert r.returncode == 0, r.stderr
    cfg = load_config(home / ".jancode-agent/config.toml")
    assert cfg.provider.base_url == relay + "/v1"


def test_existing_config_backed_up(relay, sandbox):
    home, env = sandbox
    conf_dir = home / ".jancode-agent"
    conf_dir.mkdir()
    (conf_dir / "config.toml").write_text("[provider]\nmodel = \"user-custom\"\n")
    r = run_script(env, "--url", relay + "/v1", "--key", "sk-good")
    assert r.returncode == 0, r.stderr
    backups = list(conf_dir.glob("config.toml.bak.*"))
    assert backups, "原配置必须先备份"
    assert "user-custom" in backups[0].read_text()


def test_missing_key_aborts(relay, sandbox):
    _, env = sandbox
    r = run_script(env, "--url", relay + "/v1")
    assert r.returncode == 1
    assert "--key" in (r.stdout + r.stderr)
