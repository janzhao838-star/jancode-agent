"""命令行入口。

支持两种用法：
    jancode "帮我看看这个项目"     一次任务
    jancode                        交互模式

输出刻意做得很轻——每一步只打一行，工具结果折叠显示。
接 API 的智能体会产生大量中间输出，全量打印会淹没真正要看的东西。
"""

from __future__ import annotations


# Windows 控制台默认是 GBK，中文/符号输出会直接抛 UnicodeEncodeError 把进程打崩。
# 这里强制成 UTF-8 并允许替换无法编码的字符：宁可个别字符变成问号，也不能崩。
def _force_utf8_output() -> None:
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_output()

import argparse
import asyncio
import sys
from pathlib import Path

from . import __version__
from .agent import Agent
from .config import BUILTIN_PROVIDERS, load_config
from .providers import ProviderError


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jancode",
        description="JanCode Agent —— 开源终端 AI 编程助手，面向国内大模型与自建中转站",
    )
    p.add_argument("prompt", nargs="*", help="要交给智能体的任务。留空则进入交互模式。")
    p.add_argument("--provider", help=f"供应商，可选：{'、'.join(sorted(BUILTIN_PROVIDERS))}")
    p.add_argument("--base-url", help="直接指定接口地址，覆盖供应商预设")
    p.add_argument("--model", help="直接指定模型名，覆盖供应商预设")
    p.add_argument("--api-key", help="API 密钥。建议改用环境变量 JANCODE_API_KEY")
    p.add_argument("--workspace", default=".", help="工作目录，默认当前目录")
    p.add_argument("--max-steps", type=int, help="工具调用循环上限")
    p.add_argument("--no-bash", action="store_true", help="禁止执行 shell 命令")
    p.add_argument("--no-subagents", action="store_true", help="禁用子智能体（不提供 task 工具）")
    p.add_argument("--list-providers", action="store_true", help="列出内置供应商后退出")
    p.add_argument("--web", action="store_true", help="启动浏览器图形界面，而不是命令行")
    p.add_argument("--port", type=int, default=8765, help="图形界面的端口，默认 8765")
    p.add_argument("--no-open", action="store_true", help="启动图形界面时不自动打开浏览器")
    p.add_argument("--doctor", action="store_true", help="检查环境并诊断问题")
    p.add_argument("--offline", action="store_true", help="自检时不发网络请求")
    p.add_argument("--version", action="version", version=f"jancode-agent {__version__}")
    return p


def _show_providers() -> None:
    print("内置供应商：\n")
    width = max(len(k) for k in BUILTIN_PROVIDERS)
    for name, spec in sorted(BUILTIN_PROVIDERS.items()):
        print(f"  {name:<{width}}  {spec['label']}")
        print(f"  {'':<{width}}  {spec['base_url']}  默认模型 {spec['model']}")
    print("\n用法：jancode --provider janzhao \"你的任务\"")
    print("密钥通过环境变量传入：export JANCODE_API_KEY=sk-...")


async def _run_once(agent: Agent, prompt: str, verbose: bool) -> int:
    from .agent import Step

    exit_code = 0
    async for step in agent.run(prompt):
        # 子智能体的步骤缩进一层。不做区分的话，主智能体和子智能体的工具调用
        # 会混在同一个缩进级别上，看不出到底是谁在干活。
        indent = "    " if step.subagent else "  "
        if step.kind == "tool":
            # 工具「开始」和「结束」是两个 step，用 text 是否为空区分
            if not step.text:
                args = ", ".join(f"{k}={v!r}" for k, v in list(step.tool_args.items())[:2])
                print(f"{indent}⚙ {step.tool_name}({args[:100]})", flush=True)
            elif verbose:
                mark = "✓" if step.tool_ok else "✗"
                body = step.text.strip().splitlines()
                shown = body[:6]
                for i, line in enumerate(shown):
                    print(f"{indent}  {mark if i == 0 else ' '} {line[:160]}")
                if len(body) > len(shown):
                    print(f"{indent}    …（另有 {len(body) - len(shown)} 行）")
        elif step.kind == "answer":
            if step.subagent:
                # 子智能体的结论会作为工具结果回流给主智能体，这里只提示一句。
                # 直接打印会和主智能体的最终答复混淆——用户分不清哪个是结论。
                print(f"{indent}✓ 子智能体「{step.subagent}」已给出结论")
            else:
                print(f"\n{step.text}\n")
        elif step.kind == "error":
            who = f"子智能体「{step.subagent}」：" if step.subagent else ""
            print(f"\n⚠ {who}{step.text}\n", file=sys.stderr)
            # 子智能体失败不算主任务失败：失败原因会作为工具结果回流，
            # 主智能体还有机会自己接手或换个思路。
            if not step.subagent:
                exit_code = 1
    return exit_code


