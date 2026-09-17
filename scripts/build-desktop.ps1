# 一键打包 Windows 版
#
# 为什么要有这个脚本：Windows 打包必须在 Windows 上做（PyInstaller 不能交叉编译），
# 手工装环境有十几步、每步都可能踩坑。这个脚本把「装 Python、建虚拟环境、装依赖、
# 跑测试、打包、压缩」串起来，出错会停下并告诉你卡在哪一步。
#
# 用法：双击仓库根目录的 一键打包-Windows.bat

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

# Windows PowerShell 5.1 默认按 ANSI 代码页读脚本，中文会乱码甚至吃掉引号。
# 脚本本身存成 UTF-8 BOM（见文件头），控制台再设一次编码，两头都对齐。
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

function Step($n, $text) { Write-Host ""; Write-Host "== $n. $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "失败：$text" -ForegroundColor Red; exit 1 }
function PyVersion($cand) {
    try {
        $code = "import sys;print(sys.version_info[0],sys.version_info[1])"
        return & cmd /c "$cand -c ""$code""" 2>$null
    } catch { return "" }
}

Step 1 "检查 Python 3.11"
$py = $null
foreach ($cand in @("py -3.11", "python3.11", "python")) {
    if ((PyVersion $cand) -match "^3 11") { $py = $cand; break }
}
if (-not $py) {
    Write-Host "没找到 Python 3.11，尝试用 winget 安装…" -ForegroundColor Yellow
    try { winget install -e --id Python.Python.3.11 --accept-source-agreements --accept-package-agreements } catch {}
    foreach ($cand in @("py -3.11", "python3.11")) {
        if ((PyVersion $cand) -match "^3 11") { $py = $cand; break }
    }
}
if (-not $py) { Fail "装不上 Python 3.11。请到 python.org 下载 3.11（安装时勾选 Add to PATH），然后重跑。" }
Write-Host "使用：$py" -ForegroundColor Green

Step 2 "创建虚拟环境 .venv-win"
if (-not (Test-Path ".venv-win")) { & cmd /c "$py -m venv .venv-win" }
$vpy = ".venv-win\Scripts\python.exe"
if (-not (Test-Path $vpy)) { Fail "虚拟环境没建起来" }

Step 3 "安装依赖"
# 每条都要单独检查退出码：之前三条连在一起只看了最后一条，
# 前面的失败会被放过，结果拖到跑测试时才报「找不到 pytest」。
& $vpy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Fail "升级 pip 失败（多半是网络）" }

& $vpy -m pip install -e .
if ($LASTEXITCODE -ne 0) { Fail "安装项目本体失败。若是网络问题，可试：& $vpy -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple" }

# pytest 是开发依赖，不在项目依赖里，必须单独装
& $vpy -m pip install pywebview pyinstaller pytest
if ($LASTEXITCODE -ne 0) { Fail "安装依赖失败。若是网络问题，可试：& $vpy -m pip install pywebview pyinstaller pytest -i https://pypi.tuna.tsinghua.edu.cn/simple" }

Step 4 "跑测试"
& $vpy -m pytest -q
if ($LASTEXITCODE -ne 0) { Fail "测试没过，先别打包" }

Step 5 "打包"
& $vpy -m PyInstaller packaging\jancode-desktop.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Fail "打包失败（看上面的报错）" }

Step 6 "压缩产物"
# onefile 模式出来的是单个 exe，onedir 模式才是目录。
# 之前只找目录，onefile 的产物明明打好了却报「没找到」。
$target = $null
$dir = Get-ChildItem dist -Directory | Where-Object { $_.Name -like "*JanCode*" } | Select-Object -First 1
if ($dir) {
    $target = $dir.FullName
    Write-Host "打包目录：$target"
} else {
    $exe = Get-ChildItem dist -File -Filter "*.exe" | Select-Object -First 1
    if ($exe) {
        $target = $exe.FullName
        Write-Host ("打包文件：" + $target + "（" + [math]::Round($exe.Length / 1MB, 1) + " MB）") -ForegroundColor Green
    }
}
if (-not $target) { Fail "dist 里既没有目录也没有 exe，看看上面 PyInstaller 的报错" }

$zip = "dist\JanCode-Agent-windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $target -DestinationPath $zip -Force
Write-Host ("压缩包：" + $zip + "（" + [math]::Round((Get-Item $zip).Length / 1MB, 1) + " MB）") -ForegroundColor Green

Write-Host ""
Write-Host "完成！产物：$zip" -ForegroundColor Green
Write-Host "把控制台最后 20 行发我，就能确认 Windows 版是不是真能跑。" -ForegroundColor Green
