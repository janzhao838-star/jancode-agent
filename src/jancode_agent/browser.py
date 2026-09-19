# -*- coding: utf-8 -*-
"""网页读取：智能体的「浏览器后台处理」能力。

两个工具都无副作用，所以放进 READ_ONLY_TOOLS——计划/只读模式下
也能用。这是刻意的设计：读网页和 grep 一样是收集信息，不是改动。

web_get       直接 httpx 抓取。快（毫秒级），适合静态页和 JSON API。
browser_read  无头 Chrome 渲染后取最终 DOM。慢（数秒），但能执行
              页面 JS，适合 SPA 和「打开后才知道内容」的页面。

为什么不用 AppleScript 驱动用户正在用的 Chrome：第一次调用会触发
TCC 权限弹窗，工具调用会一直挂起等用户点「允许」，而且用户不点
就永远挂着。无头 Chrome 用独立临时 profile，零权限、零打扰。

为什么不做点击/输入这类交互：CDP 需要 WebSocket 客户端，而本项目
的运行时依赖只有 httpx。诚实把边界写在工具说明里，比假装能交互
然后随机失败好。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from html.parser import HTMLParser

# 正文最多带多少字符回上下文，防止一个巨页把对话撑爆
MAX_PAGE_CHARS = 4000
# web_get 的超时
FETCH_TIMEOUT = 30.0
# browser_read 的总超时（Chrome 启动 + 加载 + 虚拟时间预算都要算在内）
BROWSER_TIMEOUT = 60.0
# 无头 Chrome 渲染的虚拟时间预算：页面 JS 在这期间可以跑完
VIRTUAL_TIME_BUDGET = 10000

# 常见浏览器 UA。有些站点看到无 UA 或奇怪 UA 会拒绝或降级响应。
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _check_url(url: str) -> str | None:
    """返回错误说明，None 表示放行。只允许 http(s)。

    为什么拦 file://：智能体读本地文件应该走 read_file（有工作目录
    边界），不能借浏览器绕过它读任意路径。
    """
    u = (url or "").strip()
    if not u:
        return "url 不能为空。"
    if not (u.startswith("http://") or u.startswith("https://")):
        return "url 必须是 http(s) 地址，例如 https://example.com"
    return None


class _TextExtract(HTMLParser):
    """把 HTML 变成干净正文：丢掉 script/style，保留 title 和可见文本。"""

    _SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in self._SKIP:
            self._skip_depth += 1
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title += data
        else:
            self._parts.append(data)

    def text(self) -> str:
        # 压掉多余空行，块级元素之间只留一个换行
        raw = "".join(self._parts)
        lines = [ln.strip() for ln in raw.split("\n")]
        return "\n".join(ln for ln in lines if ln)


def _html_to_text(html: str) -> tuple[str, str]:
    """返回 (标题, 正文)。解析失败时退化为剥标签的正则，正文不能因为
    一个坏标签就整个拿不到。"""
    p = _TextExtract()
    try:
        p.feed(html)
        p.close()
        title, body = p.title.strip(), p.text()
        if body or title:
            return title, body
    except Exception:
        pass
    stripped = re.sub(r"(?s)<[^>]+>", " ", html)
    return "", re.sub(r"\s+", " ", stripped).strip()


async def web_get(url: str) -> tuple[bool, str]:
    """直接抓取网页。返回 (是否成功, 给模型看的文本)。"""
    err = _check_url(url)
    if err:
        return False, err
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(FETCH_TIMEOUT, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": _UA},
        ) as client:
            resp = await client.get(url.strip())
    except httpx.HTTPError as exc:
        return False, "抓取失败：" + str(exc)[:300]
    ctype = resp.headers.get("content-type", "")
    head = "HTTP " + str(resp.status_code) + " · " + url.strip()
    if resp.status_code >= 400:
        return False, head + "（请求出错，页面内容不可用）"
    body = resp.text
    if "json" in ctype or body[:1] in ("{", "["):
        return True, head + "（JSON）\n" + body[:MAX_PAGE_CHARS]
    title, text = _html_to_text(body)
    if not text:
        return True, head + "（页面没有可提取的正文，可能是纯图片或空白页）"
    out = head
    if title:
        out += "\n标题：" + title
    return True, out + "\n\n" + text[:MAX_PAGE_CHARS]


def _find_chrome() -> str | None:
    """找到可用的 Chrome。找不到就明说，别报一个莫名其妙的 FileNotFoundError。"""
    for cand in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome") or "",
        shutil.which("google-chrome-stable") or "",
        shutil.which("chrome") or "",
        shutil.which("chromium") or "",
    ):
        if cand and (cand.startswith("/") and os.path.exists(cand) or shutil.which(cand)):
            return cand
    return None


# DOM 读取的字节上限（超出按截断处理，防一个巨页拖垮内存）
MAX_DOM_CHARS = 20 * 1024 * 1024

async def browser_read(url: str) -> tuple[bool, str]:
    """无头 Chrome 渲染后取正文。适合需要执行 JS 的页面。"""
    err = _check_url(url)
    if err:
        return False, err
    chrome = _find_chrome()
    if chrome is None:
        return False, "本机找不到 Chrome，browser_read 用不了；可以直接用 web_get 抓取。"
    profile = tempfile.mkdtemp(prefix="jancode-browser-")
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--user-data-dir=" + profile,
        "--virtual-time-budget=" + str(VIRTUAL_TIME_BUDGET),
        "--dump-dom",
        "--timeout=30000",
        url.strip(),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            limit=MAX_DOM_CHARS,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        shutil.rmtree(profile, ignore_errors=True)
        return False, "启动 Chrome 失败：" + str(exc)[:200]
    # 实测：Chrome 输出完 DOM 之后经常不退出（用户自己的 Chrome 开着时
    # 尤其如此），等 communicate() 会白等到天荒地老。所以不靠进程退出，
    # 读到完整 HTML（</html> 收尾）就主动收工杀进程；页面没正常收尾时
    # （404、JSON）拿到多少算多少。
    try:
        chunk = await asyncio.wait_for(
proc.stdout.readuntil(b"</html>"), timeout=BROWSER_TIMEOUT
        )
        out = chunk + b"</html>"
    except asyncio.IncompleteReadError as exc:
        out = exc.partial
    except asyncio.TimeoutError:
        proc.kill()
        shutil.rmtree(profile, ignore_errors=True)
        return False, "Chrome 渲染超时（" + str(BROWSER_TIMEOUT) + " 秒），页面可能太重或网络太慢。"
    finally:
        if proc.returncode is None:
            proc.kill()
    shutil.rmtree(profile, ignore_errors=True)  # 临时 profile 用完就删
    dom = out.decode("utf-8", "replace")
    if not dom.strip():
        return False, "Chrome 没有返回任何内容，页面可能加载失败。可改用 web_get 试试。"
    title, text = _html_to_text(dom)
    if not text:
        return True, "页面渲染完成但没有可提取的正文：" + url.strip()
    out_head = "已渲染 · " + url.strip()
    if title:
        out_head = out_head + chr(10) + "标题：" + title
    return True, out_head + chr(10) + chr(10) + text[:MAX_PAGE_CHARS]