async def _interactive(agent: Agent, verbose: bool) -> int:
    print(f"JanCode Agent {__version__}  工作目录 {agent.config.workspace}")
    print("模型 " + agent.config.provider.model + "  @" + agent.config.provider.base_url)
    print("输入任务后回车执行；/exit 退出，/clear 清空上下文。\n")
    while True:
        try:
            line = input("❯ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/clear":
            agent.messages = agent.messages[:1]
            print("已清空上下文。\n")
            continue
        await _run_once(agent, line, verbose)


def build_config(args) -> AgentConfig:
    """把命令行参数装配成运行配置。

    单独抽出来给桌面版复用：桌面版要的是同一套覆盖规则（--base-url / --model /
    --no-subagents …），各写一份迟早会漂移——上次 --web 忽略 --api-key 就是这么来的。
    """
    from dataclasses import replace

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"工作目录不存在：{workspace}")

    try:
        config = load_config(provider_name=args.provider, workspace=workspace)
    except ValueError as exc:
        raise ValueError(f"配置错误：{exc}") from exc

    # 命令行参数覆盖配置文件
    provider = config.provider
    if getattr(args, "base_url", None):
        provider = replace(provider, base_url=args.base_url.rstrip("/"))
    if getattr(args, "model", None):
        provider = replace(provider, model=args.model)
    if getattr(args, "api_key", None):
        provider = replace(provider, api_key=args.api_key)
    config = replace(config, provider=provider)
    if getattr(args, "max_steps", None):
        config = replace(config, max_steps=args.max_steps)
    if getattr(args, "no_bash", False):
        config = replace(config, allow_bash=False)
    if getattr(args, "no_subagents", False):
        config = replace(config, allow_subagents=False)
    return config


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.list_providers:
        _show_providers()
        return 0

    try:
        config = build_config(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.doctor:
        # 自检放在命令行覆盖**之后**：以前它自己又 load_config 了一遍，
        # 于是 --api-key / --base-url / --model 全被忽略——用户拿
        # 「jancode --doctor --api-key sk-xxx」排查密钥问题，检查的却是默认供应商，
        # 报出来的结论是反的。用上面装配好的 config 才对。
        from .doctor import diagnose, render
        report = asyncio.run(diagnose(config, provider=args.provider, live=not args.offline))
        print(render(report))
        return 0 if report.worst != "bad" else 1

    if args.web:
        # 图形界面在 server 内部自行校验密钥，这里不重复检查。
        # 必须把上面装配好的 config 整个传进去：只传供应商名字的话，
        # --api-key / --base-url / --model / --no-bash / --no-subagents
        # 这些命令行覆盖会静默失效（服务端会重新从环境变量和配置文件读一遍）。
        from .server import serve
        return serve(
            port=args.port,
            config=config,
            open_browser=not args.no_open,
        )

    if not config.provider.api_key:
        print(
            "未提供 API 密钥。\n"
            "  可以设置环境变量：export JANCODE_API_KEY=sk-...\n"
            "  或者用参数：jancode --api-key sk-... \"任务\"",
            file=sys.stderr,
        )
        return 2

    async def _main() -> int:
        async with Agent(config) as agent:
            prompt = " ".join(args.prompt).strip()
            if prompt:
                return await _run_once(agent, prompt, verbose=True)
            return await _interactive(agent, verbose=True)

    try:
        return asyncio.run(_main())
    except ProviderError as exc:
        print(f"模型调用失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
