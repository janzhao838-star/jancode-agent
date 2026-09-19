# -*- coding: utf-8 -*-
"""静态交叉检查：界面与后端对不对得上。

查四项：script 块语法、元素 ID、页面容器、接口名。

三条踩过的坑，写在这里免得后人重犯（前两版都因此误报过）：
1. 元素 ID 不只有 id="x"，还有 JS 里 el.id='x' 赋的值，
   以及从数组字面量取出来赋的（i.id=r[0]）。后者静态查不出来，
   所以单独列成「需人工确认」，不混进「缺失」里——误报会让人
   不再相信这个脚本，那它就白写了。
2. 后端路由有否定写法（if self.path != "/api/run"），
   只找 path == "..." 会把真实存在的接口当成缺失。
3. 界面里的 /api/tags 是 Ollama 服务的接口，不是本项目的。
   所以只统计 fetch('/api/...') 这种同源调用。

用法：python scripts/crosscheck.py   （退出码非 0 表示发现真问题）
"""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 默认 cp1252，中文必炸
import pathlib
import re
import subprocess
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parent.parent
html = (root / "src/jancode_agent/web/index.html").read_text(encoding="utf-8")
server = (root / "src/jancode_agent/server.py").read_text(encoding="utf-8")
problems = []
notes = []

# ---------- 1) script 块语法 ----------
blocks = [b for b in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S) if b.strip()]
bad = 0
for i, body in enumerate(blocks):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(body)
        path = f.name
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    if r.returncode != 0:
        bad += 1
        problems.append("script 块 #%d 语法错误: %s" % (i, r.stderr.strip().split(chr(10))[0][:160]))
    pathlib.Path(path).unlink()
print("1) script 块: %d 个，语法错误 %d 个" % (len(blocks), bad))

# ---------- 2) 元素 ID ----------
literal = set(re.findall(r'id="([^"]+)"', html))
literal |= set(re.findall(r"\.id\s*=\s*['\"]([A-Za-z0-9_-]+)['\"]", html))
used = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", html))
used |= set(re.findall(r"querySelector(?:All)?\(['\"]#([A-Za-z0-9_-]+)['\"]\)", html))
# 以字符串形式出现在别处的 ID：可能来自数组字面量后在 JS 里赋值
quoted = set(re.findall(r"['\"]([A-Za-z][A-Za-z0-9_-]{2,})['\"]", html))
dynamic = sorted(u for u in used if u not in literal and u in quoted)
missing = sorted(u for u in used if u not in literal and u not in quoted)
print("2) 元素 ID: 静态定义 %d 个，JS 引用 %d 个，真缺失 %d 个，需人工确认 %d 个" % (
    len(literal), len(used), len(missing), len(dynamic)))
for m in missing:
    problems.append("JS 引用了不存在的元素 ID: #" + m)
for d in dynamic:
    notes.append("动态创建、未能静态确认: #" + d)

# ---------- 3) 页面容器 ----------
views_defined = set(re.findall(r'id="view-([a-z0-9_-]+)"', html))
views_nav = set(re.findall(r'data-view="([a-z0-9_-]+)"', html))
missing_views = sorted(v for v in views_nav if v not in views_defined)
print("3) 页面容器: 定义 %d 个，导航指向 %d 个，指向了但不存在的 %d 个" % (
    len(views_defined), len(views_nav), len(missing_views)))
for v in missing_views:
    problems.append("导航指向不存在的页面容器: view-" + v)

# ---------- 4) 接口名 ----------
routes = set(re.findall(r"['\"](/api/[a-z0-9_/-]+)['\"]", server))
called = set(re.findall(r"fetch\(['\"](/api/[a-z0-9_/-]+)", html))
missing_api = sorted(c for c in called if c not in routes)
print("4) 接口: 后端 %d 个，界面同源调用 %d 个，调用了但没有的 %d 个" % (
    len(routes), len(called), len(missing_api)))
for a in missing_api:
    problems.append("界面调用了后端没有的接口: " + a)

print()
if notes:
    print("需人工确认 %d 项（不算问题）:" % len(notes))
    for n in notes:
        print("  ·", n)
    print()
if problems:
    print("发现 %d 个真问题:" % len(problems))
    for p in problems:
        print("  ✗", p)
    sys.exit(1)
print("四项交叉检查全部通过。")
print("后端接口:", " ".join(sorted(routes)))
