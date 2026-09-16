"""环境自检。

存在理由：使用者不写程序。当工具不工作时，他需要的是
「哪一步不对、下一步该做什么」，而不是一段 Python 报错。

所以每一项检查都要给三种东西：检查了什么、结果如何、不对时怎么办。
最后一项最关键——只报告问题而不给做法，等于没帮上忙。
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import BUILTIN_PROVIDERS, DEFAULT_CONFIG_PATH, AgentConfig, load_config

OK = "ok"
WARN = "warn"
BAD = "bad"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", fix: str = "") -> None:
        self.checks.append(Check(name, status, detail, fix))

    @property
    def worst(self) -> str:
        if any(c.status == BAD for c in self.checks):
            return BAD
        if any(c.status == WARN for c in self.checks):
            return WARN
        return OK


def check_python() -> Check:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 11):
        return Check("Python 版本", OK, detail)
    return Check("Python 版本", BAD, f"{detail}（需要 3.11 以上）",
                 "安装 3.12：macOS 用 brew install python@3.12；"
                 "Windows 到 python.org 下载并勾选 Add to PATH")


def check_config(path: Path | None = None, provider: str | None = None) -> tuple[Check, AgentConfig | None]:
    path = path or DEFAULT_CONFIG_PATH
    exists = path.is_file()
    try:
        cfg = load_config(path=path, provider_name=provider)
    except ValueError as exc:
        return Check("配置文件", BAD, str(exc).splitlines()[0],
                     f"检查 {path} 的内容，或删掉它改用内置预设"), None

    if exists:
        return Check("配置文件", OK, f"已读取 {path}"), cfg
    return Check("配置文件", OK, f"未找到，使用内置预设（{path} 可选）"), cfg


def check_key(cfg: AgentConfig) -> Check:
    if cfg.provider.api_key:
        # 只显示长度，不显示内容——日志和截图里不该出现密钥
        return Check("API 密钥", OK, f"已设置（长度 {len(cfg.provider.api_key)}）")
    return Check("API 密钥", BAD, "未设置",
                 "设置环境变量：export JANCODE_API_KEY=sk-xxx（Windows 用 setx）\n"
                 "密钥在你中转站的「令牌」页面创建")


def check_workspace(cfg: AgentConfig) -> Check:
    ws = Path(cfg.workspace)
    if not ws.is_dir():
        return Check("工作目录", BAD, f"{ws} 不存在", f"创建它，或用 --workspace 指定别的目录")
    probe = ws / ".jancode-write-test"
    try:
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("工作目录", BAD, f"{ws} 不可写（{exc}）",
                     "换一个有写权限的目录，或用 --workspace 指定")
    return Check("工作目录", OK, str(ws))


async def check_connection(cfg: AgentConfig, timeout: float = 15.0) -> Check:
    """真正发一次请求。

    这是唯一能回答「到底能不能用」的检查。前面几项都过了但网络不通、
    密钥无效的情况很常见，只查配置是查不出来的。
    """
    import httpx

    base = cfg.provider.base_url.rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg.provider.api_key:
        headers["Authorization"] = f"Bearer {cfg.provider.api_key}"

    # 故意用一个空消息列表：服务端会因参数不全而报错，
    # 但鉴权发生在参数校验之前——所以 4xx 里能区分出「密钥对不对」。
    payload = {"model": cfg.provider.model, "messages": [], "max_tokens": 1}

    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(url, headers=headers, json=payload)
    except httpx.ConnectError:
        return Check("连接中转站", BAD, f"连不上 {base}",
                     "检查网络；确认地址没写错；如果是自建站，确认服务在运行")
    except httpx.TimeoutException:
        return Check("连接中转站", BAD, f"{base} 响应超时",
                     "网络可能不通，或站点负载过高")
    except httpx.HTTPError as exc:
        return Check("连接中转站", BAD, f"{base} 请求失败：{exc}", "检查地址与网络")

    body = resp.text[:200]
    if resp.status_code == 401:
        return Check("连接中转站", BAD, f"{base} 拒绝了这个密钥（401）",
                     "密钥无效或已过期。到中转站的「令牌」页面重新创建一个。\n"
                     f"站点原始报错：{body}")
    if resp.status_code == 403:
        return Check("连接中转站", BAD, f"{base} 拒绝了访问（403）",
                     "密钥没问题但权限不足——可能是该密钥没有被授予任何模型，"
                     "需要管理员在后台分配。")
    if resp.status_code == 404:
        return Check("连接中转站", BAD, f"{base} 上不存在 /chat/completions（404）",
                     "base_url 可能多了或少了 /v1，两种写法都试一下。")
    if resp.status_code >= 500:
        return Check("连接中转站", BAD, f"{base} 返回 {resp.status_code}",
                     "对方服务器出错。如果是自建站，去看服务日志。")
    if resp.status_code < 400:
        return Check("连接中转站", OK, f"{base} 正常响应")
    # 4xx 但不是鉴权错误：说明地址和密钥都对，只是我们发的参数不全
    return Check("连接中转站", OK, f"{base} 已连通（返回 {resp.status_code}，符合预期）")


async def diagnose(cfg: AgentConfig, config_path: Path | None = None,
                   provider: str | None = None, live: bool = True) -> Report:
    """对给定配置做自检。

    注意 cfg 是权威来源，不会被 config_path 覆盖。
    早前这里会重新加载一遍配置，导致调用方传进来的 cfg 被静默丢弃——
    传了参数却被忽略，是最难排查的一类问题。
    config_path 只用于报告「配置文件是否存在」。
    """
    report = Report()
    report.checks.append(check_python())

    cfg_check, _ = check_config(config_path, provider)
    if cfg_check.status == BAD:
        report.checks.append(cfg_check)
        return report
    report.checks.append(cfg_check)

    report.checks.append(check_key(cfg))
    report.checks.append(check_workspace(cfg))

    report.add(
        "模型供应商", OK,
        f"{cfg.provider.label or cfg.provider.name} · {cfg.provider.model}\n"
        f"     {cfg.provider.base_url} · 协议 {cfg.provider.wire_api}",
    )

    if live and cfg.provider.api_key:
        report.checks.append(await check_connection(cfg))
    elif live:
        report.add("连接中转站", WARN, "跳过（没有密钥无法测试）",
                   "设好密钥后重新运行 jancode-agent doctor")

    return report


def render(report: Report) -> str:
    marks = {OK: "✓", WARN: "!", BAD: "✗"}
    lines: list[str] = ["", "JanCode Agent 环境自检", "─" * 40, ""]
    for c in report.checks:
        lines.append(f"  {marks[c.status]} {c.name}")
        if c.detail:
            for line in c.detail.splitlines():
                lines.append(f"      {line}")
        if c.fix and c.status != OK:
            lines.append("")
            for line in c.fix.splitlines():
                lines.append(f"      → {line}")
        lines.append("")

    lines.append("─" * 40)
    if report.worst == OK:
        lines.append("一切正常，可以开始用了：jancode-agent --web")
    elif report.worst == WARN:
        lines.append("基本可用，但上面标 ! 的地方建议处理。")
    else:
        lines.append("上面标 ✗ 的地方需要处理，按 → 后面的提示操作即可。")
    lines.append("")
    return "\n".join(lines)
