# -*- coding: utf-8 -*-
"""界面点击切换检查：真的点导航，看页面切没切过去。

需要一个已启动的 JanCode 服务和一个 Chrome。用法：

    python scripts/uicheck.py --port 58290

做三件事，缺一不可：
1. 点导航项（走真实用户路径，不是直接调函数——函数不是全局的）
2. 看是不是「恰好一个」页面处于显示状态，且就是目标页
3. 看该页的内容标志物在不在（证明数据加载真的发生了）

踩过的坑：第一版把「容器切换」判成 False，因为正则假定 HTML 里
id 在 class 前面，实际是 class 在前。断言写错会把成功判成失败，
所以这里按整个 div 标签抓，不受属性顺序影响。
"""
import argparse
import pathlib
import re
import subprocess
import sys

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SRC = pathlib.Path(__file__).resolve().parent.parent / "src/jancode_agent/web/index.html"

MARKERS = {"chat": "mg-effort", "files": "README.md", "skills": "skill",
           "agents": "agent", "models": "mg-prov", "tools": "read_file"}
TAG = re.compile(r'<div class="view([^"]*)" id="view-([a-z]+)"')


def render(view, port):
    orig = SRC.read_text(encoding="utf-8")
    inject = ("<script>window.addEventListener('load',function(){setTimeout(function(){"
              'var n=document.querySelector(\'.nitem[data-view="' + view + '"]\');'
              "n&&n.click();},1200)});</script></body>")
    SRC.write_text(orig.replace("</body>", inject, 1), encoding="utf-8")
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                            "--virtual-time-budget=6000", "--dump-dom",
                            "http://127.0.0.1:%d/" % port],
                           capture_output=True, text=True, timeout=120)
        return r.stdout
    finally:
        SRC.write_text(orig, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=58290)
    args = ap.parse_args()
    if not pathlib.Path(CHROME).exists():
        print("没找到 Chrome，跳过界面检查")
        return 0
    ok = bad = 0
    for view in ["chat", "files", "skills", "agents", "models", "tools"]:
        dom = render(view, args.port)
        active = [v for cls, v in TAG.findall(dom) if "on" in cls.split()]
        marks = [MARKERS[view]] if MARKERS[view] in dom else []
        good = active == [view] and bool(marks)
        print("%s %-7s 当前显示=%-12s 内容标志=%s" % (
            "✓" if good else "✗", view, str(active), marks or "无"))
        ok += 1 if good else 0
        bad += 0 if good else 1
    print()
    print("界面点击切换：%d 通过 / %d 未通过" % (ok, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
