"""工具系统。

智能体的能力边界完全由这里决定。设计原则：

1. 每个工具都要有清晰的成功/失败信号——模型靠返回值判断下一步，
   返回含糊的成功会导致它在错误方向上反复尝试。
2. 失败返回的是「给模型看的说明」，不是 Python 异常堆栈。
   模型看不懂 traceback，但看得懂「文件不存在，可用 list_dir 查看」。
3. 所有文件路径都限制在工作目录内。越界的读写直接拒绝，
   不做「尽力而为」的尝试。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# 单次读取文件的行数上限。超过时截断并明确告知模型，
# 否则一个巨大的文件会把上下文直接撑爆。
MAX_READ_LINES = 2000
# 搜索结果条数上限
MAX_GREP_HITS = 200


@dataclass
class ToolResult:
    ok: bool
    output: str

    def render(self) -> str:
        return self.output


class ToolError(Exception):
    """工具参数错误。会被转成给模型看的提示。"""


# 「只读」类工具：不产生任何副作用，计划/只读模式下放行。
READ_ONLY_TOOLS = frozenset({"read_file", "list_dir", "grep", "web_get", "browser_read"})

# 这两种模式在代码层面禁止一切有副作用的工具。
NO_SIDE_EFFECT_MODES = frozenset({"readonly", "plan"})

# 认识的模式。不在这个集合里的名字按最严格处理，不按放行处理。
KNOWN_MODES = frozenset({"auto", "sandbox", "plan", "readonly"})

# sandbox 模式判「有没有写入意图」用的关键词。
_SANDBOX_WRITE_HINTS = (
    " > ", " >> ", ">>", "tee ", " cp ", " mv ", " rm ", " mkdir ",
    "touch ", "chmod ", "chown ", "dd ", "truncate "
)

# 绝对路径。故意排除前面是字母数字/点/冒号的，否则会把网址
# （https://…）和模块路径当成文件路径误拦。反引号写成 x60，
# 免得它把外层脚本截断。
_ABSOLUTE = re.compile(r'(?<![\w.:-])(/[^\s\x22\x27\x60;|&()<>]+)')


class Toolbox:
    """工具集合。绑定到一个工作目录。"""

    def __init__(
        self,
        workspace: Path,
        allow_bash: bool = True,
        bash_timeout: float = 120.0,
        max_output: int = 20_000,
        allow_subagents: bool = False,
        spawn_subagent: Callable[[str, str], Any] | None = None,
        mode: str = "auto",
    ):
        self.workspace = Path(workspace).resolve()
        self.allow_bash = allow_bash
        self.bash_timeout = bash_timeout
        self.max_output = max_output
        # 子智能体的真正执行在 Agent 层（要新建一个循环、要管深度）。
        # 工具层只做参数校验和分发，这样 Toolbox 不需要知道模型客户端怎么来。
        self.allow_subagents = allow_subagents
        self.spawn_subagent = spawn_subagent
        self.mode = (mode or "auto").strip().lower()

    # ---------- 工作模式（代码级拦截） ----------

    def policy_error(self, name: str) -> str | None:
        """按工作模式拦截工具调用，返回拒绝原因，None 表示放行。

        为什么要有这一层：四个模式原先只写进系统提示词。提示词是「请求」，
        模型不听话就直接动手改文件了。这里是「拒绝」，两者缺一不可。
        """
        if self.mode not in KNOWN_MODES:
            # 模式名不认识（拼错了、或者从旧版本传来一个废弃值）：
            # 按最严格的只读处理。静默放行是最糟的选择——
            # 用户以为自己在受限模式里，实际却全放开了。
            if name not in READ_ONLY_TOOLS:
                return (
                    f"工作模式 {self.mode!r} 无法识别，已按最严格的「只读」处理，"
                    f"因此不允许执行 {name}。请检查界面上的模式设置。"
                )
            return None
        if self.mode in NO_SIDE_EFFECT_MODES and name not in READ_ONLY_TOOLS:
            label = "计划" if self.mode == "plan" else "只读"
            return (
                f"当前是「{label}」模式，代码层面不允许执行 {name}。"
                f"这是硬拦截而不是提示，改了提示词也绕不过去。"
                f"确实需要改动，请先让用户在界面上切到「全自动」。"
            )
        return None

    def _sandbox_bash_error(self, command: str) -> str | None:
        """sandbox 模式下拒绝「看起来在往工作目录外写」的命令。

        这里是尽力而为，不是真沙箱：shell 能做的事太多，没法在字符串层面
        关进笼子。所以只拦最可能造成真实损失的一类——同时具备写入意图、
        又出现了工作目录之外的绝对路径。只读命令（看 /etc/hosts、
        which python）一律放行，否则正常工作都做不了。
        """
        if self.mode != "sandbox":
            return None
        padded = " " + command + " "
        if not any(hint in padded for hint in _SANDBOX_WRITE_HINTS):
            return None
        for match in _ABSOLUTE.finditer(command):
            raw = match.group(1)
            try:
                target = Path(raw).expanduser().resolve()
            except OSError:
                continue
            if target != self.workspace and self.workspace not in target.parents:
                return (
                    f"「限定工作目录」模式下拒绝执行：命令里有写入意图，"
                    f"又出现了工作目录之外的路径 {raw!r}。"
                    f"要动这个位置，请先跟用户确认并切到「全自动」。"
                )
        return None

    # ---------- 路径安全 ----------

    def _safe(self, raw: str) -> tuple[Path | None, ToolResult | None]:
        """把 _resolve 的异常转成给模型的失败结果。

        所有公开方法都必须经由这里解析路径，否则越界检查会因入口不同而失效。
        """
        try:
            return self._resolve(raw), None
        except ToolError as exc:
            return None, ToolResult(False, str(exc))

    def _resolve(self, raw: str) -> Path:
        """把用户/模型给的路径解析到工作目录内。

        越界一律拒绝。注意用 resolve() 之后再判断，
        这样符号链接指向外面也能被拦住。
        """
        if not raw or not raw.strip():
            raise ToolError("路径不能为空。")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ToolError(f"无法解析路径 {raw!r}：{exc}") from exc

        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise ToolError(
                f"路径 {raw!r} 超出工作目录 {self.workspace}。只能操作工作目录内的文件。"
            )
        return resolved

    def entries(self, path: str = ".") -> tuple[list[dict[str, Any]], str]:
        """列目录，返回结构化结果，出错时返回 (空列表, 原因)。

        界面单独要这一份。让它去解析 list_dir 那段「给人看的文本」太脆了：
        改个措辞就会把界面弄坏，而改的人根本不会想到界面依赖了它。
        """
        target, err = self._safe(path)
        if err is not None:
            return [], err.output
        assert target is not None
        if not target.exists():
            return [], f"目录不存在：{path}"
        if not target.is_dir():
            return [], f"{path} 不是目录。"
        try:
            found = sorted(target.iterdir(), key=lambda q: (q.is_file(), q.name.lower()))
        except OSError as exc:
            return [], f"列目录失败：{exc}"

        out: list[dict[str, Any]] = []
        for q in found[:400]:
            try:
                size = 0 if q.is_dir() else q.stat().st_size
            except OSError:
                size = 0
            out.append({"name": q.name, "dir": q.is_dir(), "size": size})
        return out, ""

    def _clip(self, text: str) -> str:
        if len(text) <= self.max_output:
            return text
        return text[: self.max_output] + f"\n…（输出过长，已截断，共 {len(text)} 字符）"

    def clip(self, text: str) -> str:
        """按 max_output 截断。任何要进入模型上下文的长文本都该过这里。"""
        return self._clip(text)

    # ---------- 工具实现 ----------

    async def read_file(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> ToolResult:
        target, err = self._safe(path)
        if err:
            return err
        assert target is not None
        if not target.exists():
            return ToolResult(False, f"文件不存在：{path}。可以先用 list_dir 查看目录内容。")
        if target.is_dir():
            return ToolResult(False, f"{path} 是目录，不是文件。请用 list_dir。")

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(False, f"读取 {path} 失败：{exc}")

        lines = text.splitlines()
        total = len(lines)
        start = max(1, int(offset))
        count = max(1, min(int(limit), MAX_READ_LINES))
        chunk = lines[start - 1 : start - 1 + count]

        # 带行号返回：模型后续要改代码时，行号是定位锚点
        numbered = "\n".join(f"{start + i:>5}\t{ln}" for i, ln in enumerate(chunk))
        head = f"{path} 共 {total} 行，显示第 {start}–{start + len(chunk) - 1} 行：\n"
        if start + len(chunk) - 1 < total:
            numbered += f"\n…（还有 {total - (start + len(chunk) - 1)} 行未显示，可用 offset 继续读）"
        return ToolResult(True, self._clip(head + numbered))

    async def write_file(self, path: str, content: str) -> ToolResult:
        denial = self.policy_error("write_file")
        if denial is not None:
            return ToolResult(False, denial)
        target, err = self._safe(path)
        if err:
            return err
        assert target is not None
        existed = target.exists()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(False, f"写入 {path} 失败：{exc}")
        verb = "覆盖" if existed else "新建"
        return ToolResult(True, f"已{verb} {path}（{len(content)} 字符，{content.count(chr(10)) + 1} 行）。")

    async def edit_file(self, path: str, old: str, new: str, replace_all: bool = False) -> ToolResult:
        denial = self.policy_error("edit_file")
        if denial is not None:
            return ToolResult(False, denial)
        """按字面量替换。

        刻意要求 old 唯一匹配：如果出现多次，说明模型对上下文判断有误，
        此时强行替换很可能改错地方。让它提供更长的上下文再来一次更安全。
        """
        target, err = self._safe(path)
        if err:
            return err
        assert target is not None
        if not target.exists():
            return ToolResult(False, f"文件不存在：{path}")
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(False, f"读取 {path} 失败：{exc}")

        hits = text.count(old)
        if hits == 0:
            return ToolResult(False, f"在 {path} 中找不到要替换的内容。请先 read_file 确认原文。")
        if hits > 1 and not replace_all:
            return ToolResult(
                False,
                f"要替换的内容在 {path} 里出现了 {hits} 次，无法确定改哪一处。"
                f"请提供更长的上下文使其唯一，或指定 replace_all=true 全部替换。",
            )

        updated = text.replace(old, new, -1 if replace_all else 1)
        try:
            target.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return ToolResult(False, f"写回 {path} 失败：{exc}")
        return ToolResult(True, f"已修改 {path}（替换 {hits if replace_all else 1} 处）。")

    async def list_dir(self, path: str = ".") -> ToolResult:
        target, err = self._safe(path)
        if err:
            return err
        assert target is not None
        if not target.exists():
            return ToolResult(False, f"目录不存在：{path}")
        if not target.is_dir():
            return ToolResult(False, f"{path} 不是目录。请用 read_file。")

        try:
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError as exc:
            return ToolResult(False, f"列目录失败：{exc}")

        if not entries:
            return ToolResult(True, f"{path} 是空目录。")

        lines = []
        for p in entries[:400]:
            if p.is_dir():
                lines.append(f"  {p.name}/")
            else:
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"  {p.name}  ({size} 字节)")
        if len(entries) > 400:
            lines.append(f"  …（另有 {len(entries) - 400} 项未显示）")
        return ToolResult(True, f"{path} 内容：\n" + "\n".join(lines))

    async def grep(self, pattern: str, path: str = ".", include: str = "") -> ToolResult:
        target, err = self._safe(path)
        if err:
            return err
        assert target is not None
        try:
            regex = __import__("re").compile(pattern)
        except Exception as exc:
            return ToolResult(False, f"正则表达式无法编译：{exc}")

        hits: list[str] = []
        files = [target] if target.is_file() else [
            p for p in target.rglob("*") if p.is_file()
        ]
        for f in files:
            if include and not f.match(include):
                continue
            # 跳过常见的大目录，避免把时间浪费在依赖上
            if any(part in {".git", "node_modules", "__pycache__", ".venv", "target", "dist"} for part in f.parts):
                continue
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rel = (f.relative_to(self.workspace).as_posix()
                           if self.workspace in f.parents else f)
                    hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                    if len(hits) >= MAX_GREP_HITS:
                        break
            if len(hits) >= MAX_GREP_HITS:
                break

        if not hits:
            return ToolResult(True, f"没有匹配 {pattern!r} 的内容。")
        body = "\n".join(hits)
        if len(hits) >= MAX_GREP_HITS:
            body += f"\n…（已达 {MAX_GREP_HITS} 条上限，可缩小 path 或改用更精确的 pattern）"
        return ToolResult(True, body)

    async def bash(self, command: str) -> ToolResult:
        denial = self.policy_error("bash")
        if denial is not None:
            return ToolResult(False, denial)
        sandbox_denial = self._sandbox_bash_error(command)
        if sandbox_denial is not None:
            return ToolResult(False, sandbox_denial)
        if not self.allow_bash:
            return ToolResult(False, "当前配置禁用了 shell 命令执行。")

        # 明显破坏性的命令直接拒绝。这里不做完备的沙箱——
        # 真要好隔离应该上容器，但至少拦住最常见的误操作。
        lowered = command.lower()
        for danger in ("rm -rf /", "mkfs", "dd if=", ":(){", "shutdown", "reboot"):
            if danger in lowered:
                return ToolResult(False, f"命令包含危险操作 {danger!r}，已拒绝执行。")

        # 独立进程组：超时杀的是整组，而不是只杀 shell 把它启动的
        # 孙子进程（sleep、编译器……）留在系统里。
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(self.workspace),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                start_new_session=True,
            )
        except OSError as exc:
            return ToolResult(False, f"无法启动命令：{exc}")

        # 输出用读任务持续收集，而不是 communicate 一锤子买卖：
        # 超时杀进程时，已经产出的部分输出还能带回来给模型看。
        chunks: list[bytes] = []

        async def _pump() -> None:
            assert proc.stdout
            while True:
                block = await proc.stdout.read(65536)
                if not block:
                    break
                chunks.append(block)

        pump = asyncio.ensure_future(_pump())
        try:
            await asyncio.wait_for(asyncio.shield(pump), timeout=self.bash_timeout)
        except asyncio.TimeoutError:
            # 杀整组进程，别把 shell 的孙子们留在系统里。
            # POSIX 用进程组；Windows 没有进程组（start_new_session 被忽略），
            # 用 taskkill /T 杀整棵进程树。
            killed = False
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                killed = True
            except (ProcessLookupError, PermissionError, AttributeError):
                pass
            if not killed and sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True)
                killed = True
            if not killed:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                await asyncio.wait_for(pump, timeout=10)
            except Exception:
                pump.cancel()
            try:
                await proc.wait()
            except Exception:
                pass
            partial = b"".join(chunks).decode("utf-8", errors="replace")
            note = f"命令超时（{self.bash_timeout} 秒）已被终止：{command}"
            if partial.strip():
                return ToolResult(False, note + "\n终止前的部分输出：\n" + self._clip(partial))
            return ToolResult(False, note)

        await pump
        await proc.wait()
        text = b"".join(chunks).decode("utf-8", errors="replace")
        code = proc.returncode or 0
        if code != 0:
            return ToolResult(False, f"命令退出码 {code}：\n{self._clip(text)}")
        return ToolResult(True, self._clip(text) if text.strip() else "（命令无输出，执行成功）")

    async def task(self, description: str = "", prompt: str = "") -> ToolResult:
        denial = self.policy_error("task")
        if denial is not None:
            return ToolResult(False, denial)
        """把一段独立的子任务派给子智能体，只要它的最终结论。

        这里是「分发」，不是「实现」：真正的跑循环在 Agent 层。
        子智能体有独立的上下文，中间的工具调用过程不会进入主智能体的历史，
        只把最后一条答复交回来——这正是派生子智能体省上下文的原因。
        """
        if not self.allow_subagents or self.spawn_subagent is None:
            return ToolResult(False, "当前配置未启用子智能体，请自己完成这个任务。")
        if not prompt or not prompt.strip():
            return ToolResult(
                False,
                "task 缺少 prompt。需要写一段自包含的任务说明——"
                "子智能体看不到我们这段对话，只能看到你在 prompt 里写的内容。",
            )
        return await self.spawn_subagent(description.strip(), prompt.strip())

    # ---------- 对外接口 ----------

    def specs(self) -> list[dict[str, Any]]:
        """OpenAI function-calling 格式的工具声明。"""
        def obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
            return {"type": "object", "properties": props, "required": required}

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取文件内容，带行号返回。修改文件前应先用它确认原文。",
                    "parameters": obj(
                        {
                            "path": {"type": "string", "description": "相对工作目录的路径"},
                            "offset": {"type": "integer", "description": "起始行号，从 1 开始"},
                            "limit": {"type": "integer", "description": "最多读取多少行"},
                        },
                        ["path"],
                    ),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "写入文件，会覆盖已有内容。新建文件用它。",
                    "parameters": obj(
                        {
                            "path": {"type": "string", "description": "相对工作目录的路径"},
                            "content": {"type": "string", "description": "完整文件内容"},
                        },
                        ["path", "content"],
                    ),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": "在文件里做字面量替换。要替换的内容必须唯一，否则会失败。",
                    "parameters": obj(
                        {
                            "path": {"type": "string"},
                            "old": {"type": "string", "description": "要被替换的原文"},
                            "new": {"type": "string", "description": "替换成的内容"},
                            "replace_all": {"type": "boolean", "description": "是否替换全部匹配"},
                        },
                        ["path", "old", "new"],
                    ),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "description": "列出目录内容，用于了解项目结构。",
                    "parameters": obj({"path": {"type": "string"}}, []),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "grep",
                    "description": "用正则搜索文件内容，返回匹配行与行号。",
                    "parameters": obj(
                        {
                            "pattern": {"type": "string", "description": "正则表达式"},
                            "path": {"type": "string", "description": "搜索范围，默认整个工作目录"},
                            "include": {"type": "string", "description": "文件名 glob，如 *.py"},
                        },
                        ["pattern"],
                    ),
                },
            },
        {
            "type": "function",
            "function": {
                "name": "web_get",
                "description": (
                    """抓取一个网页并提取正文（自动剥掉脚本和样式）。适合静态页面、
                    文档和 JSON 接口。只支持 http(s) 地址。需要执行页面 JS 才有
                    内容的页面改用 browser_read。"""
                ),
                "parameters": obj({"url": {"type": "string", "description": "完整 http(s) 网址"}}, ["url"]),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_read",
                "description": (
                    """用无头 Chrome 渲染网页后提取正文。比 web_get 慢几秒，但能执行
                    页面脚本，适合单页应用和「渲染后才有内容」的页面。只读不交互：
                    它不能点击、输入或登录。"""
                ),
                "parameters": obj({"url": {"type": "string", "description": "完整 http(s) 网址"}}, ["url"]),
            },
        },        ]
        if self.allow_subagents:
            tools.append({
                "type": "function",
                "function": {
                    "name": "task",
                    "description": (
                        "把一段独立的子任务交给子智能体执行，只要它的最终结论。"
                        "子智能体有自己的上下文，看不到当前对话，只能看到你写的 prompt，"
                        "所以 prompt 必须自包含（目标、已知条件、要交付什么）。"
                        "它的中间过程不进入你的上下文，适合「要翻很多文件才能回答」的调查类任务。"
                    ),
                    "parameters": obj(
                        {
                            "description": {
                                "type": "string",
                                "description": "三到五个字的简短说明，用于界面显示，例如「查依赖版本」",
                            },
                            "prompt": {
                                "type": "string",
                                "description": "写给子智能体的完整任务说明。它看不到我们的对话，必须自包含。",
                            },
                        },
                        ["description", "prompt"],
                    ),
                },
            })
        tools.append({
            "type": "function",
            "function": {
                "name": "save_skill",
                "description": (
                    "把这次解决问题的方法记成技能，以后同类任务会自动带上。"
                    "当你摸索出一个值得重复使用的做法（踩过的坑、约定的流程、"
                    "这个项目的特殊规矩）时用它。日常琐事不要存。"
                ),
                "parameters": obj(
                    {
                        "name": {"type": "string", "description": "技能名，简短好认，例如「发版流程」"},
                        "description": {"type": "string",
                                        "description": "一句话说明它解决什么问题"},
                        "content": {"type": "string",
                                    "description": "具体做法，写成照着做就能复现的样子"},
                    },
                    ["name", "content"],
                ),
            },
        })
        if self.allow_bash:
            tools.append({
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "在工作目录执行 shell 命令。用于运行测试、构建、查看状态。",
                    "parameters": obj({"command": {"type": "string"}}, ["command"]),
                },
            })
        return tools

    async def web_get(self, url: str = "", **extra: Any) -> ToolResult:
        """直接抓取网页正文。实现在 browser 模块，这里只做转发。"""
        from .browser import web_get as _web_get

        ok, output = await _web_get(url)
        return ToolResult(ok, output)

    async def browser_read(self, url: str = "", **extra: Any) -> ToolResult:
        """无头 Chrome 渲染后取正文。实现在 browser 模块，这里只做转发。"""
        from .browser import browser_read as _browser_read

        ok, output = await _browser_read(url)
        return ToolResult(ok, output)

    async def save_skill(self, name: str = "", content: str = "", **extra: Any) -> ToolResult:
        denial = self.policy_error("save_skill")
        if denial is not None:
            return ToolResult(False, denial)
        """把一段做法存进技能库。名字相同就更新，不会攒出一堆重复的。"""
        from .library import upsert_skill

        title = (name or "").strip()
        body = (content or "").strip()
        if not title or not body:
            return ToolResult(False, "save_skill 需要 name 和 content 两个参数。")
        skill = upsert_skill(title, str(extra.get("description") or "").strip(), body)
        return ToolResult(True, f"已记住技能「{skill.name}」，之后的同类任务会自动带上。")

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """按名字分发。参数缺失时返回提示而不是抛异常。"""
        handler: Callable[..., Any] | None = getattr(self, name, None)
        available = {t["function"]["name"] for t in self.specs()}
        if handler is None or name not in available:
            return ToolResult(False, f"没有名为 {name!r} 的工具。可用：{'、'.join(sorted(available))}")
        denial = self.policy_error(name)
        if denial is not None:
            return ToolResult(False, denial)
        try:
            return await handler(**arguments)
        except TypeError as exc:
            return ToolResult(False, f"调用 {name} 的参数不对：{exc}")
        except ToolError as exc:
            return ToolResult(False, str(exc))

    # 说明：安全边界（_resolve 的越界检查）必须对「所有调用路径」生效，
    # 所以每个公开工具方法自己兜住 ToolError，而不是指望调用方去捕获。
    # 早前只有 call() 捕获，直接调 read_file 时异常会穿透——
    # 也就是说边界是否生效取决于从哪进来，这不可接受。
