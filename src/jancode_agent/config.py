"""配置加载。

设计取舍：不引入 pydantic 等重依赖，用标准库 tomllib 直接读 TOML。
配置文件位置与字段名刻意与常见约定保持一致，降低迁移成本。

配置优先级（后者覆盖前者）：
    内置默认值  ->  配置文件  ->  环境变量
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

# 默认配置文件位置。允许用 JANCODE_AGENT_CONFIG 覆盖，便于多套配置并存。
DEFAULT_CONFIG_PATH = Path.home() / ".jancode-agent" / "config.toml"

# 国内常见中转站与厂商的默认入口，供 --provider 快速切换。
BUILTIN_PROVIDERS: dict[str, dict[str, str]] = {
    "aionclaw": {
        "base_url": "https://router.aionclaw.com/v1",
        "model": "deepseek-v4-pro",
        "label": "AionClaw 中转站",
    },
    "janzhao": {
        "base_url": "https://janzhao.cn:9090/v1",
        "model": "deepseek-v3",
        "label": "janzhao 自建网关",
    },
    "junzi": {
        "base_url": "https://charlene.cat:9090/v1",
        "model": "deepseek-v3",
        "label": "钧子AI 中转站",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "label": "DeepSeek 官方",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "label": "智谱 GLM",
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "label": "月之暗面 Kimi",
    },
    "bailian": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "label": "阿里百炼",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "label": "硅基流动",
    },
}


@dataclass
class ProviderConfig:
    """单个模型供应商的接入参数。"""

    name: str
    base_url: str
    model: str
    api_key: str = ""
    label: str = ""
    # 走哪种协议。国内厂商普遍用 chat；部分中转站支持 responses。
    wire_api: str = "chat"
    # 单次请求超时（秒）。模型推理可能很慢，默认给足。
    timeout: float = 300.0
    # 采样温度。编程任务偏确定性，默认较低。
    temperature: float = 0.2


@dataclass
class AgentConfig:
    """智能体运行参数。"""

    provider: ProviderConfig
    # 工具调用循环上限，防止模型陷入死循环把额度跑光。
    max_steps: int = 40
    # 工具输出回灌给模型前截断的最大字符数。
    max_tool_output: int = 20_000
    # 是否允许执行 shell 命令。默认允许，但可用配置关闭。
    allow_bash: bool = True
    # shell 命令超时（秒）。
    bash_timeout: float = 120.0
    # 工作目录边界：文件工具的读写不得越出此目录。
    workspace: Path = field(default_factory=Path.cwd)
    # 是否允许主智能体把子任务派给子智能体。关闭后 task 工具不再出现，
    # 系统提示词里也不会提它——说了有却调用不到，模型会反复空试。
    allow_subagents: bool = True
    # 子智能体自己的工具循环上限。刻意比主智能体小：
    # 子任务应当是聚焦的，跑太久说明任务没被拆清楚。
    max_subagent_steps: int = 20
    # 允许的派生层数。默认 1 表示「子智能体不能再派生子智能体」。
    # 不做限制的话，模型可以无限套娃，一层层把额度吃光。
    max_subagent_depth: int = 1
    # 子智能体用的模型。留空表示与主智能体相同。
    # 用处：把「翻文件、找代码」这类活丢给便宜快的模型，把贵的留给主循环。
    subagent_model: str = ""


def _env_override(provider: ProviderConfig) -> ProviderConfig:
    """环境变量覆盖，便于 CI 与临时切换而不改配置文件。

    JANCODE_API_KEY 优先级最高，因为密钥最不该写进文件。
    """
    key = os.environ.get("JANCODE_API_KEY", "").strip()
    base = os.environ.get("JANCODE_BASE_URL", "").strip()
    model = os.environ.get("JANCODE_MODEL", "").strip()
    changes: dict[str, str] = {}
    if key:
        changes["api_key"] = key
    if base:
        changes["base_url"] = base.rstrip("/")
    if model:
        changes["model"] = model
    return replace(provider, **changes) if changes else provider


def _provider_from_name(name: str, api_key: str = "") -> ProviderConfig:
    spec = BUILTIN_PROVIDERS.get(name)
    if spec is None:
        known = "、".join(sorted(BUILTIN_PROVIDERS))
        raise ValueError(f"未知的供应商 {name!r}。可用的有：{known}")
    return ProviderConfig(
        name=name,
        base_url=spec["base_url"],
        model=spec["model"],
        label=spec["label"],
        api_key=api_key,
    )


def load_config(
    path: Path | None = None,
    provider_name: str | None = None,
    workspace: Path | None = None,
) -> AgentConfig:
    """读取配置。文件不存在时回退到内置默认，不抛异常。

    这样设计是为了让 `jancode --provider aionclaw` 在零配置下就能跑起来。
    """
    path = path or DEFAULT_CONFIG_PATH
    raw: dict = {}
    if path.is_file():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)

    provider_table = raw.get("provider", {})
    name = provider_name or provider_table.get("name") or "aionclaw"

    if name in BUILTIN_PROVIDERS:
        provider = _provider_from_name(name, api_key=provider_table.get("api_key", ""))
        # 配置文件里显式写的字段覆盖内置预设
        for fld in ("base_url", "model", "label", "wire_api"):
            if provider_table.get(fld):
                provider = replace(provider, **{fld: provider_table[fld]})
        if provider_table.get("timeout"):
            provider = replace(provider, timeout=float(provider_table["timeout"]))
        if provider_table.get("temperature") is not None:
            provider = replace(provider, temperature=float(provider_table["temperature"]))
    else:
        # 完全自定义的供应商：必须给出 base_url 与 model
        if not provider_table.get("base_url") or not provider_table.get("model"):
            # 走到这里有两种可能：想配自定义供应商但漏了字段，或者单纯把名字打错了。
            # 把内置可选项一并列出，打错字的人才知道下一步该写什么。
            known = "、".join(sorted(BUILTIN_PROVIDERS))
            raise ValueError(
                f"未识别的供应商 {name!r}。\n"
                f"  如果这是内置供应商，名字可能拼错了。可选：{known}\n"
                f"  如果要接自建站，请在配置文件的 [provider] 里同时写上 base_url 与 model。"
            )
        provider = ProviderConfig(
            name=name,
            base_url=str(provider_table["base_url"]).rstrip("/"),
            model=provider_table["model"],
            api_key=provider_table.get("api_key", ""),
            label=provider_table.get("label", name),
            wire_api=provider_table.get("wire_api", "chat"),
            timeout=float(provider_table.get("timeout", 300.0)),
            temperature=float(provider_table.get("temperature", 0.2)),
        )

    provider = _env_override(provider)

    agent_table = raw.get("agent", {})
    return AgentConfig(
        provider=provider,
        max_steps=int(agent_table.get("max_steps", 40)),
        max_tool_output=int(agent_table.get("max_tool_output", 20_000)),
        allow_bash=bool(agent_table.get("allow_bash", True)),
        bash_timeout=float(agent_table.get("bash_timeout", 120.0)),
        workspace=Path(workspace) if workspace else Path.cwd(),
        allow_subagents=bool(agent_table.get("allow_subagents", True)),
        max_subagent_steps=int(agent_table.get("max_subagent_steps", 20)),
        max_subagent_depth=int(agent_table.get("max_subagent_depth", 1)),
        subagent_model=str(agent_table.get("subagent_model", "")),
    )
