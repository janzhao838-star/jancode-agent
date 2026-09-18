#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行时 HTTP 冒烟：起真实服务，逐个端点打一遍。

和 tests/ 的区别：tests 用 build_server 的进程内对象断言返回值，
这里走完整 HTTP 栈——属性名写错（.text vs .output）、序列化
炸掉、路由漏接，只有真请求才暴露。抓到过真 bug。

用法：python scripts/smoke.py   （全过输出 ALL OK，退出码 0）
"""

import json
import pathlib
import sys
import tempfile
import threading
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.server import build_server


def main() -> int:
    checks = []

    def ck(name, cond):
        checks.append((name, bool(cond)))

    tmp = pathlib.Path(tempfile.mkdtemp())
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x:1/v1", model="m", api_key="k"),
        workspace=tmp,
    )
    # 设置文件必须隔离：POST /api/settings 这一步以前会把假配置
    # (y:2/mm) 直接写进用户真实的 desktop-settings.json，还把选中
    # 的接入方式切走，跑完冒烟界面上的模型就变成了 mm。
    httpd = build_server(port=0, config=cfg, require_key=False,
                         settings_path=tmp / "desktop-settings.json")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]

    def get(path):
        try:
            with urllib.request.urlopen(base + path) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(body)
            except ValueError:
                return exc.code, body

    def post(path, body):
        req = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    html = urllib.request.urlopen(base + "/").read().decode("utf-8")
    ck("主页含标题", "JanCode" in html)

    for path in ["/api/info", "/api/vendors", "/api/models", "/api/tools",
                 "/api/files", "/api/providers", "/api/mcp", "/api/skills",
                 "/api/agents", "/api/automations", "/api/settings"]:
        status, d = get(path)
        ck("GET " + path, status == 200 and isinstance(d, dict) and d.get("ok") is not False)

    for path, body in [
        ("/api/skills", {"name": "冒烟", "content": "测试"}),
        ("/api/agents", {"name": "冒烟手"}),
        ("/api/automations", {"name": "冒烟任务", "prompt": "hi",
                              "schedule": "每天 09:00", "enabled": True}),
        ("/api/settings", {"api_key": "kk", "base_url": "http://y:2/v1", "model": "mm"}),
    ]:
        try:
            d = post(path, body)
            ck("POST " + path, d.get("ok") is True)
        except Exception as exc:
            ck("POST " + path + " -> " + repr(exc)[:40], False)

    status, d = get("/api/badendpoint")
    ck("未知端点 404", status == 404)

    # 安全边界：越界路径必须回绝（200+ok=false 或 4xx），绝不 5xx/断连
    for path in ["/api/files?path=../../", "/api/file?path=../../etc/passwd",
                 "/api/file?path=/etc/passwd", "/api/file?path="]:
        try:
            status, d = get(path)
            ok = status == 200 and isinstance(d, dict) and d.get("ok") is False and d.get("error")
        except Exception:
            ok = False
        ck("越界回绝 " + path[:36], ok)

    httpd.shutdown()
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    bad = [n for n, ok in checks if not ok]
    print("RESULT:", "ALL OK (%d)" % len(checks) if not bad else "FAILED %d" % len(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
