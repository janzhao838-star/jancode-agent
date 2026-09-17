"""MCP 客户端（stdio / JSON-RPC）。

为什么自己写而不是引 SDK：这个项目的原则是零依赖启动，
引入官方 SDK 会带进一堆传递依赖，打包体积和版本冲突都要用户承担。
MCP 的 stdio 传输其实就是「一行一个 JSON-RPC」，标准库足够。

协议流程（照着规范实现）：
    1. 启动子进程
    2. initialize 握手，拿到服务端能力
    3. 发 notifications/initialized 通知（注意：这是通知，没有 id，不等待回复）
    4. tools/list 拿工具清单
    5. tools/call 调用，结果在 content 数组里
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import threading
import time

CONFIG_PATH = pathlib.Path.home() / ".jancode-agent" / "mcp.json"
PROTOCOL_VERSION = "2024-11-05"


def load_servers() -> list[dict]:
    if not CONFIG_PATH.exists():
        return []
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    rows = data.get("servers") if isinstance(data, dict) else data
    out = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("name") and row.get("command"):
            out.append({
                "name": str(row["name"]),
                "command": str(row["command"]),
                "args": [str(a) for a in (row.get("args") or [])],
                "env": dict(row.get("env") or {}),
                "enabled": bool(row.get("enabled", True)),
            })
    return out


def save_servers(items: list[dict]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps({"servers": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)  # 里面可能有服务端密钥
    except OSError:
        pass


def slug(text: str) -> str:
    """把名字洗成工具名允许的字符。

    工具名只允许 [a-zA-Z0-9_-]。服务名是人取的（「Memory 记忆」），
    直接拼进工具名会带空格或中文，整个请求会被 provider 拒掉——
    而且报错只说 tools[22].name 不合法，很难看出是名字问题。
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(text or ""))



def spawn_command(command: str, args: list | None = None,
                  plat: str | None = None, which=None) -> list:
    """把要执行的命令拼成 Popen 能直接用的参数列表。

    Windows 上 npx / npm 是 .cmd 批处理文件，CreateProcess 不能直接执行，
    会报「不是有效的 Win32 应用程序」，所以要用 cmd /c 包一层。

    单独抽成函数是为了能测：真机验证 Windows 需要一台 Windows 机器，
    但「拼出来的命令对不对」用假平台参数就能查出来。
    """
    plat = plat if plat is not None else os.name
    which = which or shutil.which
    rest = list(args or [])
    if plat != "nt":
        return [command, *rest]
    found = (which(command) or "").lower()
    if found.endswith((".cmd", ".bat")) or command in ("npx", "npm", "pnpm"):
        return ["cmd", "/c", command, *rest]
    return [command, *rest]

class MCPError(RuntimeError):
    pass


class MCPClient:
    """一个 MCP 服务进程的客户端。用完必须 close()，否则子进程会留着。"""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict | None = None, timeout: float = 40.0) -> None:
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self.server_info: dict = {}
        self._id = 0
        self._lock = threading.Lock()
        self._stderr: list[str] = []

    # ---------- 生命周期 ----------

    def start(self) -> None:
        env = dict(os.environ)
        env.update(self.env)

        argv = spawn_command(self.command, self.args)

        try:
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
        except FileNotFoundError as exc:
            raise MCPError(f"启动失败，找不到命令 {self.command}") from exc
        # stderr 必须有人读，否则管道写满会把子进程卡死
        threading.Thread(target=self._drain_stderr, daemon=True).start()

        info = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "jancode-agent", "version": "1.0.0"},
        })
        self.server_info = (info or {}).get("serverInfo") or {}
        # 通知没有 id，发出即可，不能等回复
        self._notify("notifications/initialized")
        result = self._rpc("tools/list", {}) or {}
        self.tools = list(result.get("tools") or [])

    def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip())
            del self._stderr[:-40]

    def close(self) -> None:
        if not self.proc:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr[-6:])

    # ---------- 协议 ----------

    def _send(self, payload: dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise MCPError("服务未启动")
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def _notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def _read_message(self) -> dict:
        """读一条 JSON-RPC 消息，跳过服务端打到 stdout 的杂项输出。"""
        assert self.proc and self.proc.stdout
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise MCPError("服务已退出" + (": " + self.stderr_tail() if self._stderr else ""))
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        raise MCPError(f"等待回复超时（{self.timeout:.0f} 秒）")

    def _rpc(self, method: str, params: dict) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            while True:
                msg = self._read_message()
                # 服务端可能发来请求或通知（有 method 没 id），忽略掉继续等
                if msg.get("id") != rid:
                    continue
                if "error" in msg:
                    err = msg["error"] or {}
                    raise MCPError(str(err.get("message") or err))
                return msg.get("result") or {}

    # ---------- 工具 ----------

    def call_tool(self, tool: str, arguments: dict | None = None) -> str:
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments or {}})
        parts = []
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, dict):
                parts.append(json.dumps(block, ensure_ascii=False))
        text = "\n".join(p for p in parts if p)
        if result.get("isError"):
            raise MCPError(text or "工具执行失败")
        return text

    def tool_specs(self, prefix: str = "mcp") -> list[dict]:
        """转成本项目智能体能用的工具格式。"""
        out = []
        for tool in self.tools:
            schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
            # 必须和本项目工具声明同构，否则 provider 会因为格式不对整批拒绝
            out.append({
                "type": "function",
                "function": {
                    "name": f"{prefix}__{slug(self.name)}__{slug(tool.get('name'))}",
                    "description": (f"[{self.name}] "
                                    + str(tool.get("description") or "")).strip(),
                    "parameters": schema,
                },
            })
        return out


