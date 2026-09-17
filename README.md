# JanCode Agent

开源终端 AI 编程助手，面向**国内大模型**与**自建中转站**。

不绑定任何厂商 SDK，不内置推广，不向任何第三方回传数据。换一个 `base_url`
就换一个模型。

## 桌面版（双击就能用）

不想碰命令行就用桌面版：一个原生窗口，左边是对话历史、工作区和当前模型，
中间直接说人话。支持 macOS 和 Windows。

下载：<https://github.com/janzhao838-star/jancode-agent/releases/latest>

| 系统 | 文件 |
| --- | --- |
| macOS（Apple 芯片） | `JanCode-Agent-macos-arm64.zip` |
| macOS（Intel） | `JanCode-Agent-macos-x64.zip` |
| Windows 64 位 | `JanCode-Agent-windows-x64.zip` |

macOS 第一次打开会被系统拦一下——安装包没有苹果的付费开发者签名，开源项目
大多如此。在「应用程序」里**右键点图标 → 打开 → 再点打开**，之后就不拦了。
Windows 直接运行 `JanCode-Agent.exe`，不需要另外装 Python。

想自己打：

```bash
pip install -e ".[desktop]" pyinstaller
bash scripts/build-desktop.sh
```

### 桌面版里有什么

- **对话历史**：每次对话都留着，随时点回去；可以删，可以重跑
- **工具调用明细**：每一步读了哪个文件、跑了什么命令，点一下展开完整输出
- **子智能体分层展示**：子智能体干的活缩进显示，看得出到底是谁在干活
- **微信推送**：填一个企业微信群机器人 webhook，任务跑完把结论推到群里
- **说话即用**：不用记任何命令

桌面版没有另写一套界面，用的就是 `jancode-agent --web` 那个页面，只是装进了
系统自带窗口（macOS 用 WKWebView，Windows 用 WebView2），所以不多占内存，
安装包也小。

## 为什么做这个

市面上多数同类工具默认接国外模型、依赖国外服务，并且把推广码和推荐位写死在
代码里。这个项目反过来：只接国内可直连的模型服务，配置透明，代码可读。

## 安装

### 一键安装（推荐，不需要懂命令行）

**macOS / Linux**

```bash
curl -fsSL -o install.sh https://raw.githubusercontent.com/janzhao838-star/jancode-agent/main/scripts/install.sh
bash install.sh
```

**Windows**（在 PowerShell 里运行）

```powershell
iwr -useb https://raw.githubusercontent.com/janzhao838-star/jancode-agent/main/scripts/install.ps1 -OutFile install.ps1
.\install.ps1
```

先下载、再运行，中间可以把脚本打开看一眼。这个项目的卖点之一就是配置透明，
安装脚本不该是个黑盒。如果 PowerShell 说「禁止运行脚本」，用这条绕过（只对本次生效）：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

想一步到位也可以，效果完全一样：

```bash
curl -sL https://raw.githubusercontent.com/janzhao838-star/jancode-agent/main/scripts/install.sh | bash
```

装完关掉窗口重开一个终端，就能用了：

```bash
jancode-agent --web
```

`jancode` 和 `jancode-agent` 是两个等价的命令名，安装脚本创建前者，pip 安装生成后者，
敲哪个都行。

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
jancode --provider janzhao "介绍一下这个项目"
jancode                                    # 交互模式
jancode --web                              # 图形界面（浏览器里用）
```

图形界面适合不想敲命令的场景：在浏览器里描述任务，能看到智能体每一步在做什么。
服务只监听 `127.0.0.1`，不对外暴露。

接自建中转站：

```bash
jancode --base-url https://你的站/v1 --model deepseek-v3 "写一个快速排序"
```

## 出问题了先跑自检

```bash
jancode-agent --doctor
```

它会检查 Python 版本、配置文件、密钥、工作目录，并**真的发一次请求**确认能不能连通。
每一项都会告诉你「哪里不对、该怎么办」：

```
  ✗ 连接中转站
      https://janzhao.cn:9090/v1 拒绝了这个密钥（401）

      → 密钥无效或已过期。到中转站的「令牌」页面重新创建一个。
      → 站点原始报错：{"error":{"message":"Invalid token",...}}
```

加 `--offline` 可以跳过网络检查。有问题时退出码为 1，便于脚本判断。

## 内置供应商

| 名称 | 说明 |
|---|---|
| `janzhao` | 钧子AI 中转站 |
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
name = "janzhao"
api_key = "sk-..."       # 也可以改用环境变量，更安全
model = "deepseek-v4-pro"
wire_api = "chat"        # 或 responses

[agent]
max_steps = 40           # 工具调用循环上限
allow_bash = true
bash_timeout = 120

# 子智能体（可省略，下面是默认值）
allow_subagents = true   # 是否允许把子任务派给子智能体
max_subagent_steps = 20  # 子智能体自己的循环上限
max_subagent_depth = 1   # 允许派生几层；1 表示子智能体不能再派生
subagent_model = ""      # 子智能体换一个模型（例如更便宜的）；留空则与主智能体相同
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
| `task` | 把一段独立子任务交给子智能体，只要它的结论（见下一节） |

**安全边界**：所有文件操作都被限制在工作目录内，越界读写直接拒绝。
`bash` 会拦下最常见的破坏性命令。这不是完备的沙箱——真要强隔离请用容器。

## 子智能体

主智能体可以把一段独立的子任务派给**子智能体**执行。子智能体有自己的上下文，
跑完只把最终结论交回来：

```
你 → 主智能体 ──task──→ 子智能体（自己的循环：读文件、grep、跑命令 …）
                        ←── 只有最终结论进入主智能体的历史
