"""本地 Web 服务。

为什么用标准库而不上 FastAPI：这个服务的职责只有一个——
把界面上的任务交给智能体并把过程推回去。引入框架会带来依赖与版本问题，
而标准库的 http.server 足够。启动零依赖，是刻意的取舍。

流式返回用 NDJSON（每行一个 JSON），而不是 WebSocket：
单向推送不需要双向通道，NDJSON 在浏览器里用 fetch + ReadableStream
就能消费，不需要额外库。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .agent import Agent
from .config import load_config
from .providers import ProviderError

WEB_DIR = Path(__file__).parent / "web"

# 界面里填的连接设置存这里。放服务端而不是浏览器存储，
# 是因为这里面有密钥：浏览器的存储是明文、而且会被同源页面读走，
# 文件能收紧到 600。
SETTINGS_PATH = Path.home() / ".jancode-agent" / "desktop-settings.json"


def _saved_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def apply_saved_settings(config: AgentConfig) -> AgentConfig:
    """把界面里保存过的设置补进配置。

    双击启动的 app 没有终端，也就没有环境变量。不读这份文件的话，
    用户每次打开都只看到「未提供 API 密钥」，而且界面上没有任何地方能填。

    环境变量优先：命令行用户明确设了，就该按他说的来，不能被界面里的旧值盖掉。
    """
    from dataclasses import replace

    saved = _saved_settings()
    if not saved:
        return config
    provider = config.provider
    if not os.environ.get("JANCODE_API_KEY") and saved.get("api_key"):
        provider = replace(provider, api_key=str(saved["api_key"]))
    if not os.environ.get("JANCODE_BASE_URL") and saved.get("base_url"):
        provider = replace(provider, base_url=str(saved["base_url"]).rstrip("/"))
    if not os.environ.get("JANCODE_MODEL") and saved.get("model"):
        provider = replace(provider, model=str(saved["model"]))
    return replace(config, provider=provider)


def _index_html() -> bytes:
    return (WEB_DIR / "index.html").read_bytes()


class Handler(BaseHTTPRequestHandler):
    config = None  # 由 serve() 注入
    server_version = "JanCodeAgent"

    def log_message(self, fmt: str, *args: object) -> None:
        # 默认会把每个请求打到 stderr，界面上会刷屏。静音。
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 本地工具，禁止被外部页面嵌入
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, _index_html(), "text/html; charset=utf-8")
            return
        if self.path == "/api/info":
            p = self.config.provider
            payload = {
                "model": p.model,
                "base_url": p.base_url,
                "label": p.label or p.name,
                "workspace": str(self.config.workspace),
                "has_key": bool(p.api_key),
                "allow_bash": self.config.allow_bash,
                "allow_subagents": self.config.allow_subagents,
                "max_steps": self.config.max_steps,
                "version": __version__,
            }
            self._send(200, json.dumps(payload, ensure_ascii=False).encode(), "application/json")
            return
        if self.path == "/api/models":
            self._models()
            return
        if self.path == "/api/settings":
            self._get_settings()
            return
        if self.path.startswith("/api/files"):
            self._files()
            return
        if self.path.startswith("/api/file?"):
            self._file()
            return
        if self.path == "/api/tools":
            self._tools()
            return
        if self.path == "/api/skills":
            from dataclasses import asdict

            from .library import list_skills

            self._json({"ok": True, "skills": [asdict(s) for s in list_skills()]})
            return
        if self.path == "/api/agents":
            from dataclasses import asdict

            from .library import list_agents

            self._json({"ok": True, "agents": [asdict(a) for a in list_agents()]})
            return
        if self.path == "/api/automations":
            from dataclasses import asdict

            from .library import list_automations
            from .scheduler import next_run

            items = []
            now = time.time()
            for item in list_automations():
                row = asdict(item)
                moment = next_run(item.schedule, item.last_run, now)
                row["next_run"] = moment
                row["schedule_ok"] = moment is not None
                items.append(row)
            self._json({"ok": True, "automations": items})
            return
        self._send(404, b"not found", "text/plain")

    def _body(self) -> dict:
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _edit_skill(self) -> None:
        from .library import delete_skill, upsert_skill

        payload = self._body()
        target = str(payload.get("delete") or "").strip()
        if target:
            ok = delete_skill(target)
            self._json({"ok": ok, "error": "" if ok else "没有这条技能"})
            return
        name = str(payload.get("name") or "").strip()
        content = str(payload.get("content") or "").strip()
        if not name or not content:
            self._json({"ok": False, "error": "技能需要名字和内容"})
            return
        upsert_skill(name, str(payload.get("description") or ""), content)
        self._json({"ok": True})

    def _edit_agent(self) -> None:
        from .library import delete_agent, upsert_agent

        payload = self._body()
        target = str(payload.get("delete") or "").strip()
        if target:
            ok = delete_agent(target)
            self._json({"ok": ok, "error": "" if ok else "没有这个智能体"})
            return
        name = str(payload.get("name") or "").strip()
        if not name:
            self._json({"ok": False, "error": "智能体需要名字"})
            return
        picked = payload.get("skills")
        upsert_agent(name, str(payload.get("system_prompt") or ""),
                     str(payload.get("model") or ""),
                     [str(s) for s in picked] if isinstance(picked, list) else [])
        self._json({"ok": True})

    def _edit_automation(self) -> None:
        from .library import delete_automation, upsert_automation
        from .scheduler import next_run

        payload = self._body()
        target = str(payload.get("delete") or "").strip()
        if target:
            ok = delete_automation(target)
            self._json({"ok": ok, "error": "" if ok else "没有这条自动化任务"})
            return
        name = str(payload.get("name") or "").strip()
        schedule = str(payload.get("schedule") or "").strip()
        if not name:
            self._json({"ok": False, "error": "需要一个名字"})
            return
        # 时间写法先校验再存：存进去一个看不懂的写法，界面上看着是配好了，
        # 实际永远不会触发——这种「配了但没生效」最难排查。
        if schedule and next_run(schedule, 0.0, 0.0) is None:
            self._json({"ok": False, "error": "时间写法不认识。支持：每 30 分钟 / 每小时 / 每天 09:00"})
            return
        upsert_automation(
            name,
            str(payload.get("prompt") or ""),
            schedule,
            bool(payload.get("enabled")),
            str(payload.get("agent") or ""),
            str(payload.get("model") or ""),
        )
        self._json({"ok": True})

    def _query(self) -> dict:
        from urllib.parse import parse_qs, urlparse

        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _toolbox(self):
        """按当前配置造一个工具箱。

        文件浏览走它，是为了和工作目录边界共用同一套规则——
        界面另写一套解析路径的逻辑，迟早会和工具的边界不一致，
        那时候从界面上就能读到工作目录外的文件。
        """
        from .tools import Toolbox

        return Toolbox(workspace=self.config.workspace)

    def _files(self) -> None:
        rel = self._query().get("path") or "."
        entries, error = self._toolbox().entries(rel)
        if error:
            self._json({"ok": False, "error": error})
            return
        self._json({"ok": True, "path": rel, "entries": entries})

    def _file(self) -> None:
        rel = self._query().get("path") or ""
        if not rel:
            self._json({"ok": False, "error": "缺少 path"})
            return
        # 只给预览：整本大文件塞进界面没有任何意义，还会把窗口卡住。
        result = asyncio.run(self._toolbox().read_file(rel, limit=400))
        if not result.ok:
            self._json({"ok": False, "error": result.text})
            return
        self._json({"ok": True, "path": rel, "text": result.text})

    def _tools(self) -> None:
        specs = self._toolbox().specs()
        items = []
        for spec in specs:
            fn = spec.get("function") or {}
            items.append({"name": fn.get("name", ""), "description": fn.get("description", "")})
        self._json({"ok": True, "tools": items,
                    "allow_bash": self.config.allow_bash,
                    "allow_subagents": self.config.allow_subagents,
                    "max_steps": self.config.max_steps})

    def _get_settings(self) -> None:
        """界面要的连接设置。密钥本身不回传，只回「有没有配」。"""
        p = self.config.provider
        self._json({
            "ok": True,
            "base_url": p.base_url,
            "model": p.model,
            "label": p.label or p.name,
            "has_key": bool(p.api_key),
            "key_from_env": bool(os.environ.get("JANCODE_API_KEY")),
        })

    def _save_settings(self) -> None:
        """保存界面里填的连接设置，并让它在当前进程立即生效。"""
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except (ValueError, UnicodeDecodeError):
            self._json({"ok": False, "error": "请求体不是 JSON"}, 400)
            return
        if not isinstance(payload, dict):
            self._json({"ok": False, "error": "请求体不是对象"}, 400)
            return

        saved = _saved_settings()
        for key in ("api_key", "base_url", "model"):
            value = str(payload.get(key) or "").strip()
            if value:
                saved[key] = value
        if not saved:
            self._json({"ok": False, "error": "什么都没填"})
            return

        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            os.chmod(SETTINGS_PATH, 0o600)  # 里面有密钥
        except OSError as exc:
            self._json({"ok": False, "error": f"保存失败：{exc}"})
            return

        # 立刻生效：不然用户填完还得重启一次 app，很容易以为没保存上。
        Handler.config = apply_saved_settings(Handler.config)
        self._json({"ok": True, "has_key": bool(Handler.config.provider.api_key),
                    "model": Handler.config.provider.model,
                    "base_url": Handler.config.provider.base_url})

    def _models(self) -> None:
        """把可用模型列给界面。

        直接问中转站要 /v1/models，而不是写死一份清单：中转站的模型随时在变，
        写死的清单第二天就是错的，用户照着选会选中一个用不了的模型。

        拉不到时至少把当前模型和内置预设给出来——这个菜单不能是死的，
        网络不通的时候用户更需要知道现在用的是哪个模型。
        """
        from .config import BUILTIN_PROVIDERS

        p = self.config.provider
        ids: list[str] = []
        error = ""
        try:
            import httpx

            resp = httpx.get(
                f"{p.base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {p.api_key}"},
                timeout=15.0,
            )
            if resp.status_code < 400:
                for item in (resp.json() or {}).get("data") or []:
                    name = item.get("id")
                    if name:
                        ids.append(str(name))
            else:
                error = f"中转站返回 {resp.status_code}"
        except Exception as exc:
            error = f"连不上中转站：{exc}"

        if p.model and p.model not in ids:
            ids.insert(0, p.model)
        if not ids:
            spec = BUILTIN_PROVIDERS.get(p.name) or {}
            ids = [str(spec.get("model") or p.model)]

        self._json({"ok": True, "models": ids, "current": p.model, "error": error})

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json")

    def _notify(self) -> None:
        """把一段文本推到企业微信群机器人。

        只收 https：http 会把任务结论明文发到网上。这种「图省事」的写法一旦被
        抄进教程就到处流传，所以这里直接拒绝，而不是加一句注释提醒。
        """
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except (ValueError, UnicodeDecodeError):
            self._json({"ok": False, "error": "请求体不是 JSON"}, 400)
            return

        url = str(payload.get("webhook") or "").strip()
        text = str(payload.get("text") or "").strip()[:4000]
        if not url.startswith("https://"):
            self._json({"ok": False, "error": "webhook 必须是 https 地址"})
            return
        if not text:
            self._json({"ok": False, "error": "推送内容为空"})
            return

        try:
            import httpx

            resp = httpx.post(
                url,
                json={"msgtype": "markdown", "markdown": {"content": text}},
                timeout=10.0,
            )
        except Exception as exc:
            self._json({"ok": False, "error": f"推送失败：{exc}"})
            return

        if resp.status_code >= 400:
            self._json({"ok": False, "error": f"对方返回 {resp.status_code}"})
            return
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if data.get("errcode"):
            self._json({"ok": False, "error": f"企业微信返回 {data.get('errcode')}：{data.get('errmsg', '')}"})
            return
        self._json({"ok": True})

    def do_POST(self) -> None:
        if self.path == "/api/notify":
            self._notify()
            return
        if self.path == "/api/settings":
            self._save_settings()
            return
        if self.path == "/api/skills":
            self._edit_skill()
            return
        if self.path == "/api/agents":
            self._edit_agent()
            return
        if self.path == "/api/automations":
            self._edit_automation()
            return
        if self.path != "/api/run":
            self._send(404, b"not found", "text/plain")
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            self._send(400, b'{"error":"\\u8bf7\\u6c42\\u4f53\\u4e0d\\u662f JSON"}', "application/json")
            return

        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            self._send(400, b'{"error":"\\u4efb\\u52a1\\u4e0d\\u80fd\\u4e3a\\u7a7a"}', "application/json")
            return

        history = payload.get("history") or []

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def emit(obj: dict) -> None:
            self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()

        # 本次任务用哪个角色、哪个模型、带哪些技能。
        # 一律走 dataclasses.replace，不就地改 self.config —— 那是所有请求共用的，
        # 改了会串到别的任务上。
        from dataclasses import replace

        from .library import find_agent, persona_prompt, skills_section

        model = str(payload.get("model") or "").strip()
        config = self.config

        wanted = str(payload.get("agent") or "").strip()
        persona = find_agent(wanted) if wanted else None
        if persona is not None:
            if not model and persona.model:
                model = persona.model
            config = replace(config, system_extra=persona_prompt(persona))
        else:
            # 没选角色时把技能全带上：技能是用户为了「以后都能用」写的，
            # 不该因为这次忘了选角色就不生效。
            config = replace(config, system_extra=skills_section())

        if model and model != config.provider.model:
            config = replace(config, provider=replace(config.provider, model=model))

        async def drive() -> None:
            async with Agent(config) as agent:
                # 把界面上的历史对话恢复进上下文，保证多轮连续
                from .providers import Message
                for turn in history[-20:]:
                    role = turn.get("role")
                    content = str(turn.get("content") or "")
                    if role in ("user", "assistant") and content:
                        agent.messages.append(Message(role=role, content=content))

                async for step in agent.run(prompt):
                    emit({"kind": step.kind, "text": step.text,
                          "tool": step.tool_name, "ok": step.tool_ok,
                          "subagent": step.subagent})

        try:
            asyncio.run(drive())
        except ProviderError as exc:
            emit({"kind": "error", "text": str(exc)})
        except Exception as exc:  # 兜底：任何异常都要让界面看到，而不是静默断流
            emit({"kind": "error", "text": f"内部错误：{exc}"})
        emit({"kind": "done"})


class MissingApiKey(RuntimeError):
    """没配密钥。

    单独一个异常类型，是为了让调用方分辨「配置没弄好」和「端口被占了」
    这两类完全不同的失败——它们的处理方式不一样。
    """


def build_server(host: str = "127.0.0.1", port: int = 8765, workspace: Path | None = None,
                 provider: str | None = None,
                 config: AgentConfig | None = None,
                 require_key: bool = True) -> ThreadingHTTPServer:
    """装配好配置、建好服务，但**不启动**。

    把「建」和「跑」分开是为了桌面版：它要在后台线程里跑 serve_forever，
    并在窗口关掉时自己 shutdown。两者揉在一起的话，桌面版只能另抄一份出来，
    以后改一处忘一处。
    """
    # 刻意不在这里合并界面保存的设置：build_server 也会被命令行和网页版用到，
    # 那边用户可能是用 --api-key/--base-url 明确指定的，被文件里的旧值盖掉
    # 就是「我传了参数却不生效」。合并只发生在桌面版的入口（desktop.main）。
    Handler.config = config or load_config(
        provider_name=provider, workspace=workspace or Path.cwd())
    if require_key and not Handler.config.provider.api_key:
        raise MissingApiKey(
            "未提供 API 密钥。请设置环境变量 JANCODE_API_KEY 后重试。")
    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str = "127.0.0.1", port: int = 8765, workspace: Path | None = None,
          provider: str | None = None, open_browser: bool = True,
          config: AgentConfig | None = None) -> int:
    """启动本地服务。只监听回环地址——这是本机工具，不该暴露到局域网。

    config 是命令行已经装配好的配置（含 --api-key/--base-url/--model 等覆盖），
    有就直接用；没有才按 provider 名字现查一遍。
    两种都留着是为了不破坏直接调 serve() 的用法。

    为什么必须能传：以前这里只会拿到供应商**名字**，于是界面重新去读环境变量和
    配置文件——用参数给密钥的人看到的是「未提供 API 密钥」，而 --no-bash、
    --no-subagents 这些开关更是被静默丢掉，用户以为禁掉了其实没有。
    """
    try:
        httpd = build_server(host, port, workspace, provider, config)
    except MissingApiKey as exc:
        print(str(exc))
        return 2

    url = f"http://{host}:{port}/"
    # 每行都 flush：输出重定向到文件时 stdout 是块缓冲，不 flush 的话
    # 用户（和读日志的脚本）要等缓冲区满才看得到"已启动"。
    print(f"JanCode Agent 界面已启动：{url}", flush=True)
    print(f"工作目录 {Handler.config.workspace}", flush=True)
    print(f"模型 {Handler.config.provider.model} @ {Handler.config.provider.base_url}", flush=True)
    print("按 Ctrl+C 停止。\n", flush=True)

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0