def probe(server: dict) -> dict:
    """连一次，报告这个服务能不能用、有哪些工具。给界面状态用。"""
    client = MCPClient(server["name"], server["command"], server.get("args"),
                       server.get("env"))
    try:
        client.start()
        return {"ok": True, "tools": [t.get("name") for t in client.tools],
                "server": client.server_info.get("name") or server["name"],
                "error": ""}
    except (MCPError, Exception) as exc:
        return {"ok": False, "tools": [], "server": "", "error": str(exc)}
    finally:
        client.close()


class Manager:
    """进程级单例：MCP 服务起一次，所有任务共用。

    每个任务都重新起一遍子进程太慢（npx 光启动就要一两秒），
    所以按需启动、进程内复用。
    """

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._errors: dict[str, str] = {}
        self._loaded = False

    def load(self, force: bool = False) -> None:
        if self._loaded and not force:
            return
        self._loaded = True
        for server in load_servers():
            if not server["enabled"]:
                continue
            if server["name"] in self._clients and self._clients[server["name"]].alive:
                continue
            client = MCPClient(server["name"], server["command"],
                               server.get("args"), server.get("env"))
            try:
                client.start()
                self._clients[server["name"]] = client
                self._errors.pop(server["name"], None)
            except Exception as exc:  # 一个服务起不来不能拖垮其他服务
                self._errors[server["name"]] = str(exc)
                client.close()

    def reload(self) -> None:
        for client in list(self._clients.values()):
            client.close()
        self._clients.clear()
        self._errors.clear()
        self._loaded = False
        self.load(force=True)

    def specs(self) -> list[dict]:
        self.load()
        out: list[dict] = []
        for client in self._clients.values():
            out.extend(client.tool_specs())
        return out

    def call(self, full_name: str, arguments: dict | None):
        from .tools import ToolResult

        self.load()
        bits = full_name.split("__", 2)
        if len(bits) != 3:
            return ToolResult(False, f"工具名 {full_name!r} 格式不对")
        _, server, tool = bits
        client = self._clients.get(server)
        if client is None:
            # 工具名里的服务名是洗过的，这里按洗过的名字再找一次
            client = next((c for n, c in self._clients.items() if slug(n) == server), None)
        if client is None:
            return ToolResult(False, f"MCP 服务 {server!r} 未运行：{self._errors.get(server, '未配置')}")
        try:
            return ToolResult(True, client.call_tool(tool, arguments or {}))
        except MCPError as exc:
            return ToolResult(False, f"MCP 工具执行失败：{exc}")
        except Exception as exc:
            return ToolResult(False, f"MCP 工具异常：{exc}")

    def status(self) -> list[dict]:
        self.load()
        rows = []
        for server in load_servers():
            client = self._clients.get(server["name"])
            rows.append({
                "name": server["name"],
                "command": server["command"] + " " + " ".join(server.get("args") or []),
                "enabled": server["enabled"],
                "running": bool(client and client.alive),
                "tools": [t.get("name") for t in (client.tools if client else [])],
                "error": self._errors.get(server["name"], ""),
            })
        return rows

    def close_all(self) -> None:
        for client in list(self._clients.values()):
            client.close()
        self._clients.clear()


manager = Manager()
