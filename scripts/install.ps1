# JanCode Agent 一键安装（Windows）
#
# 与 install.sh 保持同样的原则：每一处可能出问题的地方都要说清楚
# 「发生了什么、该怎么办」。用 Windows 的人多半不熟悉命令行，
# 报错看不懂就等于装不上。
$ErrorActionPreference = 'Stop'

$Repo   = if ($env:JANCODE_AGENT_REPO) { $env:JANCODE_AGENT_REPO } else { 'https://github.com/janzhao838-star/jancode-agent.git' }
$Root   = Join-Path $env:USERPROFILE '.jancode-agent'
$Target = Join-Path $Root 'src'
$Venv   = Join-Path $Root 'venv'

function Say($m)  { Write-Host $m }
function Ok($m)   { Write-Host "  OK $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  !  $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "`n安装中止：$m" -ForegroundColor Red; exit 1 }

Say ""
Say "JanCode Agent 安装程序"
Say "─────────────────────────────"

# ── 1. 找 Python ──
Say ""
Say "① 检查 Python"
$py = $null
foreach ($cand in @('py -3.13','py -3.12','py -3.11','python')) {
    $exe = $cand.Split(' ')[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $ver = & $exe -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
        if ($ver -and [version]$ver -ge [version]'3.11') { $py = $cand; break }
        else { Warn "$cand 版本过低（需要 3.11 以上，当前 $ver）" }
    } catch { }
}
if (-not $py) {
    Die @"
没有找到 Python 3.11 以上。
  怎么装：
    1. 打开 https://www.python.org/downloads/windows/
    2. 下载 3.12 的安装包
    3. 安装时务必勾选 "Add python.exe to PATH"
  装完关掉这个窗口，重新打开再运行一次本脚本。
"@
}
Ok "$py"
$pythonExe = $py.Split(' ')[0]
$pythonArgs = @()
if ($py.Split(' ').Count -gt 1) { $pythonArgs = $py.Split(' ')[1..($py.Split(' ').Count-1)] }

# ── 2. 取源码 ──
Say ""
Say "② 获取源码"
if (Test-Path (Join-Path $Target '.git')) {
    Say "  已存在，尝试更新…"
    try { & git -C $Target pull --ff-only --quiet 2>$null; Ok "已更新到最新" }
    catch { Warn "更新失败（可能是本地有改动），继续使用现有版本" }
} else {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Die @"
没有找到 git。
  怎么装：
    1. 打开 https://git-scm.com/download/win
    2. 下载并安装（一路下一步即可）
    3. 关掉这个窗口，重新打开再运行一次
"@
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $Target) | Out-Null
    try { & git clone --quiet --depth 1 $Repo $Target 2>$null }
    catch { Die "无法下载源码。请检查网络能否访问 GitHub。" }
    Ok "已下载到 $Target"
}

# ── 3. 建虚拟环境并安装 ──
Say ""
Say "③ 安装到独立环境（不会影响系统 Python）"
if (-not (Test-Path $Venv)) {
    & $pythonExe @pythonArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Die "创建虚拟环境失败。" }
}
$vpy = Join-Path $Venv 'Scripts\python.exe'
& $vpy -m pip install --quiet --upgrade pip 2>$null | Out-Null
& $vpy -m pip install --quiet -e $Target 2>$null
if ($LASTEXITCODE -ne 0) {
    Say "  默认源失败，改用国内镜像重试…"
    & $vpy -m pip install --quiet -e $Target -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) { Die "安装依赖失败。请检查网络。" }
}
Ok "已安装"

# ── 4. 启动命令 ──
Say ""
Say "④ 创建启动命令"
$Bin = Join-Path $Root 'bin'
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$cmdPath = Join-Path $Bin 'jancode-agent.cmd'
@"
@echo off
"$vpy" -m jancode_agent.cli %*
"@ | Set-Content -Path $cmdPath -Encoding ASCII
Ok $cmdPath

# 加入用户级 PATH（只加一次，且不碰系统级 PATH）
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$Bin*") {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$Bin", 'User')
    Ok "已把启动命令加入用户 PATH"
} else {
    Ok "启动命令已在 PATH 中"
}

# ── 5. 收尾 ──
Say ""
Say "─────────────────────────────"
Say "安装完成。"
Say ""
if ($env:JANCODE_API_KEY) {
    Ok "已检测到密钥环境变量"
} else {
    Warn "还没有设置 API 密钥。用之前先执行（把 sk-xxx 换成你的密钥）："
    Say '      setx JANCODE_API_KEY "sk-xxx"'
    Say "    执行后要关掉窗口重新打开才生效"
}
Say ""
Say "怎么用（关掉这个窗口，重新打开一个）："
Say "    jancode-agent --web              # 图形界面"
Say "    jancode-agent --list-providers   # 看有哪些模型供应商"
Say "    jancode-agent `"读一下这个项目`"     # 直接派活"
Say ""
