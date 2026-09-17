"""国内主流模型的目录。

为什么要有这个：用户拿到的是各种中转站或官方开放平台，模型名各不相同，
每次都要自己查文档抄 base_url 和模型名。这里把常用的按厂商列出来，
填一套就能直接用。

注意：这里只放公开的官方接口地址和模型名，不预设任何密钥。
能不能用取决于用户自己的账号权限，所以界面上要允许随时改成中转站地址。
"""

from __future__ import annotations

# (厂商, 接口地址, 模型列表, 说明)
CATALOG: list[dict] = [
    {"vendor": "钧子AI 中转站", "base_url": "https://janzhao.cn:9090/v1",
     "models": ["gpt-6-astra", "gpt-5.6", "gpt-5.4", "deepseek-v4-pro"],
     "note": "你自己的中转站"},
    {"vendor": "DGX · DeepSeek V4.1 Flash（四台）",
     "base_url": "https://ai.janzhao.cn:9090/deepseek/mac-studio-5/v41/v1",
     "models": ["deepseek-v4.1-flash"], "note": "DGX1+DGX2+DGX3+DGX4"},
    {"vendor": "DGX · DeepSeek V4（两台）",
     "base_url": "https://ai.janzhao.cn:9090/deepseek/mac-studio-5/v1",
     "models": ["deepseek-v4-flash-0731"], "note": "DGX1+DGX2"},
    {"vendor": "DGX · Qwen3.8 Flash Next",
     "base_url": "https://ai.janzhao.cn:9090/deepseek/mac-studio-5/qwen/v1",
     "models": ["qwen3.8-flash-next"], "note": "DGX1+DGX2"},
    {"vendor": "DGX · Qwen（DGX1）",
     "base_url": "https://ai.janzhao.cn:9090/qwen/v1",
     "models": ["qwen3.6-35b-a3b"], "note": "单台 DGX"},
    {"vendor": "DeepSeek 官方", "base_url": "https://api.deepseek.com/v1",
     "models": ["deepseek-chat", "deepseek-reasoner"],
     "note": "官方开放平台，按量计费"},
    {"vendor": "阿里通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen3-coder-plus", "qwen-long"],
     "note": "DashScope 兼容 OpenAI 协议"},
    {"vendor": "月之暗面 Kimi", "base_url": "https://api.moonshot.cn/v1",
     "models": ["moonshot-v1-128k", "moonshot-v1-32k", "kimi-k2-0905-preview"],
     "note": "长文本见长"},
    {"vendor": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "models": ["glm-4-plus", "glm-4-air", "glm-4-flash", "glm-4.5"],
     "note": "智谱开放平台"},
    {"vendor": "字节豆包", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "models": ["doubao-pro-32k", "doubao-lite-128k"],
     "note": "火山方舟，模型名要用自己的接入点 ID"},
    {"vendor": "百度文心", "base_url": "https://qianfan.baidubce.com/v2",
     "models": ["ernie-4.5-turbo-128k", "ernie-4.0-8k", "ernie-speed-128k"],
     "note": "千帆平台"},
    {"vendor": "腾讯混元", "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
     "models": ["hunyuan-turbos-latest", "hunyuan-large", "hunyuan-standard"],
     "note": "腾讯云混元"},
    {"vendor": "MiniMax", "base_url": "https://api.minimax.chat/v1",
     "models": ["abab6.5s-chat", "abab6.5-chat"],
     "note": "MiniMax 开放平台"},
    {"vendor": "阶跃星辰", "base_url": "https://api.stepfun.com/v1",
     "models": ["step-2-16k", "step-1-32k", "step-1v-8k"],
     "note": "阶跃星辰"},
    {"vendor": "讯飞星火", "base_url": "https://spark-api-open.xf-yun.com/v1",
     "models": ["4.0Ultra", "generalv3.5", "lite"],
     "note": "星火认知大模型"},
    {"vendor": "百川智能", "base_url": "https://api.baichuan-ai.com/v1",
     "models": ["Baichuan4", "Baichuan3-Turbo", "Baichuan3-Turbo-128k"],
     "note": "百川开放平台"},
    {"vendor": "零一万物", "base_url": "https://api.lingyiwanwu.com/v1",
     "models": ["yi-lightning", "yi-large", "yi-medium"],
     "note": "零一万物"},
    {"vendor": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
     "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct",
                "THUDM/glm-4-9b-chat"],
     "note": "聚合平台，一个 key 用多家开源模型"},
    {"vendor": "本地 Ollama", "base_url": "http://127.0.0.1:11434/v1",
     "models": ["qwen3.6:35b-a3b"],
     "note": "本机跑的模型，不需要密钥"},
]


def vendors() -> list[dict]:
    return [dict(row) for row in CATALOG]


def all_model_names() -> list[str]:
    names = []
    for row in CATALOG:
        names.extend(row["models"])
    return names
