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
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


# 正在跑的任务：run_id -> {inbox, stop}。
# 插话和停止是从另外的 HTTP 线程进来的，所以必须加锁。
LIVE: dict = {}
LIVE_LOCK = threading.Lock()
from . import __version__
from .agent import Agent
from .config import load_config
from .providers import ProviderError

WEB_DIR = Path(__file__).parent / "web"

# 界面里填的连接设置存这里。放服务端而不是浏览器存储，
# 是因为这里面有密钥：浏览器的存储是明文、而且会被同源页面读走，
# 文件能收紧到 600。
SETTINGS_PATH = Path.home() / ".jancode-agent" / "desktop-settings.json"


def _settings_path() -> Path:
    return getattr(Handler, "settings_path", None) or SETTINGS_PATH


def _saved_settings() -> dict:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


PROVIDERS_KEY = "providers"
ACTIVE_KEY = "active_provider"


def load_providers() -> tuple[list[dict], str]:
    """读出所有接入配置和当前选中的那套。

    每个客户用的中转站和模型都不一样，只存一套等于每次换客户都要改文件。
    兼容早期只有 base_url/api_key/model 平铺字段的存档：把它当成唯一一套，
    老用户升级后配置不丢。
    """
    saved = _saved_settings()
    raw = saved.get(PROVIDERS_KEY)
    if not isinstance(raw, list) or not raw:
        legacy = {}
        for field in ("base_url", "api_key", "model", "wire_api"):
            value = saved.get(field)
            if value:
                legacy[field] = value
        if legacy:
            legacy["name"] = "默认"
            return [legacy], "默认"
        return [], ""
    items = []
    for row in raw:
        if not isinstance(row, dict) or not str(row.get("name") or "").strip():
            continue
        items.append({
            "name": str(row.get("name")).strip(),
            "base_url": str(row.get("base_url") or ""),
            "api_key": str(row.get("api_key") or ""),
            "model": str(row.get("model") or ""),
            "wire_api": str(row.get("wire_api") or "chat"),
        })
    active = str(saved.get(ACTIVE_KEY) or "").strip()
    if active not in {i["name"] for i in items}:
        active = items[0]["name"] if items else ""
    return items, active


def save_providers(items: list[dict], active: str) -> None:
    payload = dict(_saved_settings())
    payload[PROVIDERS_KEY] = items
    payload[ACTIVE_KEY] = active
    for row in items:
        if row["name"] == active:
            payload["base_url"] = row["base_url"]
            payload["api_key"] = row["api_key"]
            payload["model"] = row["model"]
            payload["wire_api"] = row.get("wire_api", "chat")
    sp = _settings_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    try:
        os.chmod(sp, 0o600)
    except OSError:
        pass


def _env_wins(provider):
    """JANCODE_* 环境变量设置时，让它赢过界面里存的配置。

    apply_saved_settings 和 _apply_active 共用同一条优先原则：
    脚本和 CI 里临时指定的接入方式，不能被界面存档盖掉。
    直接读当前环境而不是「保留 config 现值」——后者只在进程启动时
    环境变量已生效的前提下成立，运行中设置的值会漏。
    """
    for names, attr in (
        (("JANCODE_API_KEY",), "api_key"),
        (("JANCODE_BASE_URL",), "base_url"),
        (("JANCODE_MODEL",), "model"),
    ):
        for n in names:
            value = os.environ.get(n, "").strip()
            if value:
                provider = replace(provider, **{attr: value})
                break
    return provider

def provider_config(config: AgentConfig, row: dict) -> AgentConfig:
    """把一套接入配置套到 config 上。"""
    provider = config.provider
    if row.get("base_url"):
        provider = replace(provider, base_url=row["base_url"])
    if row.get("api_key"):
        provider = replace(provider, api_key=row["api_key"])
    if row.get("model"):
        provider = replace(provider, model=row["model"])
    if row.get("wire_api"):
        provider = replace(provider, wire_api=row["wire_api"])
    return replace(config, provider=provider)


