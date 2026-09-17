"""推理强度档位：界面上固定四档，实际发送按模型能力收敛。

界面永远给四个选项：默认 / 低 / 高 / 最大。这样任何模型下选择器都在，
用户不用先搞懂「我这个模型支不支持调节」。

但 reasoning_effort 不是所有模型都认：给不支持的模型硬塞这个字段，
provider 会直接返回 400，整个请求失败。所以发送前要过一遍
effective_effort() 收敛：

- 默认 → 不带字段（任何模型都安全）
- 已知支持的模型 → 选什么发什么（超出的档位收到该模型最高档）
- 完全未知的模型 → 按所选发送；多数网关会剥掉不认识的字段，
  真不认的会 400，请求层检测到后自动去掉该字段重试一次。

中转站的 /v1/models 通常只返回模型名，没有能力元数据（实测确实如此），
所以能力判断用公开的模型家族规则；将来若某个 provider 提供了能力字段，
应该优先用它，这里就是兜底层。
"""

from __future__ import annotations

import re

# 界面固定四档：任何模型都显示，选择器不再凭模型名消失。
CHOICES = ["default", "low", "high", "max"]

# 各家推理模型实际支持的档位（内部用，负责收敛）。
# gpt-5 及以后还支持 minimal（极速），普通推理模型没有这一档。
_THREE = ["low", "medium", "high"]
_FOUR = ["minimal", "low", "medium", "high"]
# 只支持「开/关思考」的，无论选什么都映射成 high（开）
_ONE = ["high"]


def options_for(model: str) -> list[str]:
    """这个模型实际支持的推理档位。空列表表示未知/不支持调节。"""
    name = (model or "").strip().lower()
    if not name:
        return []

    # OpenAI 推理系列：gpt-5 及以后的/o1/o3/o4/codex 都支持 low/medium/high
    if re.match(r"^(o1|o3|o4|gpt-[5-9]|codex)", name):
        return list(_FOUR)

    # Anthropic 的思考档位
    if name.startswith("claude") and ("thinking" in name or "4-" in name or "5-" in name):
        return list(_THREE)

    # DeepSeek：只有 reasoner 类带思考，普通对话模型不收这个字段
    if "deepseek-reasoner" in name or "deepseek-r" in name or "deepseek-v4" in name:
        return list(_ONE)

    # 通义：带 thinking 的（qwq、qwen3-*-thinking）
    if "qwq" in name or ("qwen" in name and "thinking" in name):
        return list(_ONE)

    # 智谱：z1 系列是推理模型；glm-5 系按开/关处理
    if name.startswith("glm-z") or name.startswith("glm-5") or "glm-4.5" == name:
        return list(_ONE)

    # Kimi：k2 thinking（kimi-k2-0905-preview 属于推理型）
    if "kimi-k2" in name or ("kimi" in name and "thinking" in name) or "k1.5" in name:
        return list(_ONE)

    # 阶跃：step-2 支持思考开关
    if name.startswith("step-2"):
        return list(_ONE)

    # 其余（qwen-plus、glm-4-plus、moonshot-v1、doubao、ernie、hunyuan、
    # minimax、baichuan、yi、本地 ollama 等）：未知，交给出错兜底
    return []


def effective_effort(model: str, requested: str) -> str:
    """把界面上的选择收敛成真正要发送的值。

    返回空字符串表示不带 reasoning_effort 字段。
    """
    want = (requested or "").strip().lower()
    if want in ("", "default"):
        return ""

    supported = options_for(model)
    if not supported:
        # 未知模型：低/高原样发，最大收敛到 high（业界最高档就是 high）。
        return "high" if want == "max" else want

    if want in supported:
        return want

    # 选的档位超出该模型能力：收到它支持的最高档。
    # 只支持开/关的模型，无论低/高/最大都等于「开」。
    return supported[-1]


def label(level: str) -> str:
    return {"default": "默认", "minimal": "极速", "low": "低", "medium": "均衡",
            "high": "高", "max": "最大"}.get(level, level)