```

**为什么要有它**：价值全在上下文隔离。让子智能体去翻二十个文件找一个定义，
这二十次工具往返不会进入主智能体的上下文——主智能体只拿到一句结论。
不隔离的话，它就只是个更贵的工具调用。

**三个兜底**（都会真实生效，不是文档承诺）：

- `max_subagent_steps`：子智能体自己的循环上限，刻意比主智能体小。
  子任务跑太久，通常说明任务没被拆清楚。
- `max_subagent_depth`：默认 1，即子智能体不能再派生子智能体。
  不限制的话，模型可以一层层套下去把额度吃光。到上限后 `task` 工具会直接从
  工具列表里消失——比「留着但调用时报错」更省步数。
- 子智能体失败不拖垮主任务：失败原因作为工具结果回流，主智能体还有机会自己接手。

**过程可见**：子智能体的每一步会实时冒泡到命令行和网页界面，带缩进与标签，
不会变成一个跑几分钟、界面上毫无动静的黑盒。

不需要就整个关掉：

```bash
jancode --no-subagents "你的任务"
```

关闭后 `task` 工具不会出现，系统提示词里也不会提它——提示词里说了有、
工具列表里却没有，模型会反复调用一个不存在的工具，把步数耗光。

## 模式预设

「数字专家」页顶部有四种**模式预设**，选一个工作方式，之后的任务都按它来：

| 预设 | 行为 |
|------|------|
| 标准模式 | 全自动连续执行，危险操作先说明再动手 |
| 计划模式 | 先出完整分步计划，你确认后才执行修改 |
| 极简模式 | 回答尽量短：结论在前，过程在后 |
| 创造模式 | 主动给出多种可行方案并说明取舍 |

- **以此为准**：写进 `~/.jancode-agent/desktop-settings.json`，跨重启生效。
  模式由服务端代码强制执行（工具层拦截），不是只靠提示词自觉。
- **复制自定义**：把预设复制成你自己的智能体，随意改提示词。
  预设本体随代码分发，用户改不到——要改就复制，这是它和「数字专家」的区别。

## 上下文自动归档

长任务里上下文只增不减，最后要么撑爆模型窗口，要么被中转站按超长拒单。
jancode 会在每步开始前检查上下文体量，超过预算（约 12 万字符）就把**较早的**
工具输出和模型长篇回复压成「开头 + 已归档标记」的短桩：

- 最近 16 条消息永远保持原样（正在用的工具结果在里面）；
- 用户的话和系统提示词永不归档；
- 工具配对靠 `tool_call_id`，压桩不影响消息结构；
- 桩里写明原文多大、怎么找回（重新执行工具 / read_file 看源头）。

## 设计上的几个取舍

- **循环有硬上限**。模型陷入反复尝试时必须有外力叫停，否则一直消耗额度。
- **工具失败不中断会话**。失败原因回灌给模型，多数情况它能自己纠正。
- **检测重复调用**。同一个工具、同样参数连续出现，是卡死的典型信号，提前停。
- **路径越界对所有入口生效**。不是只在统一分发处检查——那样换个入口就绕过。
- **报错要能指导下一步**。"文件不存在"不如"文件不存在，可以先用 list_dir"。
- **子智能体只回传结论**。中间过程不进主上下文——这是它省上下文的唯一理由，
  做不到隔离就不该有它。

## 实测过的对接情况

下面这些是**真的跑过**的，不是「应该能用」：

- **对话与流式输出**：对中转站发真实请求，回答逐字流出。实测 170 个增量
  事件，首个事件 5.0 秒、全文 10.3 秒吐完、收尾 0.3 秒。
  （修之前是全文憋十几秒一次性出现——原因是我们自己在答案已经显示完之后
  又多发了一次完整请求，不是网关慢。这条教训值得记住。）
- **工具调用**：模型真的调用了 `read_file`，拿到真实文件内容并据此
  作答；分片到达的 `tool_calls` 能正确拼装（参数跨多个 chunk）。
- **MCP**：filesystem 与 memory 两个服务真的起来了，工具的调用与结果回灌都通。
- **工作模式**：代码层面拦截已验证——只读模式下写文件被拒，且文件确实没有
  落盘；模式名写错时按最严格的只读处理（fail-closed）。
- **界面**：六个页面都用无头浏览器真实渲染过，并用真实点击逐页验证过切换：
  每次都恰好一个页面显示、且就是目标页，各页内容标志物
  （`README.md` / `read_file` / 技能与专家条目等）如期出现。
- **打包产物**：安装版 `index.html` 与源码逐字节一致（md5 相同）；
  安装版跑真实请求也能产生流式增量。

## 已知未验证的部分

写在这里而不是留在对话里，免得下一个人以为全都验过了：

（界面点击切换原本列在这里，本轮已用真实点击验证，并固化成`scripts/uicheck.py`：
六个页面全部通过——每次都恰好一个页面处于显示状态且就是目标页，
且该页内容标志物出现。）

- **Windows 侧从未在真机上跑过**。命令拼装那一层已经用单元测试验过
  （`tests/test_windows_spawn.py`：win32 下 npx / npm / pnpm，以及解析到 `.cmd` 的
  路径，都会包成 `cmd /c …`；真正的 `.exe` 不包）——
  但真机运行时仍未验证。需要一台 Windows 机器跑一遍：安装、双击启动、
  MCP 服务启动、打包出的 `.exe` 能起来。

## 测试

```bash
pip install -e ".[dev]"
pytest          # 125 项。既有用假客户端驱动循环的单元测试，
                # 也有起真进程、真 HTTP 服务跑整个命令行的端到端测试

python scripts/crosscheck.py   # 静态交叉检查：script 块语法、元素 ID、
                               # 页面容器、接口名。退出码非 0 表示发现真问题
```
## 许可

Apache-2.0。