def apply_saved_settings(config: AgentConfig) -> AgentConfig:
    """把界面里保存的接入配置套到 config 上。

    环境变量优先：脚本和 CI 里临时指定接入方式很常见，
    被一个界面存档盖掉会让人完全摸不着头脑。
    """
    items, active = load_providers()
    row = next((i for i in items if i["name"] == active), None)
    if row is None:
        return config
    patched = provider_config(config, row)
    provider = patched.provider
    # 只有 JANCODE_* 能盖掉界面里保存的配置。
    # OPENAI_* 是通用变量，别的工具也会设（用户 shell 里就导出了一个
    # 别人的中转站地址），让它盖掉用户自己在界面里配好的地址，会变成
    # 「明明配好了却连不上」这种最难查的问题。
    return replace(patched, provider=_env_wins(provider))


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
        if self.path.startswith("/api/providers"):
            items, active = load_providers()
            self._json({"ok": True, "active": active, "providers": [
                {**{k: v for k, v in row.items() if k != "api_key"},
                 "has_key": bool(row.get("api_key")),
                 "is_active": row["name"] == active} for row in items]})
            return
        if self.path.startswith("/api/mcp"):
            from .mcp import manager

            self._json({"ok": True, "servers": manager.status()})
            return
        if self.path == "/api/vendors":
            from .catalog import vendors

            self._json({"ok": True, "vendors": vendors()})
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

            from .library import builtin_skills, list_skills

            self._json({"ok": True, "skills": [asdict(s) for s in list_skills()],
                        "builtins": builtin_skills()})
            return
        if self.path == "/api/agents":
            from dataclasses import asdict

            from .library import builtin_agents, list_agents
            from .presets import BUILTIN_PRESETS

            self._json({"ok": True, "agents": [asdict(a) for a in list_agents()],
                        "builtins": builtin_agents(),
                        "presets": BUILTIN_PRESETS})
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

    def _install_builtin(self, name: str, remove: bool) -> None:
        from .library import delete_skill, install_builtin

        if remove:
            ok = delete_skill(name)
            self._json({"ok": ok, "error": "" if ok else "这条技能没有装过"})
            return
        ok = install_builtin(name)
        self._json({"ok": ok, "error": "" if ok else "没有这条内置技能"})

    def _edit_skill(self) -> None:
        from .library import delete_skill, upsert_skill

        payload = self._body()
        if payload.get("install"):
            self._install_builtin(str(payload["install"]), False)
            return
        if payload.get("uninstall"):
            self._install_builtin(str(payload["uninstall"]), True)
            return
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

    def _install_builtin_agent(self, name: str, remove: bool) -> None:
        from .library import delete_agent, install_builtin_agent

        if remove:
            ok = delete_agent(name)
            self._json({"ok": ok, "error": "" if ok else "没有添加过这个专家"})
            return
        ok = install_builtin_agent(name)
        self._json({"ok": ok, "error": "" if ok else "没有这个内置专家"})

    def _edit_agent(self) -> None:
        from .library import delete_agent, upsert_agent

        payload = self._body()
        # 模式预设：use=切换当前会话的执行模式；install=复制一份到我的智能体。
        # 预设本体随代码分发改不到，要改就复制，这是它和「数字专家」最大的不同。
        if payload.get("use_preset"):
            from .presets import find_preset

            preset = find_preset(str(payload["use_preset"]))
            if preset is None:
                self._json({"ok": False, "error": "没有这个预设"})
                return
            self._install_preset_mode(preset)
            return
        if payload.get("install_preset"):
            from .presets import find_preset

            preset = find_preset(str(payload["install_preset"]))
            if preset is None:
                self._json({"ok": False, "error": "没有这个预设"})
                return
            upsert_agent(preset["name"], preset["system_prompt"], "", [])
            self._json({"ok": True, "copied": preset["name"]})
            return
        if payload.get("install"):
            self._install_builtin_agent(str(payload["install"]), False)
            return
        if payload.get("uninstall"):
            self._install_builtin_agent(str(payload["uninstall"]), True)
            return
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

    def _install_preset_mode(self, preset: dict) -> None:
        # 把选中的模式预设落成持久设置并立即生效：模式存进
        # desktop-settings.json 的 active_mode，/api/run 每次读它作兜底。
        saved = _saved_settings()
        saved["active_mode"] = preset["mode"]
        prompt = preset.get("system_prompt")
        if prompt:
            saved["preset_prompt"] = prompt
        else:
            saved.pop("preset_prompt", None)
        try:
            sp = _settings_path()
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            os.chmod(sp, 0o600)
        except OSError as exc:
            self._json({"ok": False, "error": f"保存失败：{exc}"})
            return
        self._json({"ok": True, "mode": preset["mode"], "name": preset["name"]})


    def _apply_settings_to_active(self, saved: dict) -> None:
        """把本次提交的连接设置写进 active 那套接入配置。

        _sync_active_provider 是从当前 config 抄回存档，用来记住正在用的；
        这里是用户刚填的新值，必须以提交为准，否则第二次保存会被
        providers 数组里的旧值盖掉（apply_saved_settings 优先读数组）。
        """
        items, active = load_providers()
        row = next((i for i in items if i["name"] == active), None)
        if row is None:
            items = [{"name": "默认", "base_url": str(saved.get("base_url") or "")
                      + "", "api_key": str(saved.get("api_key") or "")
                      + "", "model": str(saved.get("model") or "")}]
            active = "默认"
        else:
            for key in ("api_key", "base_url", "model"):
                value = str(saved.get(key) or "").strip()
                if value:
                    row[key] = value
        save_providers(items, active)

    def _sync_active_provider(self) -> None:
        """把界面里那次保存同步进当前选中的那套接入配置。

        多套配置存在时平铺字段不再被读取，不同步的话用户会以为改完生效了。
        """
        items, active = load_providers()
        row = next((i for i in items if i["name"] == active), None)
        if row is None:
            return
        cfg = Handler.config.provider
        row["base_url"] = cfg.base_url or row["base_url"]
        row["model"] = cfg.model or row["model"]
        if cfg.api_key:
            row["api_key"] = cfg.api_key
        save_providers(items, active)

    def _edit_mcp(self) -> None:
        """增删 MCP 服务、重连、探活。"""
        from .mcp import MCPClient, load_servers, manager, save_servers

        payload = self._body()
        action = str(payload.get("action") or "add").strip()
        name = str(payload.get("name") or "").strip()

        if action == "reload":
            manager.reload()
            self._json({"ok": True, "servers": manager.status()})
            return

        if action == "remove":
            rows = [s for s in load_servers() if s["name"] != name]
            save_servers(rows)
            manager.reload()
            self._json({"ok": True})
            return

        if action == "probe":
            command = str(payload.get("command") or "").strip()
            if not command:
                self._json({"ok": False, "error": "没有启动命令"})
                return
            client = MCPClient(name or "probe", command,
                               [str(a) for a in (payload.get("args") or [])],
                               payload.get("env") or None)
            try:
                client.start()
                self._json({"ok": True, "tools": [x.get("name") for x in client.tools],
                            "server": client.server_info.get("name", "")})
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)})
            finally:
                client.close()
            return

        command = str(payload.get("command") or "").strip()
        if not name or not command:
            self._json({"ok": False, "error": "服务和命令都要填"})
            return
        rows = load_servers()
        row = next((s for s in rows if s["name"] == name), None)
        if row is None:
            row = {"name": name, "command": command, "args": [], "env": {}, "enabled": True}
            rows.append(row)
        row["command"] = command
        row["args"] = [str(a) for a in (payload.get("args") or [])]
        row["enabled"] = bool(payload.get("enabled", True))
        if payload.get("env"):
            row["env"] = {str(k): str(v) for k, v in payload["env"].items()}
        save_servers(rows)
        manager.reload()
        self._json({"ok": True, "servers": manager.status()})

    def _edit_provider(self) -> None:
        """增删改和切换接入配置。"""
        payload = self._body()
        action = str(payload.get("action") or "save").strip()
        name = str(payload.get("name") or "").strip()
        items, active = load_providers()

        if action == "delete":
            items = [i for i in items if i["name"] != name]
            if active == name:
                active = items[0]["name"] if items else ""
            save_providers(items, active)
            self._apply_active(items, active)
            self._json({"ok": True})
            return

        if action == "activate":
            if name not in {i["name"] for i in items}:
                self._json({"ok": False, "error": "没有这套接入配置"})
                return
            save_providers(items, name)
            self._apply_active(items, name)
            self._json({"ok": True, "active": name})
            return

        if not name:
            self._json({"ok": False, "error": "给这套配置起个名字"})
            return
        row = next((i for i in items if i["name"] == name), None)
        if row is None:
            row = {"name": name, "base_url": "", "api_key": "", "model": "",
                   "wire_api": "chat"}
            items.append(row)
        for field in ("base_url", "model", "wire_api"):
            if payload.get(field) is not None:
                row[field] = str(payload.get(field) or "")
        # 密钥留空表示「不改」：每次改模型都要重输一遍密钥太折磨人
        if str(payload.get("api_key") or "").strip():
            row["api_key"] = str(payload["api_key"]).strip()
        if payload.get("activate", True):
            active = name
        save_providers(items, active)
        self._apply_active(items, active)
        self._json({"ok": True, "active": active})

    def _apply_active(self, items: list[dict], active: str) -> None:
        row = next((i for i in items if i["name"] == active), None)
        if row is None:
            return
        base = Handler.config
        patched = replace(base, provider=provider_config(base, row).provider)
        # JANCODE_* 环境变量必须仍然赢过界面里存的配置（apply_saved_settings
        # 同款原则）：脚本和 CI 里临时指定的接入方式，不能被一次界面切换盖掉。
        Handler.config = replace(patched, provider=_env_wins(patched.provider))

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
            self._json({"ok": False, "error": result.output})
            return
        self._json({"ok": True, "path": rel, "text": result.output})

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
            sp = _settings_path()
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            os.chmod(sp, 0o600)  # 里面有密钥
        except OSError as exc:
            self._json({"ok": False, "error": f"保存失败：{exc}"})
            return

        # 立刻生效：不然用户填完还得重启一次 app，很容易以为没保存上。
        # 顺序很关键：先把本次提交的新值写进 active 那套接入配置，再套到进程。
        # 反过来的话 apply 读到的还是 providers 数组里的旧值——第二次保存
        # 会被第一次保存的值盖掉，表现为「改了没反应」。
        self._apply_settings_to_active(saved)
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

        # 把用户配过的模型并进来。
        # 只问当前地址要 /models 是不够的：有些网关（自建的 DGX 就是）压根不返回
        # 模型清单，用户切过一次中转站，之前配好的模型就在这个下拉框里消失了，
        # 看着像「配置丢了」。所以：当前地址能拉到的 + 配过的 + 内置目录，全并进来。
        # 只保留当前这套接入配置自己的模型：用哪套 API 就显示哪套的。
        # 当前配置的模型永远排在最前，即使接口不返回清单也能选它。
        if p.model:
            ids.insert(0, p.model)
        seen = set()
        ids = [x for x in ids if not (x in seen or seen.add(x))]

        # 每个模型支持哪些推理档位，一并给界面：不支持的就别显示选择器。
        from .efforts import options_for

        levels = {mid: options_for(mid) for mid in ids}
        if p.model:
            levels[p.model] = options_for(p.model)

        self._json({"efforts": levels, "ok": True, "models": ids, "current": p.model, "error": error})

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

    def _steer_or_stop(self, is_stop: bool) -> None:
        """插话 / 停止：把指令投给正在跑的那一轮。

        插话不是排队——Agent 循环会在下一个事件处中断当前生成、
        带着修正重新作答；这里只负责把话送到。
        """
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, UnicodeDecodeError):
            self._json({"ok": False, "error": "请求体不是 JSON"})
            return
        run_id = str(payload.get("run_id") or "")
        with LIVE_LOCK:
            handle = LIVE.get(run_id)
        if handle is None:
            self._json({"ok": False, "gone": True, "error": "这一轮已经结束了"})
            return
        if is_stop:
            handle["stop"].set()
        else:
            text = str(payload.get("text") or "").strip()
            if not text:
                self._json({"ok": False, "error": "插话内容不能为空"})
                return
            handle["inbox"].put(text)
        self._json({"ok": True})

    def do_POST(self) -> None:
        if self.path == "/api/notify":
            self._notify()
            return
        if self.path == "/api/mcp":
            self._edit_mcp()
            return
        if self.path == "/api/providers":
            self._edit_provider()
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
        if self.path in ("/api/steer", "/api/stop"):
            self._steer_or_stop(self.path == "/api/stop")
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

        # 这一轮的运行句柄：插话和停止靠它找到正在跑的 Agent。
        run_id = str(payload.get("run_id") or "")
        handle = None
        if run_id:
            from queue import Queue
            handle = {"inbox": Queue(), "stop": threading.Event()}
            with LIVE_LOCK:
                LIVE[run_id] = handle

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

        from .library import disclaimer_for, find_agent, persona_prompt, skills_section

        model = str(payload.get("model") or "").strip()
        effort = str(payload.get("effort") or "")[:16]
        mode = str(payload.get("mode") or "")[:16]
        # 没显式指定模式时用预设选的 active_mode 兜底（数字专家页「以此为准」
        # 写进 desktop-settings.json 的），再兜不住就 auto。
        if not mode:
            mode = str(_saved_settings().get("active_mode") or "")[:16]
        config = self.config
        # 预设附带的提示词（极简/创造等模式的人设）拼在技能段前面：
        # 没选角色时 system_extra 会装技能，预设提示词排在最前，不覆盖技能。
        preset_prompt = str(_saved_settings().get("preset_prompt") or "").strip()
        if preset_prompt and mode not in ("", "auto"):
            config = replace(config, system_extra=preset_prompt)

        wanted = str(payload.get("agent") or "").strip()
        persona = find_agent(wanted) if wanted else None
        disclaimer = ""
        if persona is not None:
            disclaimer = disclaimer_for(persona.name)
            if not model and persona.model:
                model = persona.model
            config = replace(config, system_extra=persona_prompt(persona))
        else:
            # 没选角色时把技能全带上：技能是用户为了「以后都能用」写的，
            # 不该因为这次忘了选角色就不生效。
            config = replace(config, system_extra=skills_section())

        if model and model != config.provider.model:
            config = replace(config, provider=replace(config.provider, model=model))
        if effort:
            config = replace(config, provider=replace(config.provider, effort=effort))

        # 工作模式：计划模式先出方案不动手；只读模式禁止改文件。
        # 这两条是行为约束，必须进系统提示，不能只靠用户在正文里说一句
        # （正文里的要求容易被后面的内容带跑）。
        MODES = {
            # 全自动：不再逐步征求意见，但危险操作仍需在方案里说明
            "auto": (
                "工作模式：全自动。连续执行，不要每一步都问我。"
                "但涉及删除文件、改系统配置、发网络请求这类有副作用的操作，"
                "要先在回复里把动作和影响写清楚再执行。"
            ),
            # 限定在工作目录内：不许碰工作目录以外的任何文件
            "sandbox": (
                "工作模式：限定工作目录。所有读写都只能在工作目录内进行，"
                "不要访问工作目录以外的路径（尤其是用户主目录下的配置和密钥文件）。"
                "确实需要访问外部路径时，先把原因和路径写出来让我确认。"
            ),
            "plan": (
                "工作模式：计划模式。先给出完整的分步计划（每步做什么、"
                "改哪些文件、怎么验证），不要执行任何修改。等我确认后再动手。"
            ),
            "readonly": (
                "工作模式：只读。只做阅读、分析和建议，不要创建、修改或删除"
                "任何文件，也不要执行有副作用的命令。需要改动时把方案写出来让我确认。"
            ),
        }
        # 模式先进 config：Toolbox 会拿它做代码级拦截。
        # 不认识的模式一律当 auto，避免「写错了就变放行」这种意外。
        # 写错的模式名不该变成放行：退回最严格的 readonly，
        # 宁可多拦一次让用户去切模式，也不能因为拼错就悄悄放开。
        if mode not in MODES and mode:
            config = replace(config, mode="readonly")
        else:
            config = replace(config, mode=mode or "auto")
        if mode in MODES:
            base_extra = config.system_extra or ""
            config = replace(config, system_extra=(base_extra + "\n\n" + MODES[mode]).strip())

        last_answer = {"text": ""}

        async def drive() -> None:
            async with Agent(config) as agent:
                # 把界面上的历史对话恢复进上下文，保证多轮连续
                from .providers import Message
                for turn in history[-20:]:
                    role = turn.get("role")
                    content = str(turn.get("content") or "")
                    if role in ("user", "assistant") and content:
                        agent.messages.append(Message(role=role, content=content))

                async for step in agent.run(
                prompt,
                inbox=handle["inbox"] if handle else None,
                stop=handle["stop"] if handle else None,
            ):
                    if step.kind == "answer" and not step.subagent:
                        # 流式下回答是分多块来的：增量要累加，
                        # 非增量的整段回答则直接覆盖。
                        # 法律声明是靠 last_answer 判断有没有附加过的，
                        # 拼错这里会导致声明重复或不出现。
                        if getattr(step, "delta", False):
                            last_answer["text"] += step.text
                        elif step.text:
                            last_answer["text"] = step.text
                    emit({"kind": step.kind, "text": step.text,
                          "tool": step.tool_name, "ok": step.tool_ok,
                          "subagent": step.subagent,
                          "delta": getattr(step, "delta", False),
                          "usage": getattr(step, "usage", None) or None})

        try:
            asyncio.run(drive())
            # 法律声明由这里强制附加：模型忘了、改写了、或者被后续指令盖掉了，
            # 都不影响用户在最后一定看得到它。这类声明的责任在程序，不在模型。
            if disclaimer and disclaimer not in last_answer["text"]:
                emit({"kind": "answer", "text": disclaimer, "tool": "", "ok": True,
                      "subagent": ""})
        except ProviderError as exc:
            emit({"kind": "error", "text": str(exc)})
        except Exception as exc:  # 兜底：任何异常都要让界面看到，而不是静默断流
            emit({"kind": "error", "text": f"内部错误：{exc}"})
        finally:
            # 客户端中途断连时 emit 会抛 BrokenPipeError，不进 finally 的清理
            # 会把 run_id 留在 LIVE 里——越积越多，还让 stop 误以为还活着。
            if run_id:
                with LIVE_LOCK:
                    LIVE.pop(run_id, None)
        try:
            emit({"kind": "done"})
        except OSError:
            pass  # 客户端已断连，done 送不到就算了


