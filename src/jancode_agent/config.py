"""配置加载。

设计取舍：不引入 pydantic 等重依赖，用标准库 tomllib 直接读 TOML。
配置文件位置与字段名刻意与常见约定保持一致，降低迁移成本。

配置优先级（后者覆盖前者）：
    内置默认值  ->  配置文件  ->  环境变量
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

# 默认配置文件位置。允许用 JANCODE_AGENT_CONFIG 覆盖，便于多套配置并存。
DEFAULT_CONFIG_PATH = Path.home() / ".jancode-agent" / "config.toml"

# 国内常见中转站与厂商的默认入口，供 --provider 快速切换。
BUILTIN_PROVIDERS: dict[str, dict[str, str]] = {
    "janzhao": {
        "base_url": "https://janzhao.cn:9090/v1",
        "model": "deepseek-v4-pro",
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
    # 下面五条是从 DeepSeek Harness（本机另一套智能体工具）原样搬来的
    # 自建接入：同一台 Mac Studio 上跑的开源大模型，OpenAI 兼容协议。
    # 密钥沿用 Harness 的环境变量名，两边共用同一把，不用配两次。
    "dgx-glm53": {
        "base_url": "https://ai.janzhao.cn:9090/deepseek/mac-studio-5/glm53/v1",
        "model": "glm-5.3-flash",
        "label": "GLM 5.3 Flash（自建）",
        "key_env": "DGX_GLM53_KEY",
        "effort": "high",
    },
    "dgx-deepseek-v41": {
        "base_url": "https://ai.janzhao.cn:9090/deepseek/mac-studio-5/v41/v1",
        "model": "deepseek-v4.1-flash",
        "label": "DeepSeek V4.1 Flash（自建）",
        "key_env": "DGX_DEEPSEEK_V41_KEY",
        "effort": "high",
    },
    "dgx-deepseek-v4": {
        "base_url": "https://ai.janzhao.cn:9090/deepseek/mac-studio-5/v1",
        "model": "deepseek-v4-flash-0731",
        "label": "DeepSeek V4 Flash（自建）",
        "key_env": "DGX_DEEPSEEK_TP2_KEY",
        "effort": "low",
    },
    "dgx-qwen38": {
        "base_url": "https://ai.janzhao.cn:9090/v1",
        "model": "qwen3.8-27b-sglang",
        "label": "Qwen3.8 27B（自建）",
        "key_env": "DGX_QWEN_API_KEY",
    },
    "dgx-qwen-flash": {
        "base_url": "https://ai.janzhao.cn:9090/deepseek/mac-studio-5/qwen/v1",
        "model": "qwen3.8-flash-next",
        "label": "Qwen3.8 Flash Next（自建）",
        "key_env": "DGX_QWEN_FLASH_TP2_KEY",
        "effort": "low",
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
    # 推理强度／速度档位：low / medium / high，空表示用服务端默认。
    # 请求里以 reasoning_effort 传给后端；不支持的模型会忽略它。
    effort: str = ""
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
    # 工作模式：auto/sandbox/plan/readonly。
    # 它不只进系统提示词，还由 Toolbox 在代码层做硬拦截——
    # 只写提示词的话，模型不听话就直接动手改文件了。
    mode: str = "auto"
    # 是否允许执行 shell 命令。默认允许，但可用配置关闭。
    allow_bash: bool = True
    # shell 命令超时（秒）。
    bash_timeout: float = 120.0
    # 工作目录边界：文件工具的读写不得越出此目录。
    workspace: Path = field(default_factory=Path.cwd)
    # 追加到系统提示词末尾的内容：自定义智能体的角色设定、以及技能库。
    # 放成配置里的一个字段，而不是让 server 去改 Agent 的内部状态——
    # 命令行和子智能体也走同一条路径，谁都改得到。
    system_extra: str = ""
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
    # 中转站给的接入片段基本都是 OPENAI_* 这套通用变量，
    # 只认自己的 JANCODE_* 就等于「照抄文档里的命令却不生效」。
    # 两套都认，自己的优先。
    key = _env_first("JANCODE_API_KEY", "OPENAI_API_KEY")
    base = _env_first("JANCODE_BASE_URL", "OPENAI_BASE_URL")
    model = _env_first("JANCODE_MODEL", "OPENAI_MODEL")
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
    # 预设可以声明 key_env：本机多套工具共用同一把密钥时，
    # 不用在 jancode 配置里再存一份（密钥少一处是一处）。
    # 显式传入的 api_key（配置文件/界面）优先于环境变量。
    key = api_key or _env_first(spec["key_env"]) if spec.get("key_env") else api_key
    return ProviderConfig(
        name=name,
        base_url=spec["base_url"],
        model=spec["model"],
        label=spec["label"],
        api_key=key,
        effort=spec.get("effort", ""),
    )


def _env_first(*names: str) -> str:
    """按顺序取第一个非空的环境变量。"""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def load_config(
    path: Path | None = None,
    provider_name: str | None = None,
    workspace: Path | None = None,
) -> AgentConfig:
    """读取配置。文件不存在时回退到内置默认，不抛异常。

    这样设计是为了让 `jancode --provider janzhao` 在零配置下就能跑起来。
    """
    path = path or DEFAULT_CONFIG_PATH
    raw: dict = {}
    if path.is_file():
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            # 配置写坏一行不该让程序直接崩：回退内置默认并指路。
            # 不抛异常是本函数的约定（docstring 写明），坏文件静默忽略
            # 又太坑——打印到 stderr，用户至少知道为什么没生效。
            print(f"配置文件 {path} 解析失败，已忽略（{exc}）。",
                  file=sys.stderr, flush=True)
            raw = {}

    provider_table = raw.get("provider", {})
    name = provider_name or provider_table.get("name") or "janzhao"

    if name in BUILTIN_PROVIDERS:
        provider = _provider_from_name(name, api_key=provider_table.get("api_key", ""))
        # 配置文件里显式写的字段覆盖内置预设
        for fld in ("base_url", "model", "label", "wire_api"):
            if provider_table.get(fld):
                provider = replace(provider, **{fld: provider_table[fld]})
        if provider_table.get("timeout"):
            provider = replace(provider, timeout=float(provider_table["timeout"]))
        # effort 也属于供应商接入参数，漏读的话配置文件里写了也不生效
        # （Web 端能设档位，命令行/TOML 用户却设不了）。
        if provider_table.get("effort"):
            provider = replace(provider, effort=str(provider_table["effort"]))
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
            effort=str(provider_table.get("effort", "")),
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
        # mode 与 system_extra 以前只在 Web 端由界面写入，
        # 配置文件里写了同样被静默丢弃——一并接上。
        mode=str(agent_table.get("mode", "auto")),
        system_extra=str(agent_table.get("system_extra", "")),
        bash_timeout=float(agent_table.get("bash_timeout", 120.0)),
        workspace=Path(workspace) if workspace else Path.cwd(),
        allow_subagents=bool(agent_table.get("allow_subagents", True)),
        max_subagent_steps=int(agent_table.get("max_subagent_steps", 20)),
        max_subagent_depth=int(agent_table.get("max_subagent_depth", 1)),
        subagent_model=str(agent_table.get("subagent_model", "")),
    )
