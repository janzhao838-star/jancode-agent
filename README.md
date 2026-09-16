# JanCode Agent

开源终端 AI 编程助手，面向**国内大模型**与**自建中转站**。

不绑定任何厂商 SDK，不内置推广，不向任何第三方回传数据。换一个 `base_url`
就换一个模型。

## 为什么做这个

市面上多数同类工具默认接国外模型、依赖国外服务，并且把推广码和推荐位写死在
代码里。这个项目反过来：只接国内可直连的模型服务，配置透明，代码可读。

## 安装

### 一键安装（推荐，不需要懂命令行）

**macOS / Linux**

```bash
curl -sL https://raw.githubusercontent.com/janzhao838-star/jancode-agent/main/scripts/install.sh | bash
```

**Windows**（在 PowerShell 里运行）

```powershell
iwr -useb https://raw.githubusercontent.com/janzhao838-star/jancode-agent/main/scripts/install.ps1 | iex
```

装完关掉窗口重开一个终端，就能用了：

```bash
jancode-agent --web
```

脚本会自己处理：找 Python（版本不够会告诉你怎么装）、下载源码、建独立环境
（不动系统 Python）、失败时自动换国内镜像、创建启动命令并加进 PATH。

### 手动安装

```bash
git clone https://github.com/janzhao838-star/jancode-agent.git
cd jancode-agent
pip install -e .
```

需要 Python 3.11 以上。运行期只依赖 `httpx`。

## 快速开始

```bash
export JANCODE_API_KEY=你的密钥

jancode --list-providers                  # 看有哪些内置供应商
jancode --provider aionclaw "介绍一下这个项目"
jancode                                    # 交互模式
jancode --web                              # 图形界面（浏览器里用）
```

图形界面适合不想敲命令的场景：在浏览器里描述任务，能看到智能体每一步在做什么。
服务只监听 `127.0.0.1`，不对外暴露。

接自建中转站：

```bash
jancode --base-url https://你的站/v1 --model deepseek-v3 "写一个快速排序"
```

## 内置供应商

| 名称 | 说明 |
|---|---|
| `aionclaw` | AionClaw 中转站 |
| `janzhao` | janzhao 自建网关 |
| `junzi` | 钧子AI 中转站 |
| `deepseek` | DeepSeek 官方 |
| `zhipu` | 智谱 GLM |
| `kimi` | 月之暗面 Kimi |
| `bailian` | 阿里百炼 |
| `siliconflow` | 硅基流动 |

完整参数见 `jancode --list-providers`。

## 配置文件

放在 `~/.jancode-agent/config.toml`，可选：

```toml
[provider]
name = "aionclaw"
api_key = "sk-..."       # 也可以改用环境变量，更安全
model = "deepseek-v4-pro"
wire_api = "chat"        # 或 responses

[agent]
max_steps = 40           # 工具调用循环上限
allow_bash = true
bash_timeout = 120
```

优先级：内置默认 → 配置文件 → 环境变量。

## 智能体能做什么

| 工具 | 用途 |
|---|---|
| `read_file` | 读文件，带行号 |
| `write_file` | 新建或覆盖文件 |
| `edit_file` | 字面量替换，要求唯一匹配 |
| `list_dir` | 列目录 |
| `grep` | 正则搜索内容 |
| `bash` | 执行 shell 命令 |

**安全边界**：所有文件操作都被限制在工作目录内，越界读写直接拒绝。
`bash` 会拦下最常见的破坏性命令。这不是完备的沙箱——真要强隔离请用容器。

## 设计上的几个取舍

- **循环有硬上限**。模型陷入反复尝试时必须有外力叫停，否则一直消耗额度。
- **工具失败不中断会话**。失败原因回灌给模型，多数情况它能自己纠正。
- **检测重复调用**。同一个工具、同样参数连续出现，是卡死的典型信号，提前停。
- **路径越界对所有入口生效**。不是只在统一分发处检查——那样换个入口就绕过。
- **报错要能指导下一步**。"文件不存在"不如"文件不存在，可以先用 list_dir"。

## 实测过的对接情况

对 `router.aionclaw.com` 发过真实请求，返回 401 且带中转站原始报错：

```
返回 401：{"error":{"code":"","message":"Invalid token...","type":"new_api_error"}}
```

说明协议路径正确（401 而非 404）、错误能原样透出。AionClaw 与钧子AI
同用 New API 引擎。

## 测试

```bash
pip install -e ".[dev]"
pytest
```

## 许可

Apache-2.0。