class MissingApiKey(RuntimeError):
    """没配密钥。

    单独一个异常类型，是为了让调用方分辨「配置没弄好」和「端口被占了」
    这两类完全不同的失败——它们的处理方式不一样。
    """


def build_server(host: str = "127.0.0.1", port: int = 8765, workspace: Path | None = None,
                 provider: str | None = None,
                 config: AgentConfig | None = None,
                 require_key: bool = True,
                 settings_path: Path | None = None) -> ThreadingHTTPServer:
    """装配好配置、建好服务，但**不启动**。

    把「建」和「跑」分开是为了桌面版：它要在后台线程里跑 serve_forever，
    并在窗口关掉时自己 shutdown。两者揉在一起的话，桌面版只能另抄一份出来，
    以后改一处忘一处。
    """
    # 刻意不在这里合并界面保存的设置：build_server 也会被命令行和网页版用到，
    # 那边用户可能是用 --api-key/--base-url 明确指定的，被文件里的旧值盖掉
    # 就是「我传了参数却不生效」。合并只发生在桌面版的入口（desktop.main）。
    # 测试/冒烟把设置文件指到临时目录，POST /api/settings 才不会把假配置
    # 写进用户真实的 desktop-settings.json。
    Handler.settings_path = settings_path
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

    # 定时任务在这里也要盯上：桌面版自己会起调度器，但用户用
    # jancode-agent serve 或 Python 直接调 serve() 时，界面上
    # 建的自动化任务显示「已启用」却永远不会触发。
    from .scheduler import start as start_scheduler
    start_scheduler(Handler.config)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0
