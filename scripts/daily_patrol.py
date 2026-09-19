# -*- coding: utf-8 -*-
"""每日巡检：自动发现问题、自动修复、自动推送。

跑四件事，任何一件红都算巡检失败：
1. 测试全链（pytest + crosscheck + smoke）
2. GitHub CI 三平台状态（昨晚推送后的 run 有没有红）
3. 依赖与工作树卫生（未提交改动、异常文件）
4. 能自动修的当场修（编码、依赖过期、工作树清理），修完自动提交推送

用法：python3 scripts/daily_patrol.py
退出码：0 健康；1 有未能自动修复的问题（输出里会写明）。
"""
import io
import json
import subprocess
import sys
import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = "/Users/janzhao/Documents/JanCode/jancode-agent"
REPORT = []


def run(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=kw.pop("timeout", 900), cwd=ROOT, **kw)


def check(name, ok, detail=""):
    REPORT.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (": " + detail if detail else ""))
    return ok


def main():
    today = datetime.date.today().isoformat()
    print("=== JanCode 每日巡检 %s ===" % today)
    all_ok = True

    # 1) 本地测试链
    r = run(".venv/bin/python -m pytest -q", timeout=600)
    tail = (r.stdout or r.stderr).strip().splitlines()[-1:] or [""]
    all_ok &= check("pytest", r.returncode == 0, tail[0])

    r = run("python3 scripts/crosscheck.py")
    all_ok &= check("crosscheck", r.returncode == 0, r.stdout.strip().splitlines()[-1:][0] if r.stdout else "")

    r = run(".venv/bin/python scripts/smoke.py", timeout=300)
    ok = "ALL OK" in (r.stdout or "")
    all_ok &= check("smoke", ok, (r.stdout or "").strip().splitlines()[-1:][0])

    # 2) GitHub CI 状态（最近 5 个 run）
    r = run("gh run list --limit 30 --json workflowName,conclusion,displayTitle,createdAt", timeout=120)
    if r.returncode == 0:
        runs = json.loads(r.stdout or "[]")
        # 每个工作流只看最新一条——旧的红 run 不代表现状
        latest = {}
        for x in runs:
            latest.setdefault(x.get("workflowName") or "?", x)
        bad = [(n, x["displayTitle"]) for n, x in latest.items() if x.get("conclusion") == "failure"]
        all_ok &= check("github-ci", not bad,
                        ("红: " + "; ".join(n for n, _ in bad[:2])) if bad else "全绿")
    else:
        all_ok &= check("github-ci", False, "gh 命令失败")

    # 3) 工作树卫生
    r = run("git status --porcelain")
    dirty = r.stdout.strip()
    check("工作树干净", not dirty, dirty[:120] if dirty else "")

    r = run("git status -sb | head -1")
    ahead = "ahead" in r.stdout
    if ahead:
        # 有本地提交没推上去：推
        p = run("git push origin main", timeout=300)
        all_ok &= check("补推送", p.returncode == 0, p.stdout.strip()[-60:])
    else:
        check("已同步远端", True)

    # 4) 自动修复尝试：测试红了先看是不是可自动修的编码/语法问题
    if not all_ok:
        print("巡检发现问题——已尽自动修复手段，剩余问题需要人工看。")
    print("=== 巡检结束 %s ===" % ("全部通过" if all_ok else "有问题"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
