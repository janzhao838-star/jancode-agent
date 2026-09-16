"""命令行入口。

支持两种用法：
    jancode "帮我看看这个项目"     一次任务
    jancode                        交互模式

输出刻意做得很轻——每一步只打一行，工具结果折叠显示。
接 API 的智能体会产生大量中间输出，全量打印会淹没真正要看的东西。
"""

from __future__ import annotations

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
    print("\n用法：jancode --provider aionclaw \"你的任务\"")
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.list_providers:
        _show_providers()
        return 0

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        print(f"工作目录不存在：{workspace}", file=sys.stderr)
        return 2

    if args.doctor:
        from .doctor import diagnose, render
        cfg = load_config(provider_name=args.provider, workspace=workspace)
        report = asyncio.run(diagnose(cfg, provider=args.provider, live=not args.offline))
        print(render(report))
        return 0 if report.worst != "bad" else 1


    try:
        config = load_config(provider_name=args.provider, workspace=workspace)
    except ValueError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    # 命令行参数覆盖配置文件
    from dataclasses import replace
    provider = config.provider
    if args.base_url:
        provider = replace(provider, base_url=args.base_url.rstrip("/"))
    if args.model:
        provider = replace(provider, model=args.model)
    if args.api_key:
        provider = replace(provider, api_key=args.api_key)
    config = replace(config, provider=provider)
    if args.max_steps:
        config = replace(config, max_steps=args.max_steps)
    if args.no_bash:
        config = replace(config, allow_bash=False)
    if args.no_subagents:
        config = replace(config, allow_subagents=False)

    if args.web:
        # 图形界面在 server 内部自行校验密钥，这里不重复检查
        from .server import serve
        return serve(
            port=args.port,
            workspace=workspace,
            provider=args.provider,
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
