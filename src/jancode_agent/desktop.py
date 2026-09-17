"""桌面版：把界面装进一个原生窗口。

为什么是「包一层」而不是重做界面：web/index.html 已经能用了，重做只会多出
第二份要同步维护的 UI。桌面版真正要解决的是另外两件事——让不写命令的人
双击就能用，以及不依赖浏览器。

窗口用系统自带的 webview（macOS 是 WKWebView，Windows 是 WebView2），
不内嵌浏览器内核，所以安装包不会凭空多出上百 MB。
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

WINDOW_TITLE = "JanCode 智能体"


def free_port() -> int:
    """让系统分配一个空闲端口。

    不用固定端口：多开一个实例、或上一次没退干净，固定端口就会起不来，
    而用户看到的现象是「窗口白屏」——完全没有线索。让系统分配就没有这种事。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until_ready(port: int, host: str = "127.0.0.1", timeout: float = 20.0) -> bool:
    """等端口真的能连上再开窗口。

    不等的话，窗口会先加载一个还没起来的地址，用户看到的是「无法连接」，
    然后必须手动刷新——第一印象就是坏的。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.05)
    return False


def _alert(title: str, message: str) -> None:
    """用系统自己的方式弹提示框。

    桌面版从访达/开始菜单启动时是没有终端的：出错时 print 出去，
    用户什么都看不到，只会以为「双击了没反应」。所以关键错误必须弹出来。
    弹出本身失败也不该让程序崩掉，所以整段都吞异常。
    """
    try:
        if sys.platform == "darwin":
            safe = message.replace("\\", "\\\\").replace('"', '\\"')
            script = f'display alert "{title}" message "{safe}" as critical'
            subprocess.run(["osascript", "-e", script], check=False, timeout=60)
        elif sys.platform.startswith("win"):
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:
        pass


def _fail(message: str, alert: bool = True) -> int:
    print(message, file=sys.stderr, flush=True)
    # 自检是无人值守模式：弹对话框在 CI 里没人点「好」，只会把任务挂住。
    if alert:
        _alert(WINDOW_TITLE, message)
    return 2


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jancode-desktop", description="JanCode 智能体（桌面版）")
    p.add_argument("--provider", help="供应商名，可选值见 jancode --list-providers")
    p.add_argument("--base-url", help="接口地址，覆盖供应商预设")
    p.add_argument("--model", help="模型名，覆盖供应商预设")
    p.add_argument("--api-key", help="API 密钥。建议改用环境变量 JANCODE_API_KEY")
    p.add_argument("--workspace", default=str(Path.home()),
                   help="工作目录，默认用户主目录（从访达启动时当前目录是 /，不能当默认值）")
    p.add_argument("--port", type=int, default=0, help="监听端口，0 表示由系统分配")
    p.add_argument("--max-steps", type=int)
    p.add_argument("--no-bash", action="store_true", help="禁止执行 shell 命令")
    p.add_argument("--no-subagents", action="store_true", help="禁用子智能体")
    p.add_argument("--width", type=int, default=1180)
    p.add_argument("--height", type=int, default=820)
    p.add_argument("--selftest", action="store_true",
                   help="只检查环境与配置并打印结果，不开窗口（给 CI 和自动化用）")
    return p


def _tool_roundtrip(workspace: Path) -> None:
    """自检：把工具层真跑一遍。

    写 → 读 → 改 → 查 → bash 五步全部走 Toolbox.call() 完整分发，
    任何一步结果不对都抛异常，让 selftest 以非零退出。打包后的
    PyInstaller 环境里工具层是最容易「装完才发现坏了」的一层。
    """
    import asyncio
    import tempfile

    from .tools import Toolbox

    async def run() -> None:
        with tempfile.TemporaryDirectory(dir=workspace) as tmp:
            box = Toolbox(workspace=Path(tmp))

            rel = "自检.txt"
            r = await box.call("write_file", {"path": rel, "content": "第一行\n第二行"})
            assert r.ok, f"write_file 失败：{r.output}"

            r = await box.call("read_file", {"path": rel})
            assert r.ok and "第二行" in r.output, f"read_file 失败：{r.output}"

            r = await box.call("edit_file", {"path": rel, "old": "第二行", "new": "改过的行"})
            assert r.ok, f"edit_file 失败：{r.output}"

            r = await box.call("grep", {"pattern": "改过的行", "path": "."})
            assert r.ok and "自检.txt" in r.output, f"grep 失败：{r.output}"

            r = await box.call("bash", {"command": "echo 往返正常"})
            assert r.ok and "往返正常" in r.output, f"bash 失败：{r.output}"

    asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    from .cli import build_config
    from .server import MissingApiKey, build_server

    try:
        config = build_config(args)
    except ValueError as exc:
        return _fail(str(exc), alert=not args.selftest)

    # 桌面版才有这一步：双击启动没有命令行参数，环境变量也往往没有，
    # 界面里保存过的那份设置就是唯一的来源。
    from .server import apply_saved_settings

    config = apply_saved_settings(config)

    port = args.port or free_port()
    # require_key=False：没配密钥也要把界面开起来。
    # 以前这里是直接退出——用户双击得到一个弹窗就没了，而填密钥的地方
    # 恰恰就在界面里，等于把唯一的路堵死。现在改成：界面照常打开，
    # 提示去哪儿填，填完立刻能用。
    try:
        httpd = build_server(host="127.0.0.1", port=port, config=config,
                             require_key=False)
    except MissingApiKey as exc:
        return _fail(str(exc), alert=not args.selftest)
    except OSError as exc:
        return _fail(f"端口 {port} 用不了：{exc}")

    # 服务跑在后台线程；窗口关掉后由主线程 shutdown。
    # daemon=True 是兜底：万一退出路径没走到，也不至于留个进程占着端口。
    threading.Thread(target=httpd.serve_forever, name="jancode-server", daemon=True).start()
    url = f"http://127.0.0.1:{port}/"

    if not wait_until_ready(port):
        httpd.shutdown()
        httpd.server_close()
        return _fail(f"本地服务在 20 秒内没有起来（端口 {port}）。")

    # 自动化任务由桌面版负责调度：命令行/网页版是「用完就走」的用法，
    # 在那里常驻一个定时器不符合预期，还会在用户不知情时花掉额度。
    try:
        from .scheduler import start as start_scheduler

        start_scheduler(config)
    except Exception as exc:  # 调度起不来不该让整个应用打不开
        print(f"定时任务未启动：{exc}", file=sys.stderr, flush=True)

    if args.selftest:
        # 不开窗口，只确认「服务能起来 + 配置是对的」。CI 里没有显示器也能跑。
        print(f"服务已就绪：{url}", flush=True)
        print(f"工作目录 {config.workspace}", flush=True)
        print(f"模型 {config.provider.model} @ {config.provider.base_url}", flush=True)
        # 工具调用往返：打包后的 app（PyInstaller 冻结环境）里，
        # 工具层是除了 HTTP 服务之外最容易「装完才发现坏了」的一层——
        # 比如资源路径变了、asyncio 策略不同。在这里真跑一遍写读改查。
        try:
            _tool_roundtrip(config.workspace)
        except Exception as exc:
            httpd.shutdown()
            httpd.server_close()
            return _fail(f"自检工具往返失败：{exc}", alert=False)
        print("工具往返 write/read/edit/grep/bash 全部通过。", flush=True)
        print("桌面版自检通过。", flush=True)
        httpd.shutdown()
        httpd.server_close()
        return 0

    try:
        import webview
    except ImportError:
        httpd.shutdown()
        httpd.server_close()
        return _fail(
            "缺少桌面版依赖 pywebview。\n"
            "命令行安装：pip install \"jancode-agent[desktop]\"\n"
            "或者直接用网页版：jancode-agent --web")

    # 看的是服务端最终生效的配置，而不是本地这份 config：
    # 界面里保存过的设置是在 build_server 里才合并进去的，
    # 用本地这份判断会把「已经配好了」误报成「还没配」。
    if not httpd.RequestHandlerClass.config.provider.api_key:
        print("还没有配置 API 密钥：在界面右下角点「设置」填上接口地址与密钥。",
              file=sys.stderr, flush=True)
    print(f"JanCode 智能体已启动：{url}", flush=True)
    webview.create_window(WINDOW_TITLE, url, width=args.width, height=args.height,
                          min_size=(720, 520))
    try:
        webview.start()
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
